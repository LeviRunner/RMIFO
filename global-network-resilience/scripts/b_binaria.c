#include <ctype.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <strings.h>

#define MAX_FIELDS 8
#define LINE_BUFFER_SIZE 32768

typedef struct {
	char *cable_id;
	char *cable_name;
	char *owners;
	int rfs_year;
	int is_operational;
	int landing_points;
	char *suppliers;
} Cable;

char *dup_string(const char *src) {
	size_t len = strlen(src);
	char *copy = (char *)malloc(len + 1);
	if (copy == NULL) {
		fprintf(stderr, "dup_string: failed to allocate %zu bytes\n", len + 1);
		return NULL;
	}
	memcpy(copy, src, len + 1);
	return copy;
}

void free_cable(Cable *cable) {
	free(cable->cable_id);
	free(cable->cable_name);
	free(cable->owners);
	free(cable->suppliers);
}

void free_cables(Cable *cables, size_t count) {
	for (size_t i = 0; i < count; i++) {
		free_cable(&cables[i]);
	}
	free(cables);
}

void trim_newline(char *text) {
	if (text == NULL) {
		return;
	}

	size_t len = strlen(text);
	while (len > 0 && (text[len - 1] == '\n' || text[len - 1] == '\r')) {
		text[len - 1] = '\0';
		len--;
	}
}

int parse_int_or_default(const char *text, int default_value) {
	if (text == NULL || text[0] == '\0') {
		return default_value;
	}

	char *end_ptr = NULL;
	long value = strtol(text, &end_ptr, 10);
	if (end_ptr == text) {
		return default_value;
	}

	return (int)value;
}

int parse_bool_text(const char *text, int default_value) {
	if (text == NULL || text[0] == '\0') {
		return default_value;
	}

	if (strcasecmp(text, "true") == 0 || strcmp(text, "1") == 0) {
		return 1;
	}
	if (strcasecmp(text, "false") == 0 || strcmp(text, "0") == 0) {
		return 0;
	}

	return default_value;
}

int parse_csv_line(const char *line, char **fields, int max_fields) {
	int count = 0;
	int in_quotes = 0;
	size_t len = strlen(line);
	char *token = (char *)malloc(len + 1);
	size_t token_len = 0;

	if (token == NULL) {
		return -1;
	}

	for (size_t i = 0; i < len; i++) {
		char c = line[i];

		if (c == '"') {
			if (in_quotes && i + 1 < len && line[i + 1] == '"') {
				token[token_len++] = '"';
				i++;
			} else {
				in_quotes = !in_quotes;
			}
		} else if (c == ',' && !in_quotes) {
			token[token_len] = '\0';
			if (count < max_fields) {
				fields[count] = dup_string(token);
				if (fields[count] == NULL) {
					free(token);
					return -1;
				}
				count++;
			}
			token_len = 0;
		} else if ((c == '\n' || c == '\r') && !in_quotes) {
			continue;
		} else {
			token[token_len++] = c;
		}
	}

	token[token_len] = '\0';
	if (count < max_fields) {
		fields[count] = dup_string(token);
		if (fields[count] == NULL) {
			free(token);
			return -1;
		}
		count++;
	}

	free(token);
	return count;
}

void free_fields(char **fields, int field_count) {
	for (int i = 0; i < field_count; i++) {
		free(fields[i]);
	}
}

int contains_case_insensitive(const char *text, const char *query) {
	if (query == NULL || query[0] == '\0') {
		return 1;
	}
	if (text == NULL) {
		return 0;
	}

	size_t text_len = strlen(text);
	size_t query_len = strlen(query);

	if (query_len > text_len) {
		return 0;
	}

	for (size_t i = 0; i <= text_len - query_len; i++) {
		size_t j = 0;
		while (j < query_len && tolower((unsigned char)text[i + j]) == tolower((unsigned char)query[j])) {
			j++;
		}
		if (j == query_len) {
			return 1;
		}
	}

	return 0;
}

