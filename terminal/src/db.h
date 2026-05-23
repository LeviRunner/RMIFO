#pragma once
#include <libpq-fe.h>

PGconn *db_connect(void);
void    db_close(PGconn *conn);

/* prints error and exits */
void db_fatal(PGconn *conn, const char *ctx);
