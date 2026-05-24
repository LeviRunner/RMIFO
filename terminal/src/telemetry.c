#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <math.h>
#include <time.h>
#include "telemetry.h"
#include "db.h"
#include "display.h"

/* ── sparkline (1 row of blocks) ─────────────────────────────────────────── */

static void sparkline(float *vals, int n, int width, float *out_min, float *out_max, float *out_avg)
{
    if (n <= 0) { printf(C_DIM "(sem dados)" C_RESET); return; }

    float mn = vals[0], mx = vals[0], sum = 0;
    for (int i = 0; i < n; i++) {
        if (vals[i] < mn) mn = vals[i];
        if (vals[i] > mx) mx = vals[i];
        sum += vals[i];
    }
    if (out_min) *out_min = mn;
    if (out_max) *out_max = mx;
    if (out_avg) *out_avg = sum / n;

    const char *blk[] = {"▁","▂","▃","▄","▅","▆","▇","█"};
    int w = n < width ? n : width;
    printf(C_BCYAN);
    for (int i = 0; i < w; i++) {
        int si = i * n / w;
        float v = vals[si];
        int level = (int)((v - mn) / (mx - mn + 0.0001f) * 7.0f);
        if (level < 0) level = 0;
        if (level > 7) level = 7;
        printf("%s", blk[level]);
    }
    printf(C_RESET);
}

/* ── synthetic series (fallback when table has no data) ──────────────────── */

static void show_synthetic(const char *cable_id, int length_km)
{
    printf(C_YELLOW "  (Sem amostras no banco — exibindo estimativa sintetica)\n" C_RESET);
    float base_rtt = fmaxf(12.0f, (float)length_km / 200.0f * 1.35f);
    printf(C_BOLD "  Latencia estimada:  " C_RESET C_BCYAN "%.1f ms" C_RESET " (baseada em %d km)\n",
           base_rtt, length_km);
    printf(C_BOLD "  Jitter estimado:    " C_RESET "%.1f ms\n", base_rtt * 0.06f);
    float cap = fmaxf(2.0f, 12000.0f / fmaxf(1, (float)length_km));
    printf(C_BOLD "  Throughput aprox:   " C_RESET "%.0f Gbps\n", cap);
    (void)cable_id;
}

/* ── telemetry display ───────────────────────────────────────────────────── */

static void show_telemetry(PGconn *conn, const char *cable_id, int hours)
{
    /* Check table exists */
    PGresult *chk = PQexec(conn, "SELECT to_regclass('public.cable_telemetry_samples')");
    int has_table = PQresultStatus(chk) == PGRES_TUPLES_OK && !PQgetisnull(chk, 0, 0);
    PQclear(chk);

    /* Cable info */
    const char *p1[1] = {cable_id};
    PGresult *ci = PQexecParams(conn,
        "SELECT cable_name, length_km, capacity_gbps FROM cables WHERE cable_id = $1",
        1, NULL, p1, NULL, NULL, 0);

    if (PQresultStatus(ci) != PGRES_TUPLES_OK || PQntuples(ci) == 0) {
        printf(C_RED "  Cabo '%s' nao encontrado.\n" C_RESET, cable_id);
        PQclear(ci); press_enter(); return;
    }
    const char *cname = PQgetvalue(ci, 0, 0);
    int km = atoi(PQgetvalue(ci, 0, 1));

    printf(C_BOLD "\n  Cabo: " C_RESET C_BCYAN "%s" C_RESET " (%s / %d km)\n",
           cable_id, cname, km);

    if (!has_table) { show_synthetic(cable_id, km); PQclear(ci); press_enter(); return; }

    char hours_s[16];
    snprintf(hours_s, sizeof(hours_s), "%d", hours);
    const char *p2[2] = {cable_id, hours_s};

    PGresult *tr = PQexecParams(conn,
        "SELECT measured_at, latency_ms, jitter_ms, packet_loss_pct, "
        "throughput_mbps, availability_pct, confidence_score, estimated "
        "FROM cable_telemetry_samples "
        "WHERE cable_id = $1 AND measured_at >= NOW() - ($2::text||' hours')::interval "
        "ORDER BY measured_at ASC LIMIT 500",
        2, NULL, p2, NULL, NULL, 0);

    int n = (PQresultStatus(tr) == PGRES_TUPLES_OK) ? PQntuples(tr) : 0;

    if (n == 0) {
        show_synthetic(cable_id, km);
        PQclear(tr); PQclear(ci); press_enter(); return;
    }

    /* collect series */
    float *lat   = malloc((size_t)n * sizeof(float));
    float *jit   = malloc((size_t)n * sizeof(float));
    float *loss  = malloc((size_t)n * sizeof(float));
    float *tput  = malloc((size_t)n * sizeof(float));
    float *avail = malloc((size_t)n * sizeof(float));

    for (int i = 0; i < n; i++) {
        lat[i]   = atof(PQgetvalue(tr, i, 1));
        jit[i]   = atof(PQgetvalue(tr, i, 2));
        loss[i]  = atof(PQgetvalue(tr, i, 3));
        tput[i]  = atof(PQgetvalue(tr, i, 4));
        avail[i] = atof(PQgetvalue(tr, i, 5));
    }

    float mn, mx, avg;

    printf(C_BOLD "\n  Janela: " C_RESET "%dh  |  " C_BOLD "Amostras: " C_RESET "%d\n\n", hours, n);

    printf(C_BOLD "  Latencia (ms)    " C_RESET);
    sparkline(lat, n, 60, &mn, &mx, &avg);
    printf("  min=%.1f max=%.1f avg=%.1f\n", mn, mx, avg);

    printf(C_BOLD "  Jitter (ms)      " C_RESET);
    sparkline(jit, n, 60, &mn, &mx, &avg);
    printf("  avg=%.2f\n", avg);

    printf(C_BOLD "  Perda pct (%%)    " C_RESET);
    sparkline(loss, n, 60, &mn, &mx, &avg);
    printf("  avg=%.2f%%\n", avg);

    printf(C_BOLD "  Throughput (Mbps)" C_RESET);
    sparkline(tput, n, 60, &mn, &mx, &avg);
    printf("  avg=%.0f Mbps\n", avg);

    printf(C_BOLD "  Disponib. (%%)    " C_RESET);
    sparkline(avail, n, 60, &mn, &mx, &avg);
    printf("  avg=%.3f%%\n", avg);

    /* latest record */
    int last = n - 1;
    printf("\n");
    print_sub("Ultimo registro");
    printf("  " C_DIM "%s" C_RESET "\n", PQgetvalue(tr, last, 0));
    printf("  latencia=%.2f ms  jitter=%.2f ms  perda=%.2f%%  disponib=%.3f%%\n",
           atof(PQgetvalue(tr, last, 1)), atof(PQgetvalue(tr, last, 2)),
           atof(PQgetvalue(tr, last, 3)), atof(PQgetvalue(tr, last, 5)));
    printf("  throughput=%.0f Mbps  confianca=%.2f  estimado=%s\n",
           atof(PQgetvalue(tr, last, 4)),
           atof(PQgetvalue(tr, last, 6)),
           strcmp(PQgetvalue(tr, last, 7), "t") == 0 ? "sim" : "nao");

    free(lat); free(jit); free(loss); free(tput); free(avail);
    PQclear(tr); PQclear(ci);
    press_enter();
}