int load_cables(const char *csv_path, Cable **out_cables, size_t *out_count) {
	FILE *file = fopen(csv_path, "r");
	if (file == NULL) {
		return 0;
	}

	char line[LINE_BUFFER_SIZE];
	Cable *cables = NULL;
	size_t count = 0;
	size_t capacity = 0;

	if (fgets(line, sizeof(line), file) == NULL) {
		fclose(file);
		return 0;
	}

	while (fgets(line, sizeof(line), file) != NULL) {
		char *fields[MAX_FIELDS] = {0};
		int field_count = parse_csv_line(line, fields, MAX_FIELDS);

		if (field_count < 7) {
			free_fields(fields, field_count > 0 ? field_count : 0);
			continue;
		}

		if (count == capacity) {
			size_t new_capacity = (capacity == 0) ? 128 : capacity * 2;
			Cable *new_cables = (Cable *)realloc(cables, new_capacity * sizeof(Cable));
			if (new_cables == NULL) {
				free_fields(fields, field_count);
				free_cables(cables, count);
				fclose(file);
				return 0;
			}
			cables = new_cables;
			capacity = new_capacity;
		}

		cables[count].cable_id = dup_string(fields[0]);
		cables[count].cable_name = dup_string(fields[1]);
		cables[count].owners = dup_string(fields[2]);
		cables[count].rfs_year = parse_int_or_default(fields[4], -1);
		cables[count].is_operational = parse_bool_text(fields[5], -1);
		cables[count].landing_points = parse_int_or_default(fields[6], -1);
		cables[count].suppliers = dup_string(field_count > 7 ? fields[7] : "");

		if (cables[count].cable_id == NULL || cables[count].cable_name == NULL || cables[count].owners == NULL ||
			cables[count].suppliers == NULL) {
			free_fields(fields, field_count);
			free_cables(cables, count + 1);
			fclose(file);
			return 0;
		}

		free_fields(fields, field_count);
		count++;
	}

	fclose(file);
	*out_cables = cables;
	*out_count = count;
	return 1;
}

void print_cable(const Cable *cable) {
	printf("----------------------------------------\n");
	printf("cable_id: %s\n", cable->cable_id);
	printf("cable_name: %s\n", cable->cable_name);
	printf("owners: %s\n", cable->owners);
	if (cable->rfs_year >= 0) {
		printf("rfs_year: %d\n", cable->rfs_year);
	} else {
		printf("rfs_year: (sem dado)\n");
	}
	if (cable->is_operational >= 0) {
		printf("is_operational: %s\n", cable->is_operational ? "True" : "False");
	} else {
		printf("is_operational: (sem dado)\n");
	}
	if (cable->landing_points >= 0) {
		printf("landing_points: %d\n", cable->landing_points);
	} else {
		printf("landing_points: (sem dado)\n");
	}
	printf("suppliers: %s\n", cable->suppliers[0] ? cable->suppliers : "(sem dado)");
}

void read_line(const char *prompt, char *buffer, size_t size) {
	printf("%s", prompt);
	if (fgets(buffer, (int)size, stdin) == NULL) {
		buffer[0] = '\0';
		return;
	}
	trim_newline(buffer);
}

void search_by_text(const Cable *cables, size_t count, int mode, const char *query) {
	size_t matches = 0;

	for (size_t i = 0; i < count; i++) {
		const char *field_value = "";

		switch (mode) {
			case 1:
				field_value = cables[i].cable_id;
				break;
			case 2:
				field_value = cables[i].cable_name;
				break;
			case 3:
				field_value = cables[i].owners;
				break;
			case 7:
				field_value = cables[i].suppliers;
				break;
			default:
				break;
		}

		if (contains_case_insensitive(field_value, query)) {
			print_cable(&cables[i]);
			matches++;
		}
	}

	printf("\nTotal encontrados: %zu\n", matches);
}

void search_by_year(const Cable *cables, size_t count, int year) {
	size_t matches = 0;

	for (size_t i = 0; i < count; i++) {
		if (cables[i].rfs_year == year) {
			print_cable(&cables[i]);
			matches++;
		}
	}

	printf("\nCabos instalados em %d: %zu\n", year, matches);
}

