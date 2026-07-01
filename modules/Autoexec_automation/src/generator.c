#define _CRT_SECURE_NO_WARNINGS

#include <ctype.h>
#include <errno.h>
#include <stdbool.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#if defined(_WIN32) || defined(_WIN64)
#include <windows.h>
#else
#include <dirent.h>
#include <sys/stat.h>
#include <sys/types.h>
#include <unistd.h>
#endif

#define STATUS_NFE "Autorizado o uso da NF-e"
#define DEFAULT_DIRECT_FOLDER "Autoexec_padrao"
#define MAX_PATH_LEN 4096

static void print_usage(const char *prog_name) {
    printf("Gerador de Autoexecs para NF-e\n\n");
    printf("Uso:\n");
    printf("  %s --xml <caminho_xml> [--out <nome_pasta>]\n", prog_name);
    printf("  %s\n\n", prog_name);
    printf("Sem argumentos, processa todos os XMLs da pasta 'xmls'.\n");
}

static bool is_digits_only(const char *text) {
    if (text == NULL || text[0] == '\0') {
        return false;
    }

    for (size_t i = 0; text[i] != '\0'; i++) {
        if (!isdigit((unsigned char)text[i])) {
            return false;
        }
    }
    return true;
}

static bool get_executable_dir(char *buffer, size_t buffer_size) {
#if defined(_WIN32) || defined(_WIN64)
    DWORD len = GetModuleFileNameA(NULL, buffer, (DWORD)buffer_size);
    if (len == 0 || len >= buffer_size) {
        return false;
    }

    char *last_sep = strrchr(buffer, '\\');
    if (last_sep == NULL) {
        return false;
    }

    *last_sep = '\0';
    return true;
#else
    if (getcwd(buffer, buffer_size) == NULL) {
        return false;
    }
    return true;
#endif
}

static bool join_path(char *out, size_t out_size, const char *base, const char *name) {
#if defined(_WIN32) || defined(_WIN64)
    int written = snprintf(out, out_size, "%s\\%s", base, name);
#else
    int written = snprintf(out, out_size, "%s/%s", base, name);
#endif
    return written > 0 && (size_t)written < out_size;
}

static bool ensure_directory_exists(const char *path) {
    char tmp[MAX_PATH_LEN];
    size_t len = strlen(path);
    if (len == 0 || len >= sizeof(tmp)) {
        return false;
    }

    strcpy(tmp, path);

#if defined(_WIN32) || defined(_WIN64)
    for (size_t i = 3; i < len; i++) {
        if (tmp[i] == '\\' || tmp[i] == '/') {
            char original = tmp[i];
            tmp[i] = '\0';
            CreateDirectoryA(tmp, NULL);
            tmp[i] = original;
        }
    }

    if (CreateDirectoryA(tmp, NULL) == 0) {
        DWORD err = GetLastError();
        if (err != ERROR_ALREADY_EXISTS) {
            return false;
        }
    }
#else
    for (size_t i = 1; i < len; i++) {
        if (tmp[i] == '/') {
            char original = tmp[i];
            tmp[i] = '\0';
            if (mkdir(tmp, 0775) != 0 && errno != EEXIST) {
                return false;
            }
            tmp[i] = original;
        }
    }

    if (mkdir(tmp, 0775) != 0 && errno != EEXIST) {
        return false;
    }
#endif

    return true;
}

static char *read_file_all(const char *path, size_t *out_size) {
    FILE *fp = fopen(path, "rb");
    if (!fp) {
        return NULL;
    }

    if (fseek(fp, 0, SEEK_END) != 0) {
        fclose(fp);
        return NULL;
    }

    long length = ftell(fp);
    if (length < 0) {
        fclose(fp);
        return NULL;
    }

    if (fseek(fp, 0, SEEK_SET) != 0) {
        fclose(fp);
        return NULL;
    }

    char *buffer = (char *)malloc((size_t)length + 1);
    if (!buffer) {
        fclose(fp);
        return NULL;
    }

    size_t read = fread(buffer, 1, (size_t)length, fp);
    fclose(fp);

    if (read != (size_t)length) {
        free(buffer);
        return NULL;
    }

    buffer[length] = '\0';
    if (out_size) {
        *out_size = (size_t)length;
    }
    return buffer;
}

