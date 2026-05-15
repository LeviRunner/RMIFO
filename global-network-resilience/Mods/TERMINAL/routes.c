#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <strings.h>
#include <math.h>
#include "routes.h"
#include "db.h"
#include "display.h"

/* ── graph data structures ───────────────────────────────────────────────── */

#define MAX_COUNTRIES 500
#define MAX_CABLES    2000
#define MAX_DEPTH     8
#define MAX_ROUTES    3
#define MAX_FOUND     120   /* DFS collects up to this many paths, keeps best 3 */

typedef struct {
    int   to;
    int   cable_idx;
    float weight;
    int   length_km;
} Edge;

typedef struct {
    Edge *edges;
    int   n;
    int   cap;
} Adj;

typedef struct {
    char name[128];
    Adj  adj;
} Country;

typedef struct {
    char id[64];
    char name[128];
} CableInfo;

typedef struct {
    Country  country[MAX_COUNTRIES];
    int      nc;
    CableInfo cable[MAX_CABLES];
    int      nca;
} Graph;

typedef struct {
    float score;
    int   dist_km;
    int   nhops;
    int   stops[MAX_DEPTH + 1];   /* country indices */
    int   cabs[MAX_DEPTH];        /* cable indices   */
} Route;

/* ── graph helpers ───────────────────────────────────────────────────────── */

static int country_idx(Graph *g, const char *name)
{
    for (int i = 0; i < g->nc; i++)
        if (strcmp(g->country[i].name, name) == 0) return i;
    if (g->nc >= MAX_COUNTRIES) return -1;
    strncpy(g->country[g->nc].name, name, 127);
    g->country[g->nc].adj.edges = NULL;
    g->country[g->nc].adj.n    = 0;
    g->country[g->nc].adj.cap  = 0;
    return g->nc++;
}

static int cable_idx(Graph *g, const char *id, const char *name)
{
    for (int i = 0; i < g->nca; i++)
        if (strcmp(g->cable[i].id, id) == 0) return i;
    if (g->nca >= MAX_CABLES) return -1;
    strncpy(g->cable[g->nca].id,   id,   63);
    strncpy(g->cable[g->nca].name, name, 127);
    return g->nca++;
}

static void add_edge(Graph *g, int from, int to, int ci, float w, int km)
{
    Adj *a = &g->country[from].adj;
    if (a->n >= a->cap) {
        a->cap = a->cap ? a->cap * 2 : 8;
        a->edges = realloc(a->edges, (size_t)a->cap * sizeof(Edge));
    }
    a->edges[a->n++] = (Edge){to, ci, w, km};
}

static void free_graph(Graph *g)
{
    for (int i = 0; i < g->nc; i++) free(g->country[i].adj.edges);
}

/* ── build graph from DB ─────────────────────────────────────────────────── */

static void build_graph(PGconn *conn, Graph *g)
{
    memset(g, 0, sizeof(*g));

    PGresult *r = PQexec(conn,
        "SELECT clp.cable_id, c.cable_name, COALESCE(c.length_km,0), "
        "clp.endpoint_order, lp.country_name "
        "FROM cable_landing_points clp "
        "JOIN landing_points lp ON lp.endpoint_id = clp.endpoint_id "
        "JOIN cables c          ON c.cable_id      = clp.cable_id "
        "WHERE lp.country_name IS NOT NULL AND lp.country_name <> '' "
        "ORDER BY clp.cable_id, clp.endpoint_order, lp.country_name");

    if (PQresultStatus(r) != PGRES_TUPLES_OK) {
        fprintf(stderr, "Erro grafo: %s\n", PQerrorMessage(conn));
        PQclear(r); return;
    }

    int total = PQntuples(r);

    /* group by cable_id and process when cable changes */
    char cur_cable[64] = "";
    char cur_cname[128] = "";
    int  cur_km = 0;
    char stops[MAX_DEPTH][128];
    int  nstops = 0;

    for (int i = 0; i <= total; i++) {
        const char *cid  = i < total ? PQgetvalue(r, i, 0) : "";
        const char *cnam = i < total ? PQgetvalue(r, i, 1) : "";
        const char *km_s = i < total ? PQgetvalue(r, i, 2) : "0";
        const char *ctry = i < total ? PQgetvalue(r, i, 4) : "";

        if (strcmp(cid, cur_cable) != 0) {
            /* process collected stops */
            if (nstops >= 2) {
                /* deduplicate consecutive countries */
                char dedup[MAX_DEPTH][128];
                int  nd = 0;
                for (int j = 0; j < nstops; j++) {
                    if (nd == 0 || strcmp(dedup[nd-1], stops[j]) != 0)
                        strncpy(dedup[nd++], stops[j], 127);
                }
                if (nd >= 2) {
                    int   nseg = nd - 1;
                    float w    = (float)cur_km / nseg;
                    if (w < 20.0f) w = 20.0f;
                    int ci = cable_idx(g, cur_cable, cur_cname);
                    for (int j = 0; j < nd - 1 && ci >= 0; j++) {
                        int a = country_idx(g, dedup[j]);
                        int b = country_idx(g, dedup[j+1]);
                        if (a >= 0 && b >= 0 && a != b) {
                            add_edge(g, a, b, ci, w, cur_km);
                            add_edge(g, b, a, ci, w, cur_km);
                        }
                    }
                }
            }
            strncpy(cur_cable, cid,  63);
            strncpy(cur_cname, cnam, 127);
            cur_km = atoi(km_s);
            nstops = 0;
        }
        if (i < total && ctry[0] && nstops < MAX_DEPTH)
            strncpy(stops[nstops++], ctry, 127);
    }
    PQclear(r);
}