void search_by_operational(const Cable *cables, size_t count, int operational) {
	size_t matches = 0;

	for (size_t i = 0; i < count; i++) {
		if (cables[i].is_operational == operational) {
			print_cable(&cables[i]);
			matches++;
		}
	}

	printf("\nTotal encontrados: %zu\n", matches);
}

void search_by_landing_points(const Cable *cables, size_t count, int landing_points) {
	size_t matches = 0;

	for (size_t i = 0; i < count; i++) {
		if (cables[i].landing_points == landing_points) {
			print_cable(&cables[i]);
			matches++;
		}
	}

	printf("\nTotal encontrados: %zu\n", matches);
}

int main(int argc, char *argv[]) {
	const char *csv_path = (argc > 1) ? argv[1] : NULL;
	const char *csv_candidates[] = {
		"data/external/submarine_cables.csv",
		"../data/external/submarine_cables.csv",
		"global-network-resilience/data/external/submarine_cables.csv"};
	const size_t candidate_count = sizeof(csv_candidates) / sizeof(csv_candidates[0]);
	Cable *cables = NULL;
	size_t count = 0;

	if (csv_path != NULL) {
		if (!load_cables(csv_path, &cables, &count)) {
			printf("Erro ao carregar CSV informado: %s\n", csv_path);
			return 1;
		}
	} else {
		for (size_t i = 0; i < candidate_count; i++) {
			if (load_cables(csv_candidates[i], &cables, &count)) {
				csv_path = csv_candidates[i];
				break;
			}
		}

		if (csv_path == NULL) {
			printf("Erro ao carregar CSV. Caminhos tentados:\n");
			for (size_t i = 0; i < candidate_count; i++) {
				printf("- %s\n", csv_candidates[i]);
			}
			printf("Dica: passe o caminho do CSV como argumento ao executar.\n");
			return 1;
		}
	}

	printf("Base carregada com %zu cabos.\n", count);
	printf("CSV utilizado: %s\n", csv_path);

	while (1) {
		char option_text[32];
		int option;

		printf("\n===== MENU DE PESQUISA DE CABOS =====\n");
		printf("1. Pesquisar por cable_id\n");
		printf("2. Pesquisar por cable_name\n");
		printf("3. Pesquisar por owners\n");
		printf("4. Pesquisar por rfs_year\n");
		printf("5. Pesquisar por is_operational\n");
		printf("6. Pesquisar por landing_points\n");
		printf("7. Pesquisar por suppliers\n");
		printf("0. Sair\n");

		read_line("Escolha uma opção: ", option_text, sizeof(option_text));
		option = parse_int_or_default(option_text, -1);

		if (option == 0) {
			break;
		}

		if (option < 0 || option > 7) {
			printf("Opcao invalida.\n");
			continue;
		}

		if (option == 1 || option == 2 || option == 3 || option == 7) {
			char query[512];
			read_line("Digite o termo de busca (parcial): ", query, sizeof(query));
			if (query[0] == '\0') {
				printf("Busca vazia.\n");
				continue;
			}
			search_by_text(cables, count, option, query);
			continue;
		}

		if (option == 4) {
			char year_text[32];
			int year;
			read_line("Digite o ano (ex: 2024): ", year_text, sizeof(year_text));
			year = parse_int_or_default(year_text, -1);
			if (year < 0) {
				printf("Ano invalido.\n");
				continue;
			}
			search_by_year(cables, count, year);
			continue;
		}

		if (option == 5) {
			char op_text[32];
			int operational;
			read_line("Digite True/False (ou 1/0): ", op_text, sizeof(op_text));
			operational = parse_bool_text(op_text, -1);
			if (operational < 0) {
				printf("Valor invalido para is_operational.\n");
				continue;
			}
			search_by_operational(cables, count, operational);
			continue;
		}

		if (option == 6) {
			char points_text[32];
			int points;
			read_line("Digite o numero de landing_points: ", points_text, sizeof(points_text));
			points = parse_int_or_default(points_text, -1);
			if (points < 0) {
				printf("Valor invalido para landing_points.\n");
				continue;
			}
			search_by_landing_points(cables, count, points);
		}
	}

	free_cables(cables, count);
	printf("Programa finalizado.\n");
	return 0;
}