static bool write_text_file(const char *path, const char *content) {
    FILE *fp = fopen(path, "wb");
    if (!fp) {
        return false;
    }

    size_t len = strlen(content);
    size_t written = fwrite(content, 1, len, fp);
    fclose(fp);

    return written == len;
}

static char *extract_tag_value(const char *xml, const char *tag_name) {
    char open_tag[128];
    char close_tag[128];

    snprintf(open_tag, sizeof(open_tag), "<%s>", tag_name);
    snprintf(close_tag, sizeof(close_tag), "</%s>", tag_name);

    const char *start = strstr(xml, open_tag);
    if (!start) {
        return NULL;
    }

    start += strlen(open_tag);
    const char *end = strstr(start, close_tag);
    if (!end || end < start) {
        return NULL;
    }

    size_t len = (size_t)(end - start);
    char *value = (char *)malloc(len + 1);
    if (!value) {
        return NULL;
    }

    memcpy(value, start, len);
    value[len] = '\0';
    return value;
}

static char *build_one_line_xml(const char *xml) {
    size_t len = strlen(xml);
    char *out = (char *)malloc(len + 1);
    if (!out) {
        return NULL;
    }

    size_t j = 0;
    bool in_ws = false;

    for (size_t i = 0; i < len; i++) {
        unsigned char ch = (unsigned char)xml[i];
        if (isspace(ch)) {
            in_ws = true;
            continue;
        }

        if (in_ws && j > 0) {
            out[j++] = ' ';
        }

        out[j++] = (char)ch;
        in_ws = false;
    }

    out[j] = '\0';
    return out;
}

static char *escape_sql_string(const char *value) {
    size_t len = strlen(value);
    size_t quote_count = 0;

    for (size_t i = 0; i < len; i++) {
        if (value[i] == '\'') {
            quote_count++;
        }
    }

    char *escaped = (char *)malloc(len + quote_count + 1);
    if (!escaped) {
        return NULL;
    }

    size_t j = 0;
    for (size_t i = 0; i < len; i++) {
        escaped[j++] = value[i];
        if (value[i] == '\'') {
            escaped[j++] = '\'';
        }
    }

    escaped[j] = '\0';
    return escaped;
}

static bool format_numeronf(const char *nnf, const char *serie, char out[13]) {
    if (!is_digits_only(nnf) || !is_digits_only(serie)) {
        return false;
    }

    char nnf_9[10];
    char serie_3[4];

    size_t nnf_len = strlen(nnf);
    size_t serie_len = strlen(serie);

    if (nnf_len > 9 || serie_len > 3) {
        return false;
    }

    snprintf(nnf_9, sizeof(nnf_9), "%09d", atoi(nnf));
    snprintf(serie_3, sizeof(serie_3), "%03d", atoi(serie));

    snprintf(out, 13, "%s%s", nnf_9, serie_3);
    return true;
}

static char *build_autoexec_sql(const char *nprot, const char *numeronf, const char *xml_one_line) {
    char *nprot_sql = escape_sql_string(nprot);
    char *xml_sql = escape_sql_string(xml_one_line);
    if (!nprot_sql || !xml_sql) {
        free(nprot_sql);
        free(xml_sql);
        return NULL;
    }

    const char *prefix = "update vendas v set v.status='";
    const char *middle1 = "', v.nfeprotocolo='";
    const char *middle2 = "', v.nfexml='";
    const char *suffix = "' where v.numeronf='";
    const char *end = "'";

    size_t needed = strlen(prefix) + strlen(STATUS_NFE) + strlen(middle1) + strlen(nprot_sql) +
                    strlen(middle2) + strlen(xml_sql) + strlen(suffix) + strlen(numeronf) +
                    strlen(end) + 1;

    char *sql = (char *)malloc(needed);
    if (!sql) {
        free(nprot_sql);
        free(xml_sql);
        return NULL;
    }

    snprintf(sql, needed, "%s%s%s%s%s%s%s%s%s", prefix, STATUS_NFE, middle1, nprot_sql, middle2,
             xml_sql, suffix, numeronf, end);

    free(nprot_sql);
    free(xml_sql);
    return sql;
}

