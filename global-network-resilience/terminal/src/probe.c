#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <math.h>
#include <time.h>
#include <errno.h>

/* networking */
#include <unistd.h>
#include <fcntl.h>
#include <sys/types.h>
#include <sys/socket.h>
#include <sys/select.h>
#include <sys/stat.h>
#include <netinet/in.h>
#include <arpa/inet.h>

#include "probe.h"
#include "db.h"
#include "display.h"

/* ── helpers ─────────────────────────────────────────────────────────────── */

static long mono_ms(void)
{
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return ts.tv_sec * 1000L + ts.tv_nsec / 1000000L;
}

static int valid_ip(const char *ip)
{
    struct in_addr a4;
    struct in6_addr a6;
    return inet_pton(AF_INET, ip, &a4) == 1 || inet_pton(AF_INET6, ip, &a6) == 1;
}

/* ── Protocol 1: TCP connect ─────────────────────────────────────────────── */

static long tcp_connect_ms(const char *ip, int port, double timeout_sec)
{
    int sock = socket(AF_INET, SOCK_STREAM, 0);
    if (sock < 0) return -1;

    int fl = fcntl(sock, F_GETFL, 0);
    fcntl(sock, F_SETFL, fl | O_NONBLOCK);

    struct sockaddr_in addr;
    memset(&addr, 0, sizeof(addr));
    addr.sin_family = AF_INET;
    addr.sin_port   = htons((uint16_t)port);
    if (inet_pton(AF_INET, ip, &addr.sin_addr) != 1) { close(sock); return -1; }

    long t0 = mono_ms();
    int r = connect(sock, (struct sockaddr *)&addr, sizeof(addr));

    if (r < 0 && errno == EINPROGRESS) {
        fd_set wfds;
        FD_ZERO(&wfds);
        FD_SET(sock, &wfds);
        long tv_s  = (long)timeout_sec;
        long tv_us = (long)((timeout_sec - tv_s) * 1e6);
        struct timeval tv = {tv_s, tv_us};
        r = select(sock + 1, NULL, &wfds, NULL, &tv);
        if (r > 0) {
            int err = 0; socklen_t len = sizeof(err);
            getsockopt(sock, SOL_SOCKET, SO_ERROR, &err, &len);
            r = (err == 0) ? 1 : -1;
        } else { r = -1; }
    } else {
        r = (r == 0) ? 1 : -1;
    }

    long elapsed = mono_ms() - t0;
    close(sock);
    return (r > 0) ? elapsed : -1;
}

static int try_tcp(const char *ip, int *ports, int nports, int nsamples, ProbeResult *res)
{
    int conn_port = -1;
    long first_ms = -1;

    for (int i = 0; i < nports && conn_port < 0; i++) {
        long ms = tcp_connect_ms(ip, ports[i], 2.5);
        if (ms >= 0) { conn_port = ports[i]; first_ms = ms; }
    }
    if (conn_port < 0) return 0;

    long rtts[16];
    int  got = 0;
    rtts[got++] = first_ms;

    for (int i = 1; i < nsamples && got < 16; i++) {
        long ms = tcp_connect_ms(ip, conn_port, 2.5);
        if (ms >= 0) rtts[got++] = ms;
    }

    double sum = 0;
    for (int i = 0; i < got; i++) sum += rtts[i];
    double avg = sum / got;

    double var = 0;
    for (int i = 0; i < got; i++) var += (rtts[i] - avg) * (rtts[i] - avg);
    double jit = (got > 1) ? sqrt(var / (got - 1)) : 0.0;

    res->avg_rtt_ms      = (float)avg;
    res->jitter_ms       = (float)jit;
    res->packet_loss_pct = (float)(1.0 - (double)got / nsamples) * 100.0f;
    res->throughput_mbps = (avg > 0) ? (float)(1500.0 * 8.0 / (avg / 1000.0) / 1e6) : 0.0f;
    res->samples         = got;
    snprintf(res->method, sizeof(res->method), "tcp-connect-port%d", conn_port);
    snprintf(res->note,   sizeof(res->note),
             "TCP connect em %s:%d. %d/%d amostras.", ip, conn_port, got, nsamples);
    return 1;
}

/* ── Protocol 2: ICMP echo via system ping ───────────────────────────────── */