/* ── active alerts ───────────────────────────────────────────────────────── */

void show_alerts(PGconn *conn)
{
    print_header("Alertas Ativos de Telemetria");

    PGresult *chk = PQexec(conn, "SELECT to_regclass('public.telemetry_alerts')");
    int has = PQresultStatus(chk) == PGRES_TUPLES_OK && !PQgetisnull(chk, 0, 0);
    PQclear(chk);

    if (!has) {
        printf(C_DIM "  Tabela telemetry_alerts nao encontrada.\n" C_RESET);
        press_enter(); return;
    }

    PGresult *r = PQexec(conn,
        "SELECT ta.cable_id, c.cable_name, ta.metric, ta.severity, "
        "ta.current_value, ta.baseline_value, ta.detected_at, ta.description "
        "FROM telemetry_alerts ta "
        "JOIN cables c ON c.cable_id = ta.cable_id "
        "WHERE ta.resolved_at IS NULL "
        "ORDER BY ta.detected_at DESC LIMIT 30");

    if (PQresultStatus(r) != PGRES_TUPLES_OK) {
        printf(C_RED "  Erro: %s\n" C_RESET, PQerrorMessage(conn));
        PQclear(r); press_enter(); return;
    }

    int n = PQntuples(r);
    if (n == 0) {
        printf(C_BGREEN "  Nenhum alerta ativo. Tudo OK!\n" C_RESET);
        PQclear(r); press_enter(); return;
    }

    static const Col cols[] = {
        {"cable_id",26},{"metrica",14},{"sev.",8},{"atual",8},{"base",8},{"detectado",22}
    };
    tbl_header(cols, 6);
    for (int i = 0; i < n; i++) {
        const char *sev = PQgetvalue(r, i, 3);
        char sev_colored[32];
        if (strcmp(sev, "critical") == 0)
            snprintf(sev_colored, sizeof(sev_colored), "CRITICO");
        else
            snprintf(sev_colored, sizeof(sev_colored), "aviso");

        const char *v[6] = {
            PQgetvalue(r, i, 0), PQgetvalue(r, i, 2),
            sev_colored, PQgetvalue(r, i, 4),
            PQgetvalue(r, i, 5), PQgetvalue(r, i, 6)
        };
        tbl_row(v, cols, 6);
    }
    tbl_footer(cols, 6);
    printf(C_BOLD C_RED "  %d alerta(s) ativo(s).\n" C_RESET, n);
    PQclear(r);
    press_enter();
}

/* ── telemetry menu ──────────────────────────────────────────────────────── */

void menu_telemetry(PGconn *conn)
{
    while (1) {
        print_header("Telemetria Avancada");
        printf("  [1] Ver telemetria de um cabo\n");
        printf("  [2] Alertas ativos\n");
        printf("  [0] Voltar\n");

        int ch = read_choice(0, 2);
        if (ch == 0) return;

        if (ch == 2) { show_alerts(conn); continue; }

        /* ch == 1 */
        char cable_id[256];
        read_line(cable_id, sizeof(cable_id), "cable_id");
        if (!cable_id[0]) continue;

        printf("  Janela de tempo:\n");
        printf("  [1] 6 horas   [2] 24 horas (padrao)   [3] 3 dias   [4] 7 dias   [5] 30 dias\n");
        int hw = read_choice(1, 5);
        int hours[] = {6, 24, 72, 168, 720};

        print_header("Telemetria");
        show_telemetry(conn, cable_id, hours[hw - 1]);
    }
}
