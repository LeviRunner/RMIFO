#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <ctype.h>
#include "search.h"
#include "db.h"
#include "display.h"

#define MAX_CABLES 6000

/* ── cable cache (sorted by cable_id, loaded once) ──────────────────────── */

typedef struct {
    char cable_id[256];
    char cable_name[256];
    char owners[256];
    char capacity_gbps[24];
    char length_km[16];
    char rfs_year[12];
    char is_operational[8];
    char landing_points_count[8];
    char suppliers[128];
} Cable;

static Cable cache[MAX_CABLES];
static int   cache_n = 0;

static void load_cache(PGconn *conn)
{
    if (cache_n > 0) return;

    PGresult *r = PQexec(conn,
        "SELECT cable_id, cable_name, owners, capacity_gbps, length_km, "
        "rfs_year, is_operational, landing_points_count, suppliers "
        "FROM cables ORDER BY cable_id");

    if (PQresultStatus(r) != PGRES_TUPLES_OK) {
        fprintf(stderr, "Erro ao carregar cache: %s\n", PQerrorMessage(conn));
        PQclear(r); return;
    }

    int n = PQntuples(r);
    if (n > MAX_CABLES) n = MAX_CABLES;
    cache_n = n;

    for (int i = 0; i < n; i++) {
#define CP(field, col, sz) strncpy(cache[i].field, PQgetvalue(r, i, col), sz - 1)
        CP(cable_id,              0, 256);
        CP(cable_name,            1, 256);
        CP(owners,                2, 256);
        CP(capacity_gbps,         3, 24);
        CP(length_km,             4, 16);
        CP(rfs_year,              5, 12);
        CP(is_operational,        6, 8);
        CP(landing_points_count,  7, 8);
        CP(suppliers,             8, 128);
#undef CP
    }
    PQclear(r);
}

/* ── cable table printer ─────────────────────────────────────────────────── */

static const Col CABLE_COLS[] = {
    {"cable_id", 32}, {"nome", 36}, {"ano", 6}, {"oper.", 5}, {"ep.", 4}, {"km", 7}
};
#define NCABLE_COLS 6

static void print_result(PGresult *r)
{
    int n = PQntuples(r);
    if (n == 0) { printf(C_DIM "  Sem resultados.\n" C_RESET); return; }

    tbl_header(CABLE_COLS, NCABLE_COLS);
    int shown = n > 60 ? 60 : n;
    for (int i = 0; i < shown; i++) {
        const char *op = strcmp(PQgetvalue(r, i, 6), "t") == 0 ? "sim" : "nao";
        const char *vals[NCABLE_COLS] = {
            PQgetvalue(r, i, 0), PQgetvalue(r, i, 1),
            PQgetvalue(r, i, 5), op,
            PQgetvalue(r, i, 7), PQgetvalue(r, i, 4)
        };
        tbl_row(vals, CABLE_COLS, NCABLE_COLS);
    }
    tbl_footer(CABLE_COLS, NCABLE_COLS);
    if (n > shown)
        printf(C_DIM "  %d resultado(s), exibindo %d.\n" C_RESET, n, shown);
    else
        printf(C_DIM "  %d resultado(s).\n" C_RESET, n);
}

/* ── generic param search ────────────────────────────────────────────────── */

static void do_search(PGconn *conn, const char *sql, const char *param)
{
    const char *p[1] = {param};
    PGresult *r = PQexecParams(conn, sql, 1, NULL, p, NULL, NULL, 0);
    if (PQresultStatus(r) != PGRES_TUPLES_OK) {
        printf(C_RED "  Erro: %s\n" C_RESET, PQerrorMessage(conn));
        PQclear(r); press_enter(); return;
    }
    print_result(r);
    PQclear(r);
    press_enter();
}

/* ── binary search visualization ─────────────────────────────────────────── */

static void str_lower(char *dst, const char *src, int size)
{
    int i;
    for (i = 0; i < size - 1 && src[i]; i++) dst[i] = (char)tolower((unsigned char)src[i]);
    dst[i] = '\0';
}