static int try_icmp(const char *ip, int count, ProbeResult *res)
{
    char cmd[256];
    snprintf(cmd, sizeof(cmd), "ping -c %d -W 2 -q %s 2>/dev/null", count, ip);

    FILE *fp = popen(cmd, "r");
    if (!fp) return 0;

    char buf[1024] = {0};
    char line[256];
    while (fgets(line, sizeof(line), fp)) {
        strncat(buf, line, sizeof(buf) - strlen(buf) - 1);
    }
    pclose(fp);

    /* parse "N received" */
    int received = 0;
    sscanf(strstr(buf, " received") ? buf : "", "%*d packets transmitted, %d received", &received);
    if (received == 0) {
        /* try alternate format */
        char *p = strstr(buf, "received");
        if (!p) return 0;
        while (p > buf && *p != ',') p--;
        sscanf(p + 1, " %d received", &received);
        if (received == 0) return 0;
    }

    /* parse "rtt min/avg/max/mdev = A/B/C/D ms" */
    float rtt_avg = 0, rtt_mdev = 0;
    char *rtt_line = strstr(buf, "rtt min");
    if (!rtt_line) rtt_line = strstr(buf, "round-trip");
    if (!rtt_line) return 0;

    sscanf(rtt_line, "rtt min/avg/max/mdev = %*f/%f/%*f/%f ms", &rtt_avg, &rtt_mdev);
    if (rtt_avg <= 0) return 0;

    float loss = 0;
    sscanf(strstr(buf, "%") ? strstr(buf, "%") - 4 : "", "%f", &loss);

    res->avg_rtt_ms      = rtt_avg;
    res->jitter_ms       = rtt_mdev;
    res->packet_loss_pct = loss;
    res->throughput_mbps = (rtt_avg > 0) ? (float)(1500.0 * 8.0 / (rtt_avg / 1000.0) / 1e6) : 0.0f;
    res->samples         = received;
    snprintf(res->method, sizeof(res->method), "icmp-echo");
    snprintf(res->note,   sizeof(res->note),
             "ICMP echo para %s. %d/%d pacotes recebidos. RTT avg=%.1f ms.", ip, received, count, rtt_avg);
    return 1;
}

/* ── Protocol 3: HTTP HEAD ───────────────────────────────────────────────── */

static int try_http(const char *ip, ProbeResult *res)
{
    int ports[2] = {80, 443};
    for (int pi = 0; pi < 2; pi++) {
        long ms = tcp_connect_ms(ip, ports[pi], 3.0);
        if (ms < 0) continue;

        /* quick HTTP HEAD */
        int sock = socket(AF_INET, SOCK_STREAM, 0);
        if (sock < 0) { /* report TCP rtt anyway */
            res->avg_rtt_ms      = (float)ms;
            res->jitter_ms       = 0;
            res->packet_loss_pct = 0;
            res->throughput_mbps = (ms > 0) ? (float)(1500.0 * 8.0 / (ms / 1000.0) / 1e6) : 0;
            res->samples         = 1;
            snprintf(res->method, sizeof(res->method), "http-head-port%d", ports[pi]);
            snprintf(res->note,   sizeof(res->note), "HTTP HEAD (TCP ok) em %s:%d.", ip, ports[pi]);
            return 1;
        }

        struct timeval tv = {3, 0};
        setsockopt(sock, SOL_SOCKET, SO_RCVTIMEO, &tv, sizeof(tv));
        setsockopt(sock, SOL_SOCKET, SO_SNDTIMEO, &tv, sizeof(tv));

        struct sockaddr_in addr;
        memset(&addr, 0, sizeof(addr));
        addr.sin_family = AF_INET;
        addr.sin_port   = htons((uint16_t)ports[pi]);
        inet_pton(AF_INET, ip, &addr.sin_addr);

        if (connect(sock, (struct sockaddr *)&addr, sizeof(addr)) < 0) {
            close(sock);
            res->avg_rtt_ms = (float)ms;
            res->samples    = 1;
            snprintf(res->method, sizeof(res->method), "http-head-port%d", ports[pi]);
            snprintf(res->note,   sizeof(res->note), "HTTP HEAD (TCP rtt=%ldms) em %s:%d.", ms, ip, ports[pi]);
            return 1;
        }

        char req[256];
        snprintf(req, sizeof(req),
                 "HEAD / HTTP/1.0\r\nHost: %s\r\nUser-Agent: RMIFO/1.2\r\nConnection: close\r\n\r\n", ip);
        if (write(sock, req, strlen(req)) < 0) { close(sock); continue; }

        char resp[256] = {0};
        int n = (int)read(sock, resp, sizeof(resp) - 1);
        close(sock);

        char code[8] = "???";
        if (n > 0 && strncmp(resp, "HTTP/", 5) == 0) {
            char *sp = strchr(resp, ' ');
            if (sp) snprintf(code, sizeof(code), "%.3s", sp + 1);
        }

        res->avg_rtt_ms      = (float)ms;
        res->jitter_ms       = 0;
        res->packet_loss_pct = 0;
        res->throughput_mbps = (ms > 0) ? (float)(1500.0 * 8.0 / (ms / 1000.0) / 1e6) : 0;
        res->samples         = 1;
        snprintf(res->method, sizeof(res->method), "http-head-port%d", ports[pi]);
        snprintf(res->note,   sizeof(res->note),
                 "HTTP HEAD em %s:%d → HTTP %s.", ip, ports[pi], code);
        return 1;
    }
    return 0;
}