static bool parse_nfe_xml(const char *xml_path, char **out_nprot, char **out_nnf, char **out_serie,
                          char **out_xml_one_line) {
    size_t xml_size = 0;
    char *xml_raw = read_file_all(xml_path, &xml_size);
    if (!xml_raw || xml_size == 0) {
        return false;
    }

    char *nprot = extract_tag_value(xml_raw, "nProt");
    char *nnf = extract_tag_value(xml_raw, "nNF");
    char *serie = extract_tag_value(xml_raw, "serie");
    char *one_line = build_one_line_xml(xml_raw);

    free(xml_raw);

    if (!nprot || !nnf || !serie || !one_line) {
        free(nprot);
        free(nnf);
        free(serie);
        free(one_line);
        return false;
    }

    *out_nprot = nprot;
    *out_nnf = nnf;
    *out_serie = serie;
    *out_xml_one_line = one_line;
    return true;
}

static bool generate_single_autoexec(const char *xml_path, const char *folder_name, const char *app_dir,
                                     char *out_target, size_t out_target_size) {
    char *nprot = NULL;
    char *nnf = NULL;
    char *serie = NULL;
    char *xml_one_line = NULL;

    if (!parse_nfe_xml(xml_path, &nprot, &nnf, &serie, &xml_one_line)) {
        return false;
    }

    char numeronf[13];
    if (!format_numeronf(nnf, serie, numeronf)) {
        free(nprot);
        free(nnf);
        free(serie);
        free(xml_one_line);
        return false;
    }

    char *sql = build_autoexec_sql(nprot, numeronf, xml_one_line);

    free(nprot);
    free(nnf);
    free(serie);
    free(xml_one_line);

    if (!sql) {
        return false;
    }

    char saida_dir[MAX_PATH_LEN];
    char folder_path[MAX_PATH_LEN];

    if (!join_path(saida_dir, sizeof(saida_dir), app_dir, "SAIDA") ||
        !join_path(folder_path, sizeof(folder_path), saida_dir, folder_name)) {
        free(sql);
        return false;
    }

    if (!ensure_directory_exists(folder_path)) {
        free(sql);
        return false;
    }

    if (!join_path(out_target, out_target_size, folder_path, "autoexec.sql")) {
        free(sql);
        return false;
    }

    bool ok = write_text_file(out_target, sql);
    free(sql);
    return ok;
}