/* ── DFS to collect top-K routes ─────────────────────────────────────────── */

static Route found[MAX_FOUND];
static int   nfound;

static void dfs(Graph *g, int cur, int target, int depth,
                float score, int km, int *path, int *cpath, int np)
{
    if (nfound >= MAX_FOUND) return;

    if (cur == target && np > 1) {
        Route *rt = &found[nfound++];
        rt->score   = score;
        rt->dist_km = km;
        rt->nhops   = np - 1;
        for (int i = 0; i < np; i++) rt->stops[i] = path[i];
        for (int i = 0; i < np-1; i++) rt->cabs[i] = cpath[i];
        return;
    }
    if (depth >= MAX_DEPTH) return;

    Adj *adj = &g->country[cur].adj;
    for (int i = 0; i < adj->n && nfound < MAX_FOUND; i++) {
        Edge *e = &adj->edges[i];
        int visited = 0;
        for (int j = 0; j < np; j++) if (path[j] == e->to) { visited = 1; break; }
        if (visited) continue;
        path[np]     = e->to;
        cpath[np-1]  = e->cable_idx;
        dfs(g, e->to, target, depth+1, score+e->weight, km+e->length_km, path, cpath, np+1);
    }
}

static int cmp_route(const void *a, const void *b)
{
    float da = ((const Route *)a)->score;
    float db = ((const Route *)b)->score;
    return (da > db) - (da < db);
}

static void print_routes(Graph *g, int from_i, int to_i)
{
    nfound = 0;
    int path[MAX_DEPTH+1], cpath[MAX_DEPTH];
    path[0] = from_i;
    dfs(g, from_i, to_i, 0, 0.0f, 0, path, cpath, 1);

    if (nfound == 0) {
        printf(C_RED "  Nenhuma rota encontrada entre os paises selecionados.\n" C_RESET);
        return;
    }
    qsort(found, (size_t)nfound, sizeof(Route), cmp_route);

    int show = nfound < MAX_ROUTES ? nfound : MAX_ROUTES;
    for (int i = 0; i < show; i++) {
        Route *rt = &found[i];
        printf("\n  " C_BOLD C_BYELLOW "#%d" C_RESET " score=%.0f  hops=%d  dist=%d km\n",
               i+1, rt->score, rt->nhops, rt->dist_km);
        printf("  Rota: ");
        for (int j = 0; j <= rt->nhops; j++) {
            if (j) printf(" → ");
            printf(C_CYAN "%s" C_RESET, g->country[rt->stops[j]].name);
        }
        printf("\n  Cabos:\n");
        for (int j = 0; j < rt->nhops; j++) {
            int ci = rt->cabs[j];
            printf("    " C_DIM "%-30s" C_RESET " (%s → %s)\n",
                   g->cable[ci].name,
                   g->country[rt->stops[j]].name,
                   g->country[rt->stops[j+1]].name);
        }
    }
}

/* ── BFS to check reachability (for critical cuts) ───────────────────────── */

