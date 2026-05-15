#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "db.h"
#include "display.h"
#include "search.h"
#include "routes.h"
#include "telemetry.h"
#include "probe.h"

static void print_banner(void)
{
    printf("\n");
    printf(C_BCYAN C_BOLD
        "  ██████╗ ███╗   ███╗██╗███████╗ ██████╗ \n"
        "  ██╔══██╗████╗ ████║██║██╔════╝██╔═══██╗\n"
        "  ██████╔╝██╔████╔██║██║█████╗  ██║   ██║\n"
        "  ██╔══██╗██║╚██╔╝██║██║██╔══╝  ██║   ██║\n"
        "  ██║  ██║██║ ╚═╝ ██║██║██║     ╚██████╔╝\n"
        "  ╚═╝  ╚═╝╚═╝     ╚═╝╚═╝╚═╝      ╚═════╝ \n"
        C_RESET);
    printf(C_DIM
        "  Resilience Map of International Fiber Optics\n"
        "  Terminal Interface  |  Global Network Analysis\n"
        C_RESET "\n");
}

static void main_menu(PGconn *conn)
{
    while (1) {
        print_header("Menu Principal");
        printf("  " C_BOLD "[1]" C_RESET " Busca de Cabos\n");
        printf("  " C_BOLD "[2]" C_RESET " Detalhes de um Cabo\n");
        printf("  " C_BOLD "[3]" C_RESET " Analise de Rotas\n");
        printf("  " C_BOLD "[4]" C_RESET " Sonda de Rede\n");
        printf("  " C_BOLD "[5]" C_RESET " Telemetria Avancada\n");
        printf("  " C_BOLD "[6]" C_RESET " Alertas Ativos\n");
        printf("  " C_BOLD "[0]" C_RESET " Sair\n");

        int ch = read_choice(0, 6);

        switch (ch) {
        case 0:
            printf(C_BGREEN "\n  Ate logo!\n\n" C_RESET);
            return;

        case 1:
            menu_search(conn);
            break;

        case 2: {
            char cable_id[256];
            print_header("Detalhes do Cabo");
            read_line(cable_id, sizeof(cable_id), "cable_id");
            if (cable_id[0])
                show_cable_details(conn, cable_id);
            break;
        }

        case 3:
            menu_routes(conn);
            break;

        case 4:
            menu_probe(conn);
            break;

        case 5:
            menu_telemetry(conn);
            break;

        case 6:
            show_alerts(conn);
            break;
        }
    }
}

int main(void)
{
    print_banner();

    PGconn *conn = db_connect();

    /* Quick connectivity check */
    PGresult *ping = PQexec(conn, "SELECT COUNT(*) FROM cables");
    if (PQresultStatus(ping) == PGRES_TUPLES_OK) {
        printf(C_BGREEN "  Banco de dados conectado." C_RESET
               "  Cabos catalogados: " C_BOLD "%s" C_RESET "\n\n",
               PQgetvalue(ping, 0, 0));
    } else {
        printf(C_YELLOW "  Aviso: tabela 'cables' nao encontrada.\n" C_RESET);
    }
    PQclear(ping);

    main_menu(conn);
    db_close(conn);
    return 0;
}
