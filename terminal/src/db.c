#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "db.h"

PGconn *db_connect(void)
{
    const char *ws = getenv("WORKSPACE_ROOT");
    if (!ws) ws = "/workspaces/RMIFO";

    const char *sock = getenv("PG_SOCKET");
    char default_sock[512];
    if (!sock) {
        snprintf(default_sock, sizeof(default_sock), "%s/.local/run", ws);
        sock = default_sock;
    }

    const char *port = getenv("PG_PORT");
    if (!port) port = "55432";

    char dsn[1024];
    snprintf(dsn, sizeof(dsn),
             "host=%s port=%s dbname=global_network_resilience user=postgres",
             sock, port);

    PGconn *conn = PQconnectdb(dsn);
    if (PQstatus(conn) != CONNECTION_OK) {
        fprintf(stderr, "Falha na conexão: %s\n", PQerrorMessage(conn));
        PQfinish(conn);
        exit(1);
    }
    return conn;
}

void db_close(PGconn *conn) { if (conn) PQfinish(conn); }

void db_fatal(PGconn *conn, const char *ctx)
{
    fprintf(stderr, "Erro DB [%s]: %s\n", ctx, PQerrorMessage(conn));
    PQfinish(conn);
    exit(1);
}
