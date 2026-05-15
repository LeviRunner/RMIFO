#pragma once
#include <libpq-fe.h>

void menu_telemetry(PGconn *conn);
void show_alerts(PGconn *conn);