static int can_reach(Graph *g, int from, int to, int blocked_ci, int max_depth)
{
    if (from == to) return 1;
    int visited[MAX_COUNTRIES] = {0};
    int queue[MAX_COUNTRIES * MAX_DEPTH];
    int depth_q[MAX_COUNTRIES * MAX_DEPTH];
    int head = 0, tail = 0;
    queue[tail] = from; depth_q[tail] = 0; tail++;
    visited[from] = 1;

    while (head < tail) {
        int cur = queue[head];
        int dep = depth_q[head++];
        if (dep >= max_depth) continue;
        Adj *adj = &g->country[cur].adj;
        for (int i = 0; i < adj->n; i++) {
            Edge *e = &adj->edges[i];
            if (e->cable_idx == blocked_ci) continue;
            if (e->to == to) return 1;
            if (!visited[e->to]) {
                visited[e->to] = 1;
                queue[tail] = e->to; depth_q[tail] = dep+1; tail++;
            }
        }
    }
    return 0;
}

/* ── list countries from DB ──────────────────────────────────────────────── */

static void list_countries(PGconn *conn)
{
    PGresult *r = PQexec(conn,
        "SELECT DISTINCT country_name FROM landing_points "
        "WHERE country_name IS NOT NULL AND country_name <> '' ORDER BY country_name");
    if (PQresultStatus(r) != PGRES_TUPLES_OK) { PQclear(r); return; }

    int n = PQntuples(r);
    printf(C_DIM "  Paises disponíveis (%d):\n  " C_RESET, n);
    for (int i = 0; i < n; i++) {
        printf("%s", PQgetvalue(r, i, 0));
        if (i < n-1) printf(", ");
        if ((i+1) % 5 == 0) printf("\n  ");
    }
    printf("\n");
    PQclear(r);
}

/* ── sub-functions ───────────────────────────────────────────────────────── */

static void do_top_routes(PGconn *conn)
{
    print_header("Top 3 Rotas Entre Paises");
    list_countries(conn);

    char a[128], b[128];
    read_line(a, sizeof(a), "Pais origem");
    read_line(b, sizeof(b), "Pais destino");
    if (!a[0] || !b[0]) return;

    printf(C_DIM "\n  Construindo grafo..." C_RESET); fflush(stdout);
    Graph g;
    build_graph(conn, &g);

    int ai = -1, bi = -1;
    for (int i = 0; i < g.nc; i++) {
        if (strcasecmp(g.country[i].name, a) == 0) ai = i;
        if (strcasecmp(g.country[i].name, b) == 0) bi = i;
    }

    if (ai < 0) { printf(C_RED "\n  Pais '%s' nao encontrado no grafo.\n" C_RESET, a); }
    else if (bi < 0) { printf(C_RED "\n  Pais '%s' nao encontrado no grafo.\n" C_RESET, b); }
    else { printf("\r  " C_DIM "Grafo: %d paises, %d cabos. Calculando..." C_RESET "\n", g.nc, g.nca); print_routes(&g, ai, bi); }

    free_graph(&g);
    press_enter();
}

static void do_critical_cuts(PGconn *conn)
{
    print_header("Cabos Criticos (Corte de Isolamento)");
    list_countries(conn);

    char a[128], b[128];
    read_line(a, sizeof(a), "Pais origem");
    read_line(b, sizeof(b), "Pais destino");
    if (!a[0] || !b[0]) return;

    Graph g;
    build_graph(conn, &g);

    int ai = -1, bi = -1;
    for (int i = 0; i < g.nc; i++) {
        if (strcasecmp(g.country[i].name, a) == 0) ai = i;
        if (strcasecmp(g.country[i].name, b) == 0) bi = i;
    }

    if (ai < 0 || bi < 0) {
        printf(C_RED "  Um ou ambos os paises nao encontrados no grafo.\n" C_RESET);
        free_graph(&g); press_enter(); return;
    }

    /* collect cables in top routes */
    nfound = 0;
    int path[MAX_DEPTH+1], cpath[MAX_DEPTH];
    path[0] = ai;
    dfs(&g, ai, bi, 0, 0.0f, 0, path, cpath, 1);
    qsort(found, (size_t)nfound, sizeof(Route), cmp_route);

    /* check which cables, if cut, isolate the pair */
    int seen_ci[MAX_CABLES] = {0};
    int critical_ci[MAX_CABLES];
    int ncritical = 0;

    int check_n = nfound < MAX_ROUTES ? nfound : MAX_ROUTES;
    for (int i = 0; i < check_n; i++) {
        for (int j = 0; j < found[i].nhops; j++) {
            int ci = found[i].cabs[j];
            if (seen_ci[ci]) continue;
            seen_ci[ci] = 1;
            if (!can_reach(&g, ai, bi, ci, 8))
                critical_ci[ncritical++] = ci;
        }
    }

    if (ncritical == 0) {
        printf(C_DIM "  Nenhum cabo critico encontrado (ha rotas alternativas).\n" C_RESET);
    } else {
        static const Col cols[] = {{"cable_id",30},{"nome do cabo",40},{"impacto",35}};
        tbl_header(cols, 3);
        for (int i = 0; i < ncritical; i++) {
            int ci = critical_ci[i];
            const char *v[3] = {g.cable[ci].id, g.cable[ci].name, "isolamento total entre os paises"};
            tbl_row(v, cols, 3);
        }
        tbl_footer(cols, 3);
        printf(C_DIM "  %d cabo(s) critico(s) identificado(s).\n" C_RESET, ncritical);
    }

    free_graph(&g);
    press_enter();
}