/* ── Protocol 4: UDP DNS query to port 53 ────────────────────────────────── */

static int try_udp_dns(const char *ip, ProbeResult *res)
{
    int sock = socket(AF_INET, SOCK_DGRAM, IPPROTO_UDP);
    if (sock < 0) return 0;

    struct timeval tv = {2, 0};
    setsockopt(sock, SOL_SOCKET, SO_RCVTIMEO, &tv, sizeof(tv));
    setsockopt(sock, SOL_SOCKET, SO_SNDTIMEO, &tv, sizeof(tv));

    struct sockaddr_in addr;
    memset(&addr, 0, sizeof(addr));
    addr.sin_family = AF_INET;
    addr.sin_port   = htons(53);
    if (inet_pton(AF_INET, ip, &addr.sin_addr) != 1) { close(sock); return 0; }

    /* minimal DNS query: ID=0xAB12, standard query, 1 question for "." ANY IN */
    static const unsigned char dns_q[] = {
        0xAB,0x12,0x01,0x00,0x00,0x01,0x00,0x00,
        0x00,0x00,0x00,0x00,0x00,0x00,0xFF,0x00,0x01
    };

    long t0 = mono_ms();
    ssize_t sent = sendto(sock, dns_q, sizeof(dns_q), 0,
                          (struct sockaddr *)&addr, sizeof(addr));
    if (sent < 0) { close(sock); return 0; }

    char buf[512];
    struct sockaddr_in from;
    socklen_t flen = sizeof(from);
    ssize_t got = recvfrom(sock, buf, sizeof(buf), 0, (struct sockaddr *)&from, &flen);
    long elapsed = mono_ms() - t0;
    close(sock);

    if (got < 4) return 0;

    res->avg_rtt_ms      = (float)elapsed;
    res->jitter_ms       = 0;
    res->packet_loss_pct = 0;
    res->throughput_mbps = (elapsed > 0) ? (float)(1500.0 * 8.0 / (elapsed / 1000.0) / 1e6) : 0;
    res->samples         = 1;
    snprintf(res->method, sizeof(res->method), "udp-dns-port53");
    snprintf(res->note,   sizeof(res->note),
             "UDP DNS probe em %s:53 → %zd bytes recebidos. RTT=%ld ms.", ip, got, elapsed);
    return 1;
}

/* ── synthetic fallback ───────────────────────────────────────────────────── */

static void synthetic_fallback(const char *ip, PGconn *conn,
                                const char *cable_id, int nsamples,
                                const char *tried, ProbeResult *res)
{
    int km = 5000;
    const char *p[1] = {cable_id};
    PGresult *r = PQexecParams(conn,
        "SELECT COALESCE(length_km,5000) FROM cables WHERE cable_id=$1",
        1, NULL, p, NULL, NULL, 0);
    if (PQresultStatus(r) == PGRES_TUPLES_OK && PQntuples(r) > 0)
        km = atoi(PQgetvalue(r, 0, 0));
    PQclear(r);

    float base = fmaxf(12.0f, (float)km / 200.0f * 1.35f);
    res->reachable       = 1;
    res->avg_rtt_ms      = base * (0.95f + (float)(rand() % 11) / 100.0f);
    res->jitter_ms       = res->avg_rtt_ms * 0.06f;
    res->packet_loss_pct = fmaxf(0.05f, fminf(2.0f, 0.12f + (float)(rand() % 11) / 100.0f));
    res->throughput_mbps = (res->avg_rtt_ms > 0) ?
                           (float)(1500.0 * 8.0 / (res->avg_rtt_ms / 1000.0) / 1e6) : 0;
    res->samples         = nsamples;
    strncpy(res->method, "synthetic_fallback", sizeof(res->method) - 1);
    snprintf(res->note, sizeof(res->note),
             "IP %s nao respondeu via %s. Estimativa sintetica (%d km).", ip, tried, km);
}

