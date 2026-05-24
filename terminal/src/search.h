#pragma once
#include <libpq-fe.h>

void menu_search(PGconn *conn);
void show_cable_details(PGconn *conn, const char *cable_id);
