#pragma once
#include <stddef.h>

/* ANSI colours */
#define C_RESET   "\033[0m"
#define C_BOLD    "\033[1m"
#define C_DIM     "\033[2m"
#define C_RED     "\033[31m"
#define C_GREEN   "\033[32m"
#define C_YELLOW  "\033[33m"
#define C_BLUE    "\033[34m"
#define C_CYAN    "\033[36m"
#define C_BRED    "\033[91m"
#define C_BGREEN  "\033[92m"
#define C_BYELLOW "\033[93m"
#define C_BCYAN   "\033[96m"
#define C_BWHITE  "\033[97m"

typedef struct { const char *label; int width; } Col;

void clear_screen(void);
void print_header(const char *title);
void print_sub(const char *text);
void press_enter(void);
int  read_choice(int min, int max);
void read_line(char *buf, int size, const char *prompt);

void tbl_header(const Col *cols, int n);
void tbl_row(const char **vals, const Col *cols, int n);
void tbl_footer(const Col *cols, int n);

/* simple ASCII bar: value 0..max into width chars using block chars */
void ascii_bar(float value, float max, int width, const char *color);