static void process_all_xmls(const char *app_dir) {
    char xmls_dir[MAX_PATH_LEN];
    if (!join_path(xmls_dir, sizeof(xmls_dir), app_dir, "xmls")) {
        printf("Erro interno ao montar caminho da pasta xmls.\n");
        return;
    }

#if defined(_WIN32) || defined(_WIN64)
    char search_pattern[MAX_PATH_LEN];
    if (!join_path(search_pattern, sizeof(search_pattern), xmls_dir, "*.xml")) {
        printf("Erro interno ao montar pesquisa de arquivos.\n");
        return;
    }

    WIN32_FIND_DATAA find_data;
    HANDLE handle = FindFirstFileA(search_pattern, &find_data);
    if (handle == INVALID_HANDLE_VALUE) {
        printf("Nenhum arquivo XML encontrado na pasta 'xmls'.\n");
        return;
    }

    int success_count = 0;
    int error_count = 0;

    printf("Processando XMLs da pasta 'xmls'...\n");
    printf("==================================================\n");

    do {
        if (find_data.dwFileAttributes & FILE_ATTRIBUTE_DIRECTORY) {
            continue;
        }

        char xml_path[MAX_PATH_LEN];
        if (!join_path(xml_path, sizeof(xml_path), xmls_dir, find_data.cFileName)) {
            error_count++;
            continue;
        }

        char file_stem[MAX_PATH_LEN];
        strncpy(file_stem, find_data.cFileName, sizeof(file_stem) - 1);
        file_stem[sizeof(file_stem) - 1] = '\0';

        char *dot = strrchr(file_stem, '.');
        if (dot) {
            *dot = '\0';
        }

        char out_file[MAX_PATH_LEN];
        bool ok = generate_single_autoexec(xml_path, file_stem, app_dir, out_file, sizeof(out_file));

        if (ok) {
            printf("[OK] %s -> %s\n", find_data.cFileName, out_file);
            success_count++;
        } else {
            printf("[ERRO] Falha ao processar %s\n", find_data.cFileName);
            error_count++;
        }

    } while (FindNextFileA(handle, &find_data));

    FindClose(handle);
#else
    DIR *dir = opendir(xmls_dir);
    if (dir == NULL) {
        printf("Nenhum arquivo XML encontrado na pasta 'xmls'.\n");
        return;
    }

    int success_count = 0;
    int error_count = 0;

    printf("Processando XMLs da pasta 'xmls'...\n");
    printf("==================================================\n");

    struct dirent *entry;
    while ((entry = readdir(dir)) != NULL) {
        if (entry->d_name[0] == '.') {
            continue;
        }

        char *dot = strrchr(entry->d_name, '.');
        if (dot == NULL || strcmp(dot, ".xml") != 0) {
            continue;
        }

        char xml_path[MAX_PATH_LEN];
        if (!join_path(xml_path, sizeof(xml_path), xmls_dir, entry->d_name)) {
            error_count++;
            continue;
        }

        struct stat st;
        if (stat(xml_path, &st) != 0 || S_ISDIR(st.st_mode)) {
            continue;
        }

        char file_stem[MAX_PATH_LEN];
        strncpy(file_stem, entry->d_name, sizeof(file_stem) - 1);
        file_stem[sizeof(file_stem) - 1] = '\0';

        dot = strrchr(file_stem, '.');
        if (dot) {
            *dot = '\0';
        }

        char out_file[MAX_PATH_LEN];
        bool ok = generate_single_autoexec(xml_path, file_stem, app_dir, out_file, sizeof(out_file));

        if (ok) {
            printf("[OK] %s -> %s\n", entry->d_name, out_file);
            success_count++;
        } else {
            printf("[ERRO] Falha ao processar %s\n", entry->d_name);
            error_count++;
        }
    }

    closedir(dir);
#endif

    printf("==================================================\n");
    printf("Resumo: %d sucesso(s), %d erro(s)\n", success_count, error_count);
}

int main(int argc, char **argv) {
    char app_dir[MAX_PATH_LEN];
    if (!get_executable_dir(app_dir, sizeof(app_dir))) {
        fprintf(stderr, "Nao foi possivel identificar o diretorio do executavel.\n");
        return 1;
    }

    const char *xml_path = NULL;
    const char *out_folder = DEFAULT_DIRECT_FOLDER;

    for (int i = 1; i < argc; i++) {
        if (strcmp(argv[i], "--xml") == 0) {
            if (i + 1 >= argc) {
                fprintf(stderr, "Parametro --xml requer um caminho.\n");
                return 1;
            }
            xml_path = argv[++i];
        } else if (strcmp(argv[i], "--out") == 0) {
            if (i + 1 >= argc) {
                fprintf(stderr, "Parametro --out requer um nome de pasta.\n");
                return 1;
            }
            out_folder = argv[++i];
        } else if (strcmp(argv[i], "--help") == 0 || strcmp(argv[i], "-h") == 0) {
            print_usage(argv[0]);
            return 0;
        } else {
            fprintf(stderr, "Argumento desconhecido: %s\n\n", argv[i]);
            print_usage(argv[0]);
            return 1;
        }
    }

    if (xml_path) {
        char out_file[MAX_PATH_LEN];
        bool ok = generate_single_autoexec(xml_path, out_folder, app_dir, out_file, sizeof(out_file));
        if (!ok) {
            fprintf(stderr, "Erro ao gerar autoexec a partir do XML informado.\n");
            return 1;
        }

        printf("Autoexec gerado com sucesso: %s\n", out_file);
        return 0;
    }

    process_all_xmls(app_dir);
    return 0;
}