static void binary_search_visual(PGconn *conn)
{
    print_header("Busca Binaria por cable_id");
    load_cache(conn);

    if (cache_n == 0) {
        printf(C_RED "  Cache vazio.\n" C_RESET); press_enter(); return;
    }

    char target_raw[256], target[256];
    read_line(target_raw, sizeof(target_raw), "cable_id alvo");
    if (!target_raw[0]) return;
    str_lower(target, target_raw, sizeof(target));

    printf("\n" C_BOLD "Catalogo: %d cabos ordenados. [Enter]=proximo passo  [s+Enter]=pular ao fim\n" C_RESET, cache_n);
    printf(C_DIM "  Verde = encontrado  Ciano = midpoint  Amarelo = intervalo ativo\n\n" C_RESET);

    int lo = 0, hi = cache_n - 1, step = 0, found = -1;

    while (lo <= hi) {
        step++;
        int mid = (lo + hi) / 2;
        char mid_low[256];
        str_lower(mid_low, cache[mid].cable_id, sizeof(mid_low));
        int cmp = strcmp(mid_low, target);

        printf(C_BOLD "Passo %-3d" C_RESET " lo=%-4d hi=%-4d mid=%-4d  ", step, lo, hi, mid);

        if (cmp == 0) {
            printf(C_BGREEN "MID=\"%s\" = alvo → ENCONTRADO!" C_RESET "\n", cache[mid].cable_id);
            found = mid;
            break;
        } else if (cmp < 0) {
            printf(C_CYAN "MID=\"%s\"" C_RESET " < alvo → " C_BYELLOW "metade direita" C_RESET "\n", cache[mid].cable_id);
            lo = mid + 1;
        } else {
            printf(C_CYAN "MID=\"%s\"" C_RESET " > alvo → " C_BYELLOW "metade esquerda" C_RESET "\n", cache[mid].cable_id);
            hi = mid - 1;
        }

        char buf[8];
        if (fgets(buf, sizeof(buf), stdin) && (buf[0] == 's' || buf[0] == 'S')) break;
    }

    if (lo > hi && found < 0)
        printf("\n" C_RED "✗ Nao encontrado apos %d passo(s). Intervalo vazio (lo=%d > hi=%d).\n" C_RESET, step, lo, hi);

    if (found >= 0) {
        printf("\n" C_BGREEN "✓ Cabo encontrado em %d passo(s)\n" C_RESET, step);
        printf(C_BOLD "  %-16s" C_RESET " %s\n", "cable_id:",  cache[found].cable_id);
        printf(C_BOLD "  %-16s" C_RESET " %s\n", "nome:",      cache[found].cable_name);
        printf(C_BOLD "  %-16s" C_RESET " %s km\n", "comprimento:", cache[found].length_km);
        printf(C_BOLD "  %-16s" C_RESET " %s\n", "ano RFS:",   cache[found].rfs_year);
    }

    press_enter();
}

/* ── cable details ──────────────────────────────────────────────────────── */

void show_cable_details(PGconn *conn, const char *cable_id)
{
    print_header("Detalhes do Cabo");

    const char *p[1] = {cable_id};

    PGresult *r = PQexecParams(conn,
        "SELECT c.cable_id, c.cable_name, c.owners, c.capacity_gbps, c.length_km, "
        "c.rfs_year, c.is_operational, c.landing_points_count, c.suppliers, "
        "cm.public_url, cm.notes, cm.is_planned "
        "FROM cables c LEFT JOIN cable_metadata cm ON cm.cable_id = c.cable_id "
        "WHERE c.cable_id = $1",
        1, NULL, p, NULL, NULL, 0);

    if (PQresultStatus(r) != PGRES_TUPLES_OK || PQntuples(r) == 0) {
        printf(C_RED "  Cabo '%s' nao encontrado.\n" C_RESET, cable_id);
        PQclear(r); press_enter(); return;
    }

#define FIELD(label, col) printf(C_BOLD "  %-16s" C_RESET "%s\n", label, PQgetvalue(r, 0, col))
    FIELD("cable_id:",      0);
    FIELD("nome:",          1);
    FIELD("proprietarios:", 2);
    printf(C_BOLD "  %-16s" C_RESET "%s Gbps\n", "capacidade:", PQgetvalue(r, 0, 3));
    printf(C_BOLD "  %-16s" C_RESET "%s km\n",   "comprimento:", PQgetvalue(r, 0, 4));
    FIELD("ano RFS:",       5);
    printf(C_BOLD "  %-16s" C_RESET "%s\n", "operacional:",
           strcmp(PQgetvalue(r, 0, 6), "t") == 0 ? C_BGREEN "sim" C_RESET : C_RED "nao" C_RESET);
    printf(C_BOLD "  %-16s" C_RESET "%s endpoints\n", "landing pts:", PQgetvalue(r, 0, 7));
    FIELD("fornecedores:",  8);
    if (!PQgetisnull(r, 0, 9))  FIELD("URL publica:", 9);
    if (!PQgetisnull(r, 0, 10) && PQgetvalue(r, 0, 10)[0]) FIELD("notas:", 10);
#undef FIELD

    PQclear(r);

    /* endpoints */
    printf("\n");
    print_sub("Endpoints de ancoragem");

    PGresult *ep = PQexecParams(conn,
        "SELECT clp.endpoint_order, lp.endpoint_name, lp.city_name, "
        "lp.country_name, lp.latitude, lp.longitude "
        "FROM cable_landing_points clp "
        "JOIN landing_points lp ON lp.endpoint_id = clp.endpoint_id "
        "WHERE clp.cable_id = $1 ORDER BY clp.endpoint_order, lp.endpoint_name",
        1, NULL, p, NULL, NULL, 0);

    if (PQresultStatus(ep) == PGRES_TUPLES_OK && PQntuples(ep) > 0) {
        static const Col ep_cols[] = {{"#",3},{"endpoint",38},{"cidade",22},{"pais",20},{"lat",9},{"lon",10}};
        int ne = PQntuples(ep);
        tbl_header(ep_cols, 6);
        for (int i = 0; i < ne; i++) {
            const char *v[6] = {
                PQgetvalue(ep, i, 0), PQgetvalue(ep, i, 1),
                PQgetvalue(ep, i, 2), PQgetvalue(ep, i, 3),
                PQgetvalue(ep, i, 4), PQgetvalue(ep, i, 5)
            };
            tbl_row(v, ep_cols, 6);
        }
        tbl_footer(ep_cols, 6);
        printf(C_DIM "  %d endpoint(s).\n" C_RESET, ne);
    } else {
        printf(C_DIM "  Nenhum endpoint cadastrado.\n" C_RESET);
    }
    PQclear(ep);

    press_enter();
}