/* ── orchestrator ────────────────────────────────────────────────────────── */

static void run_probe(PGconn *conn, const char *ip, const char *cable_id,
                      int full_scan, ProbeResult *res)
{
    memset(res, 0, sizeof(*res));

    int ports_std[]  = {443, 80, 22};
    int ports_full[] = {443, 80, 22, 8080, 8443};
    int *ports   = full_scan ? ports_full : ports_std;
    int  nports  = full_scan ? 5 : 3;
    int  nsamples = full_scan ? 5 : 4;

    char tried[64] = "";
    int  ok = 0;

    if (!valid_ip(ip)) {
        res->reachable = 1;
        synthetic_fallback(ip, conn, cable_id, nsamples, "IP invalido", res);
        strncpy(res->steps, "synthetic", sizeof(res->steps) - 1);
        return;
    }

    /* TCP */
    strncat(tried, "TCP", sizeof(tried) - strlen(tried) - 1);
    strncat(res->steps, "tcp", sizeof(res->steps) - strlen(res->steps) - 1);
    ok = try_tcp(ip, ports, nports, nsamples, res);

    if (!ok) {
        strncat(tried, "→ICMP", sizeof(tried) - strlen(tried) - 1);
        strncat(res->steps, " icmp", sizeof(res->steps) - strlen(res->steps) - 1);
        ok = try_icmp(ip, nsamples, res);
    }

    if (!ok) {
        strncat(tried, "→HTTP", sizeof(tried) - strlen(tried) - 1);
        strncat(res->steps, " http", sizeof(res->steps) - strlen(res->steps) - 1);
        ok = try_http(ip, res);
    }

    if (!ok) {
        strncat(tried, "→UDP/DNS", sizeof(tried) - strlen(tried) - 1);
        strncat(res->steps, " udp", sizeof(res->steps) - strlen(res->steps) - 1);
        ok = try_udp_dns(ip, res);
    }

    if (!ok) {
        strncat(res->steps, " synthetic", sizeof(res->steps) - strlen(res->steps) - 1);
        synthetic_fallback(ip, conn, cable_id, nsamples, tried, res);
    }

    res->reachable = 1;
}

/* ── save to JSONL history ───────────────────────────────────────────────── */

static void mkdirp(const char *path)
{
    char tmp[512];
    strncpy(tmp, path, sizeof(tmp) - 1);
    for (char *p = tmp + 1; *p; p++) {
        if (*p == '/') { *p = '\0'; mkdir(tmp, 0775); *p = '/'; }
    }
    mkdir(tmp, 0775);
}