static void do_endpoint_risk(PGconn *conn)
{
    print_header("Risco de Endpoints por Pais");
    list_countries(conn);

    char country[128];
    read_line(country, sizeof(country), "Pais (nome exato)");
    if (!country[0]) return;

    const char *p[1] = {country};
    PGresult *r = PQexecParams(conn,
        "SELECT lp.endpoint_id, lp.endpoint_name, lp.city_name, "
        "COUNT(DISTINCT clp.cable_id) AS cable_degree, "
        "COUNT(DISTINCT lp2.country_name) AS country_reach "
        "FROM landing_points lp "
        "JOIN cable_landing_points clp  ON clp.endpoint_id = lp.endpoint_id "
        "LEFT JOIN cable_landing_points clp2 ON clp2.cable_id = clp.cable_id "
        "LEFT JOIN landing_points lp2   ON lp2.endpoint_id = clp2.endpoint_id "
        "WHERE lp.country_name = $1 "
        "GROUP BY lp.endpoint_id, lp.endpoint_name, lp.city_name "
        "ORDER BY cable_degree DESC, country_reach DESC "
        "LIMIT 25",
        1, NULL, p, NULL, NULL, 0);

    if (PQresultStatus(r) != PGRES_TUPLES_OK || PQntuples(r) == 0) {
        printf(C_RED "  Nenhum endpoint encontrado para '%s'.\n" C_RESET, country);
        PQclear(r); press_enter(); return;
    }

    static const Col cols[] = {
        {"endpoint",36},{"cidade",20},{"conexoes",9},{"alcance",8},{"risco",7},{"nivel",6}
    };
    tbl_header(cols, 6);

    int n = PQntuples(r);
    for (int i = 0; i < n; i++) {
        int deg   = atoi(PQgetvalue(r, i, 3));
        int reach = atoi(PQgetvalue(r, i, 4));
        float risk = (float)(deg * 3.2f + reach * 1.8f);
        char rs[16], ls[8], ds[8], rcs[8];
        snprintf(rs, sizeof(rs), "%.1f", risk);
        snprintf(ds, sizeof(ds), "%d",   deg);
        snprintf(rcs, sizeof(rcs), "%d", reach);
        const char *level = risk >= 38 ? C_RED "alto" C_RESET :
                            risk >= 24 ? C_YELLOW "medio" C_RESET :
                                        C_GREEN "baixo" C_RESET;
        snprintf(ls, sizeof(ls), "%s", risk >= 38 ? "alto" : risk >= 24 ? "medio" : "baixo");
        const char *v[6] = {PQgetvalue(r,i,1), PQgetvalue(r,i,2), ds, rcs, rs, ls};
        tbl_row(v, cols, 6);
        (void)level;
    }
    tbl_footer(cols, 6);
    printf(C_DIM "  %d endpoint(s) em %s.\n" C_RESET, n, country);

    PQclear(r);
    press_enter();
}

/* ── public menu ─────────────────────────────────────────────────────────── */

void menu_routes(PGconn *conn)
{
    while (1) {
        print_header("Analise de Rotas e Risco");
        printf("  [1] Top 3 rotas entre paises\n");
        printf("  [2] Cabos criticos (corte de isolamento)\n");
        printf("  [3] Risco de endpoints por pais\n");
        printf("  [0] Voltar\n");

        int ch = read_choice(0, 3);
        if (ch == 0) return;
        if (ch == 1) do_top_routes(conn);
        if (ch == 2) do_critical_cuts(conn);
        if (ch == 3) do_endpoint_risk(conn);
    }
}