/* ── main search menu ────────────────────────────────────────────────────── */

#define SEL_SQL "SELECT cable_id, cable_name, owners, capacity_gbps, length_km, " \
                "rfs_year, is_operational, landing_points_count, suppliers FROM cables "

void menu_search(PGconn *conn)
{
    load_cache(conn);

    while (1) {
        print_header("Busca de Cabos");
        printf(C_DIM "  Catalogo: %d cabos\n\n" C_RESET, cache_n);
        printf("  [1] Busca binaria por cable_id\n");
        printf("  [2] Busca por nome (cable_name)\n");
        printf("  [3] Busca por proprietarios (owners)\n");
        printf("  [4] Busca por ano RFS (rfs_year)\n");
        printf("  [5] Busca por status operacional (true/false)\n");
        printf("  [6] Busca por nº de landing points\n");
        printf("  [7] Busca por fornecedores (suppliers)\n");
        printf("  [8] Busca por nome de endpoint\n");
        printf("  [9] Busca por pais do endpoint\n");
        printf("  [0] Voltar\n");

        int ch = read_choice(0, 9);
        if (ch == 0) return;

        char q[512];
        switch (ch) {
        case 1:
            binary_search_visual(conn);
            break;
        case 2:
            read_line(q, sizeof(q), "Nome (parcial)");
            print_header("Resultados: cable_name");
            do_search(conn, SEL_SQL "WHERE cable_name ILIKE '%'||$1||'%' ORDER BY cable_name", q);
            break;
        case 3:
            read_line(q, sizeof(q), "Proprietario (parcial)");
            print_header("Resultados: owners");
            do_search(conn, SEL_SQL "WHERE owners ILIKE '%'||$1||'%' ORDER BY cable_name", q);
            break;
        case 4:
            read_line(q, sizeof(q), "Ano RFS (ex: 2024)");
            print_header("Resultados: rfs_year");
            do_search(conn, SEL_SQL "WHERE rfs_year = $1::integer ORDER BY cable_name", q);
            break;
        case 5:
            read_line(q, sizeof(q), "Operacional? (true / false)");
            print_header("Resultados: is_operational");
            do_search(conn, SEL_SQL "WHERE is_operational = $1::boolean ORDER BY cable_name", q);
            break;
        case 6:
            read_line(q, sizeof(q), "Numero de landing points");
            print_header("Resultados: landing_points_count");
            do_search(conn, SEL_SQL "WHERE landing_points_count = $1::integer ORDER BY cable_name", q);
            break;
        case 7:
            read_line(q, sizeof(q), "Fornecedor (parcial)");
            print_header("Resultados: suppliers");
            do_search(conn, SEL_SQL "WHERE suppliers ILIKE '%'||$1||'%' ORDER BY cable_name", q);
            break;
        case 8:
            read_line(q, sizeof(q), "Nome ou cidade do endpoint (parcial)");
            print_header("Resultados: endpoint");
            do_search(conn,
                "SELECT DISTINCT c.cable_id, c.cable_name, c.owners, c.capacity_gbps, "
                "c.length_km, c.rfs_year, c.is_operational, c.landing_points_count, c.suppliers "
                "FROM cables c JOIN cable_landing_points clp ON clp.cable_id=c.cable_id "
                "JOIN landing_points lp ON lp.endpoint_id=clp.endpoint_id "
                "WHERE lp.endpoint_name ILIKE '%'||$1||'%' OR lp.city_name ILIKE '%'||$1||'%' "
                "ORDER BY c.cable_name", q);
            break;
        case 9:
            read_line(q, sizeof(q), "Pais (nome ou codigo, ex: Brazil / BR)");
            print_header("Resultados: pais do endpoint");
            do_search(conn,
                "SELECT DISTINCT c.cable_id, c.cable_name, c.owners, c.capacity_gbps, "
                "c.length_km, c.rfs_year, c.is_operational, c.landing_points_count, c.suppliers "
                "FROM cables c JOIN cable_landing_points clp ON clp.cable_id=c.cable_id "
                "JOIN landing_points lp ON lp.endpoint_id=clp.endpoint_id "
                "WHERE lp.country_name ILIKE '%'||$1||'%' OR lp.country_code ILIKE $1 "
                "ORDER BY c.cable_name", q);
            break;
        }
    }
}