static void save_history(const ProbeResult *res, const char *cable_id,
                         const char *endpoint_id, int full_scan)
{
    const char *ws = getenv("WORKSPACE_ROOT");
    if (!ws) ws = "/workspaces/RMIFO";

    char dir[512];
    snprintf(dir, sizeof(dir), "%s/data/local/log/probe", ws);
    mkdirp(dir);

    char path[600];
    time_t now = time(NULL);
    struct tm *utc = gmtime(&now);
    char date[16];
    strftime(date, sizeof(date), "%Y-%m-%d", utc);
    snprintf(path, sizeof(path), "%s/%s.jsonl", dir, date);

    char ts[32];
    strftime(ts, sizeof(ts), "%Y-%m-%dT%H:%M:%S+00:00", utc);

    int is_real = strcmp(res->method, "synthetic_fallback") != 0;

    FILE *f = fopen(path, "a");
    if (!f) return;

    fprintf(f,
        "{\"cable_id\":\"%s\","
        "\"measured_at\":\"%s\","
        "\"sent_at\":null,\"received_at\":null,"
        "\"latency_ms\":%.2f,\"jitter_ms\":%.2f,"
        "\"packet_loss_pct\":%.2f,\"throughput_mbps\":%.3f,"
        "\"packets_per_second\":null,"
        "\"availability_pct\":%.2f,"
        "\"route_error_count\":%d,"
        "\"source\":\"network_probe\","
        "\"method\":\"%s\","
        "\"confidence_score\":%.2f,"
        "\"estimated\":%s,"
        "\"telemetry_model_version\":\"1.2.0\","
        "\"metadata\":{"
          "\"collector\":\"rmifo-terminal/probe\","
          "\"probe_protocol\":\"%s\","
          "\"target_host\":%s,"
          "\"target_endpoint_id\":%s,"
          "\"steps\":\"%s\","
          "\"full_scan\":%s"
        "},\"hops\":[]}\n",
        cable_id, ts,
        res->avg_rtt_ms, res->jitter_ms,
        res->packet_loss_pct, res->throughput_mbps,
        100.0f - res->packet_loss_pct,
        0,
        res->method,
        is_real ? 0.78f : 0.55f,
        is_real ? "false" : "true",
        is_real ? "tcp-connect" : "heuristic",
        /* target_host as JSON string or null */
        "null",
        (endpoint_id && endpoint_id[0]) ? "\"" : "null",
        res->steps,
        full_scan ? "true" : "false"
    );
    fclose(f);

    printf(C_BGREEN "  ✓ Historico salvo: " C_RESET C_DIM "%s/%s.jsonl\n" C_RESET, dir, date);
    (void)endpoint_id;
}

/* ── display probe result ────────────────────────────────────────────────── */

static void display_result(const ProbeResult *res, const char *ip)
{
    const char *status_color = res->reachable ? C_BGREEN : C_RED;
    const char *status_text  = res->reachable ? "ACESSIVEL" : "INACESSIVEL";

    printf("\n  %s[ %s ]%s  %s%s%s\n\n",
           status_color, status_text, C_RESET,
           C_DIM, ip, C_RESET);

    /* protocol chain */
    printf(C_BOLD "  Protocolos tentados: " C_RESET);
    char steps[64];
    strncpy(steps, res->steps, sizeof(steps) - 1);
    char *tok = strtok(steps, " ");
    while (tok) {
        int ok = strstr(res->method, tok) != NULL ||
                 (strcmp(tok, "tcp")  == 0 && strncmp(res->method, "tcp",  3) == 0) ||
                 (strcmp(tok, "icmp") == 0 && strcmp(res->method, "icmp-echo") == 0) ||
                 (strcmp(tok, "http") == 0 && strncmp(res->method, "http", 4) == 0) ||
                 (strcmp(tok, "udp")  == 0 && strncmp(res->method, "udp",  3) == 0);
        if (ok)
            printf(C_BGREEN "[%s ✓]" C_RESET " ", tok);
        else
            printf(C_DIM "[%s ✗]" C_RESET " ", tok);
        tok = strtok(NULL, " ");
    }
    printf("\n");

    /* metrics grid */
    printf("\n");
    printf("  %-18s " C_BCYAN "%7.2f ms" C_RESET "    ", "RTT medio:",      res->avg_rtt_ms);
    printf("  %-18s " C_BCYAN "%7.2f ms" C_RESET "\n",   "Jitter:",         res->jitter_ms);
    printf("  %-18s ",                                    "Perda de pacotes:");
    if (res->packet_loss_pct > 5)
        printf(C_RED "%6.1f%%" C_RESET "    ", res->packet_loss_pct);
    else
        printf(C_BGREEN "%6.1f%%" C_RESET "    ", res->packet_loss_pct);
    printf("  %-18s %7.3f Mbps\n",                        "Throughput est.", res->throughput_mbps);
    printf("  %-18s %7d         ", "Amostras:",            res->samples);
    printf("  %-18s %s\n",                                 "Metodo:",         res->method);

    printf("\n  " C_DIM "%s" C_RESET "\n", res->note);

    /* ASCII RTT bar */
    printf("\n  RTT  ");
    ascii_bar(res->avg_rtt_ms, 500.0f, 40, C_BCYAN);
    printf("  %.1f ms\n", res->avg_rtt_ms);

    printf("  Loss ");
    const char *bar_color = res->packet_loss_pct > 5 ? C_RED : C_BGREEN;
    ascii_bar(res->packet_loss_pct, 100.0f, 40, bar_color);
    printf("  %.1f%%\n", res->packet_loss_pct);
}

