#include <stdio.h>
#include <string.h>
#include <stdlib.h>
#include <unistd.h>
#include "display.h"

void clear_screen(void) { printf("\033[2J\033[H"); fflush(stdout); }

void print_header(const char *title)
{
    int len = (int)strlen(title) + 4;
    printf("\n" C_BOLD C_BCYAN);
    for (int i = 0; i < len; i++) printf("─");
    printf("\n  %s\n", title);
    for (int i = 0; i < len; i++) printf("─");
    printf(C_RESET "\n\n");
}

void print_sub(const char *text)
{
    printf(C_BOLD C_BYELLOW "  ▸ %s" C_RESET "\n", text);
}

void press_enter(void)
{
    printf(C_DIM "\n  [Enter para continuar]" C_RESET);
    fflush(stdout);
    int c;
    while ((c = getchar()) != '\n' && c != EOF);
}

int read_choice(int min, int max)
{
    char buf[32];
    int choice;
    while (1) {
        printf(C_BOLD C_BCYAN "\n  » " C_RESET);
        fflush(stdout);
        if (!fgets(buf, sizeof(buf), stdin)) return min;
        if (sscanf(buf, "%d", &choice) == 1 && choice >= min && choice <= max)
            return choice;
        printf(C_RED "  Inválido. Digite entre %d e %d.\n" C_RESET, min, max);
    }
}

void read_line(char *buf, int size, const char *prompt)
{
    printf(C_BCYAN "  %s: " C_RESET, prompt);
    fflush(stdout);
    if (!fgets(buf, size, stdin)) { buf[0] = '\0'; return; }
    int len = (int)strlen(buf);
    if (len > 0 && buf[len - 1] == '\n') buf[len - 1] = '\0';
}

/* ── box-drawing helpers ─────────────────────────────────────────────────── */

static void tbl_line(const Col *cols, int n, char l, char m, char r, char f)
{
    printf(C_DIM "%c" C_RESET, l);
    for (int i = 0; i < n; i++) {
        for (int j = 0; j < cols[i].width + 2; j++) printf(C_DIM "%c" C_RESET, f);
        printf(C_DIM "%c" C_RESET, i < n - 1 ? m : r);
    }
    printf("\n");
}

void tbl_header(const Col *cols, int n)
{
    tbl_line(cols, n, 0xe2 /* ┌ */, 0, 0, 0); /* we use ASCII fallback */
    /* ASCII fallback for portability */
    printf(C_DIM "+");
    for (int i = 0; i < n; i++) {
        for (int j = 0; j < cols[i].width + 2; j++) printf("-");
        printf("+");
    }
    printf(C_RESET "\n");

    printf(C_DIM "|" C_RESET);
    for (int i = 0; i < n; i++) {
        printf(C_BOLD " %-*s " C_RESET C_DIM "|" C_RESET, cols[i].width, cols[i].label);
    }
    printf("\n");

    printf(C_DIM "+");
    for (int i = 0; i < n; i++) {
        for (int j = 0; j < cols[i].width + 2; j++) printf("=");
        printf("+");
    }
    printf(C_RESET "\n");
}

void tbl_row(const char **vals, const Col *cols, int n)
{
    printf(C_DIM "|" C_RESET);
    for (int i = 0; i < n; i++) {
        const char *v = vals[i] ? vals[i] : "";
        printf(" %-*.*s " C_DIM "|" C_RESET, cols[i].width, cols[i].width, v);
    }
    printf("\n");
}

void tbl_footer(const Col *cols, int n)
{
    printf(C_DIM "+");
    for (int i = 0; i < n; i++) {
        for (int j = 0; j < cols[i].width + 2; j++) printf("-");
        printf("+");
    }
    printf(C_RESET "\n");
}

/* ── ASCII sparkline bar ─────────────────────────────────────────────────── */

void ascii_bar(float value, float max, int width, const char *color)
{
    if (max <= 0) max = 1;
    int filled = (int)((value / max) * width);
    if (filled > width) filled = width;
    printf("%s", color ? color : "");
    for (int i = 0; i < filled; i++) printf("█");
    printf(C_DIM);
    for (int i = filled; i < width; i++) printf("░");
    printf(C_RESET);
}