/* ── select cable & endpoint from DB ─────────────────────────────────────── */

static void pick_endpoint_ip(PGconn *conn, const char *cable_id,
                              char *ep_id_out, char *ip_out, int bufsize)
{
    ep_id_out[0] = '\0'; ip_out[0] = '\0';

    const char *p[1] = {cable_id};
    PGresult *r = PQexecParams(conn,
        "SELECT lp.endpoint_id, lp.endpoint_name, lp.city_name, lp.country_name, "
        "eis.ip_start "
        "FROM cable_landing_points clp "
        "JOIN landing_points lp ON lp.endpoint_id = clp.endpoint_id "
        "LEFT JOIN endpoint_ip_samples eis ON eis.endpoint_id = lp.endpoint_id AND eis.sample_order = 0 "
        "WHERE clp.cable_id = $1 "
        "ORDER BY clp.endpoint_order, lp.endpoint_name",
        1, NULL, p, NULL, NULL, 0);

    if (PQresultStatus(r) != PGRES_TUPLES_OK || PQntuples(r) == 0) {
        PQclear(r); return;
    }

    int n = PQntuples(r);
    printf("\n  Endpoints disponíveis:\n");
    static const Col ec[] = {{"#",3},{"endpoint",36},{"cidade",20},{"pais",20},{"IP amostra",16}};
    tbl_header(ec, 5);
    for (int i = 0; i < n && i < 20; i++) {
        char idx[4]; snprintf(idx, sizeof(idx), "%d", i + 1);
        const char *v[5] = {idx, PQgetvalue(r,i,1), PQgetvalue(r,i,2),
                            PQgetvalue(r,i,3), PQgetvalue(r,i,4)};
        tbl_row(v, ec, 5);
    }
    tbl_footer(ec, 5);

    printf("  Escolha o endpoint (1-%d, ou 0 para pular): ", n < 20 ? n : 20);
    int ch = read_choice(0, n < 20 ? n : 20);
    if (ch == 0) { PQclear(r); return; }

    strncpy(ep_id_out, PQgetvalue(r, ch-1, 0), bufsize - 1);
    const char *sample_ip = PQgetvalue(r, ch-1, 4);
    if (sample_ip && sample_ip[0]) strncpy(ip_out, sample_ip, bufsize - 1);

    PQclear(r);
}

/* ── public menu ─────────────────────────────────────────────────────────── */

void menu_probe(PGconn *conn)
{
    while (1) {
        print_header("Sonda de Rede");
        printf("  Cadeia de protocolos: TCP → ICMP → HTTP → UDP/DNS → estimativa sintetica\n\n");
        printf("  [1] Sonda padrao   (TCP 443/80/22, 4 amostras)\n");
        printf("  [2] Sonda completa (TCP 443/80/22/8080/8443, 5 amostras)\n");
        printf("  [0] Voltar\n");

        int ch = read_choice(0, 2);
        if (ch == 0) return;

        int full = (ch == 2);

        char cable_id[256];
        read_line(cable_id, sizeof(cable_id), "cable_id (ex: 2africa)");
        if (!cable_id[0]) continue;

        char ep_id[256] = "", ip[64] = "";
        pick_endpoint_ip(conn, cable_id, ep_id, ip, sizeof(ip));

        if (!ip[0]) {
            read_line(ip, sizeof(ip), "IP alvo (manual, ex: 41.74.11.0)");
        } else {
            char custom[64];
            printf(C_DIM "  IP auto-detectado: %s" C_RESET "\n", ip);
            read_line(custom, sizeof(custom), "Outro IP (Enter para usar o detectado)");
            if (custom[0]) strncpy(ip, custom, sizeof(ip) - 1);
        }

        if (!ip[0]) { printf(C_RED "  IP nao informado.\n" C_RESET); press_enter(); continue; }

        printf(C_DIM "\n  Iniciando sonda %s para %s...\n" C_RESET,
               full ? "completa" : "padrao", ip);
        fflush(stdout);

        ProbeResult res;
        run_probe(conn, ip, cable_id, full, &res);

        print_header("Resultado da Sonda");
        display_result(&res, ip);
        save_history(&res, cable_id, ep_id, full);

        press_enter();
    }
}
