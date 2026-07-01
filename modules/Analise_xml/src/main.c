#include <ctype.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdarg.h>
#include <errno.h>

#ifdef _WIN32
#include <direct.h>
#include <windows.h>
#define MAKE_DIR(path) _mkdir(path)
#else
#include <sys/stat.h>
#include <sys/types.h>
#include <dirent.h>
#define MAKE_DIR(path) mkdir(path, 0777)
#endif

#define ARRAY_SIZE(x) (sizeof(x) / sizeof((x)[0]))

typedef struct {
    const char *name;
    double sum;
} TagAccumulator;

typedef struct {
    const char *category_name;
    const char *block_tag;
    TagAccumulator *tags;
    size_t tag_count;
} Category;

static char *read_entire_file(const char *path, long *out_size) {
    FILE *fp = fopen(path, "rb");
    char *buffer;
    long size;

    if (fp == NULL) {
        return NULL;
    }

    if (fseek(fp, 0, SEEK_END) != 0) {
        fclose(fp);
        return NULL;
    }

    size = ftell(fp);
    if (size < 0) {
        fclose(fp);
        return NULL;
    }

    if (fseek(fp, 0, SEEK_SET) != 0) {
        fclose(fp);
        return NULL;
    }

    buffer = (char *)malloc((size_t)size + 1);
    if (buffer == NULL) {
        fclose(fp);
        return NULL;
    }

    if (fread(buffer, 1, (size_t)size, fp) != (size_t)size) {
        free(buffer);
        fclose(fp);
        return NULL;
    }

    buffer[size] = '\0';
    fclose(fp);

    if (out_size != NULL) {
        *out_size = size;
    }

    return buffer;
}

static double parse_decimal(const char *start, size_t len, int *ok) {
    char *tmp;
    char *endptr;
    double value;
    size_t i;

    *ok = 0;
    if (len == 0) {
        return 0.0;
    }

    tmp = (char *)malloc(len + 1);
    if (tmp == NULL) {
        return 0.0;
    }

    for (i = 0; i < len; ++i) {
        char c = start[i];
        tmp[i] = (c == ',') ? '.' : c;
    }
    tmp[len] = '\0';

    value = strtod(tmp, &endptr);
    while (*endptr != '\0' && isspace((unsigned char)*endptr)) {
        ++endptr;
    }

    if (endptr != tmp && *endptr == '\0') {
        *ok = 1;
    }

    free(tmp);
    return value;
}

static const char *find_tag_open(const char *from, const char *tag, const char *limit) {
    char open_tag[64];
    const char *p;

    snprintf(open_tag, sizeof(open_tag), "<%s>", tag);
    p = strstr(from, open_tag);
    if (p == NULL || (limit != NULL && p >= limit)) {
        return NULL;
    }
    return p + strlen(open_tag);
}

static const char *find_tag_close(const char *from, const char *tag, const char *limit) {
    char close_tag[64];
    const char *p;

    snprintf(close_tag, sizeof(close_tag), "</%s>", tag);
    p = strstr(from, close_tag);
    if (p == NULL || (limit != NULL && p > limit)) {
        return NULL;
    }
    return p;
}

static void accumulate_tag_in_range(const char *begin, const char *end, TagAccumulator *acc) {
    const char *cursor = begin;

    while (cursor < end) {
        const char *value_start = find_tag_open(cursor, acc->name, end);
        const char *value_end;
        int ok;
        double parsed;

        if (value_start == NULL || value_start >= end) {
            break;
        }

        value_end = find_tag_close(value_start, acc->name, end);
        if (value_end == NULL || value_end > end) {
            break;
        }

        parsed = parse_decimal(value_start, (size_t)(value_end - value_start), &ok);
        if (ok) {
            acc->sum += parsed;
        }

        cursor = value_end + 1;
    }
}

static void process_category(const char *xml, Category *category) {
    const char *cursor = xml;

    while (1) {
        const char *block_start = find_tag_open(cursor, category->block_tag, NULL);
        const char *block_end;
        size_t i;

        if (block_start == NULL) {
            break;
        }

        block_end = find_tag_close(block_start, category->block_tag, NULL);
        if (block_end == NULL) {
            break;
        }

        for (i = 0; i < category->tag_count; ++i) {
            accumulate_tag_in_range(block_start, block_end, &category->tags[i]);
        }

        cursor = block_end + 1;
    }
}

static double category_grand_total(const Category *category) {
    double total = 0.0;
    size_t i;

    for (i = 0; i < category->tag_count; ++i) {
        if (category->tags[i].name[0] == 'v') {
            total += category->tags[i].sum;
        }
    }

    return total;
}

static double get_tag_sum(const Category *category, const char *name) {
    size_t i;

    for (i = 0; i < category->tag_count; ++i) {
        if (strcmp(category->tags[i].name, name) == 0) {
            return category->tags[i].sum;
        }
    }

    return 0.0;
}

static void print_both(FILE *report, const char *fmt, ...) {
    va_list args;
    va_list copy;

    va_start(args, fmt);
    va_copy(copy, args);
    vprintf(fmt, args);
    if (report != NULL) {
        vfprintf(report, fmt, copy);
    }
    va_end(copy);
    va_end(args);
}

static void print_category_report_both(const Category *category, FILE *report) {
    size_t i;

    print_both(report, "\n=== %s (bloco <%s>) ===\n", category->category_name, category->block_tag);
    for (i = 0; i < category->tag_count; ++i) {
        if (category->tags[i].name[0] == 'v' && category->tags[i].sum != 0.0) {
            print_both(report, "%-15s : %14.2f\n", category->tags[i].name, category->tags[i].sum);
        }
    }
    print_both(report, "TOTAL %-9s : %14.2f\n", category->category_name, category_grand_total(category));
}

static void reset_categories(Category *categories, size_t category_count) {
    size_t i;
    for (i = 0; i < category_count; ++i) {
        size_t j;
        for (j = 0; j < categories[i].tag_count; ++j) {
            categories[i].tags[j].sum = 0.0;
        }
    }
}

static int ends_with_xml(const char *name) {
    size_t len = strlen(name);
    if (len < 4) {
        return 0;
    }

    return (tolower((unsigned char)name[len - 4]) == '.' &&
            tolower((unsigned char)name[len - 3]) == 'x' &&
            tolower((unsigned char)name[len - 2]) == 'm' &&
            tolower((unsigned char)name[len - 1]) == 'l');
}

static int path_is_directory(const char *path) {
#ifdef _WIN32
    DWORD attrs = GetFileAttributesA(path);
    return attrs != INVALID_FILE_ATTRIBUTES && (attrs & FILE_ATTRIBUTE_DIRECTORY);
#else
    struct stat st;
    if (stat(path, &st) != 0) {
        return 0;
    }
    return S_ISDIR(st.st_mode);
#endif
}

static int process_xml_file(
    const char *xml_path,
    Category *categories,
    size_t category_count,
    FILE *report_file,
    double *sum_impostos,
    double *sum_vtottrib,
    double *sum_vnf,
    int *count_vnf
) {
    long xml_size = 0;
    char *xml = read_entire_file(xml_path, &xml_size);
    size_t i;

    if (xml == NULL) {
        print_both(report_file, "\n[ERRO] Nao foi possivel abrir: %s\n", xml_path);
        return 0;
    }

    reset_categories(categories, category_count);

    print_both(report_file, "\n================================================\n");
    print_both(report_file, "Arquivo processado: %s\n", xml_path);
    print_both(report_file, "Tamanho: %ld bytes\n", xml_size);

    for (i = 0; i < category_count; ++i) {
        process_category(xml, &categories[i]);
    }

    for (i = 0; i < category_count; ++i) {
        print_category_report_both(&categories[i], report_file);
    }

    {
        Category *cat_icms = &categories[0];
        Category *cat_ipi = &categories[1];
        Category *cat_pis = &categories[2];
        Category *cat_cofins = &categories[3];
        Category *cat_ii = &categories[4];
        Category *cat_issqn = &categories[5];
        Category *cat_icmstot = &categories[6];
        Category *cat_difal = &categories[7];
        Category *cat_devol = &categories[8];
        Category *cat_ibscbs = &categories[9];
        Category *cat_ibscbstot = &categories[10];

        double total_impostos_itens =
            get_tag_sum(cat_icms, "vICMS") +
            get_tag_sum(cat_icms, "vICMSST") +
            get_tag_sum(cat_ipi, "vIPI") +
            get_tag_sum(cat_pis, "vPIS") +
            get_tag_sum(cat_cofins, "vCOFINS") +
            get_tag_sum(cat_ii, "vII") +
            get_tag_sum(cat_issqn, "vISSQN") +
            get_tag_sum(cat_difal, "vFCPUFDest") +
            get_tag_sum(cat_difal, "vICMSUFDest") +
            get_tag_sum(cat_difal, "vICMSUFRemet") +
            get_tag_sum(cat_devol, "vIPIDevol") +
            get_tag_sum(cat_ibscbs, "vIBS") +
            get_tag_sum(cat_ibscbs, "vCBS");

        double valor_total_nfe = get_tag_sum(cat_icmstot, "vNF");
        double total_trib_oficial = get_tag_sum(cat_icmstot, "vTotTrib");
        double total_ibs_cbs_nf =
            get_tag_sum(cat_ibscbstot, "vIBS") +
            get_tag_sum(cat_ibscbstot, "vCBS");

        *sum_impostos += total_impostos_itens;
        *sum_vtottrib += total_trib_oficial;
        if (valor_total_nfe > 0.0) {
            *sum_vnf += valor_total_nfe;
            *count_vnf += 1;
        }

        print_both(report_file, "\n================ RESUMO FINAL ================\n");
        print_both(report_file, "Total de impostos (itens)   : %14.2f\n", total_impostos_itens);
        print_both(report_file, "Total tributos (vTotTrib)   : %14.2f\n", total_trib_oficial);
        print_both(report_file, "Total IBS+CBS (NF)          : %14.2f\n", total_ibs_cbs_nf);
        if (valor_total_nfe > 0.0) {
            print_both(report_file, "Valor total da NF-e (vNF)   : %14.2f\n", valor_total_nfe);
        } else {
            print_both(report_file, "Valor total da NF-e (vNF)   : nao informado no XML\n");
        }
        print_both(report_file, "===============================================\n");
    }

    free(xml);
    return 1;
}

static int process_xmls_in_directory(
    const char *dir_path,
    Category *categories,
    size_t category_count,
    FILE *report_file,
    double *sum_impostos,
    double *sum_vtottrib,
    double *sum_vnf,
    int *count_vnf
) {
    int processed = 0;

#ifdef _WIN32
    char pattern[1024];
    WIN32_FIND_DATAA fd;
    HANDLE h;

    snprintf(pattern, sizeof(pattern), "%s\\*.xml", dir_path);
    h = FindFirstFileA(pattern, &fd);
    if (h == INVALID_HANDLE_VALUE) {
        return 0;
    }

    do {
        if (!(fd.dwFileAttributes & FILE_ATTRIBUTE_DIRECTORY)) {
            char full_path[1200];
            snprintf(full_path, sizeof(full_path), "%s\\%s", dir_path, fd.cFileName);
            if (process_xml_file(full_path, categories, category_count, report_file, sum_impostos, sum_vtottrib, sum_vnf, count_vnf)) {
                processed++;
            }
        }
    } while (FindNextFileA(h, &fd));

    FindClose(h);
#else
    DIR *dir;
    struct dirent *entry;

    dir = opendir(dir_path);
    if (dir == NULL) {
        return 0;
    }

    while ((entry = readdir(dir)) != NULL) {
        if (ends_with_xml(entry->d_name)) {
            char full_path[1200];
            snprintf(full_path, sizeof(full_path), "%s/%s", dir_path, entry->d_name);
            if (process_xml_file(full_path, categories, category_count, report_file, sum_impostos, sum_vtottrib, sum_vnf, count_vnf)) {
                processed++;
            }
        }
    }

    closedir(dir);
#endif

    return processed;
}

int main(int argc, char *argv[]) {
    FILE *report_file = NULL;
    int processed = 0;
    double sum_impostos = 0.0;
    double sum_vtottrib = 0.0;
    double sum_vnf = 0.0;
    int count_vnf = 0;
    const char *target = (argc >= 2) ? argv[1] : "xmls";

    TagAccumulator icms_tags[] = {
        {"orig", 0}, {"CST", 0}, {"CSOSN", 0}, {"modBC", 0}, {"vBC", 0}, {"pICMS", 0},
        {"vICMS", 0}, {"pRedBC", 0}, {"modBCST", 0}, {"pMVAST", 0}, {"pRedBCST", 0},
        {"vBCST", 0}, {"pICMSST", 0}, {"vICMSST", 0}, {"vBCSTRet", 0}, {"pST", 0},
        {"vICMSSTRet", 0}, {"vICMSSubstituto", 0}, {"vICMSDeson", 0}, {"motDesICMS", 0},
        {"pCredSN", 0}, {"vCredICMSSN", 0}, {"vICMSOp", 0}, {"pDif", 0}, {"vICMSDif", 0}
    };

    TagAccumulator ipi_tags[] = {
        {"cEnq", 0}, {"CST", 0}, {"vBC", 0}, {"pIPI", 0}, {"qUnid", 0}, {"vUnid", 0}, {"vIPI", 0}
    };

    TagAccumulator pis_tags[] = {
        {"CST", 0}, {"vBC", 0}, {"pPIS", 0}, {"vPIS", 0}, {"qBCProd", 0}, {"vAliqProd", 0}
    };

    TagAccumulator cofins_tags[] = {
        {"CST", 0}, {"vBC", 0}, {"pCOFINS", 0}, {"vCOFINS", 0}, {"qBCProd", 0}, {"vAliqProd", 0}
    };

    TagAccumulator ii_tags[] = {
        {"vBC", 0}, {"vDespAdu", 0}, {"vII", 0}, {"vIOF", 0}
    };

    TagAccumulator issqn_tags[] = {
        {"vBC", 0}, {"vAliq", 0}, {"vISSQN", 0}, {"cMunFG", 0}, {"cListServ", 0},
        {"vDeducao", 0}, {"vOutro", 0}, {"vDescIncond", 0}, {"vDescCond", 0},
        {"vISSRet", 0}, {"indISS", 0}, {"cServico", 0}, {"cMun", 0}, {"cPais", 0},
        {"nProcesso", 0}, {"indIncentivo", 0}
    };

    TagAccumulator icmstot_tags[] = {
        {"vBC", 0}, {"vICMS", 0}, {"vICMSDeson", 0}, {"vFCPUFDest", 0}, {"vICMSUFDest", 0},
        {"vICMSUFRemet", 0}, {"vFCP", 0}, {"vBCST", 0}, {"vST", 0}, {"vFCPST", 0},
        {"vFCPSTRet", 0}, {"vProd", 0}, {"vFrete", 0}, {"vSeg", 0}, {"vDesc", 0},
        {"vII", 0}, {"vIPI", 0}, {"vIPIDevol", 0}, {"vPIS", 0}, {"vCOFINS", 0},
        {"vOutro", 0}, {"vNF", 0}, {"vTotTrib", 0}
    };

    TagAccumulator difal_tags[] = {
        {"vBCUFDest", 0}, {"vBCFCPUFDest", 0}, {"pFCPUFDest", 0}, {"pICMSUFDest", 0},
        {"pICMSInter", 0}, {"pICMSInterPart", 0}, {"vFCPUFDest", 0}, {"vICMSUFDest", 0},
        {"vICMSUFRemet", 0}
    };

    TagAccumulator devolucao_tags[] = {
        {"pDevol", 0}, {"vIPIDevol", 0}
    };

    TagAccumulator ibscbs_tags[] = {
        {"vBC", 0}, {"vIBSUF", 0}, {"vIBSMun", 0}, {"vIBS", 0}, {"vCBS", 0}
    };

    TagAccumulator ibscbstot_tags[] = {
        {"vBCIBSCBS", 0}, {"vIBSUF", 0}, {"vIBSMun", 0}, {"vIBS", 0}, {"vCBS", 0},
        {"vNFTot", 0}
    };

    Category categories[] = {
        {"ICMS", "ICMS", icms_tags, ARRAY_SIZE(icms_tags)},
        {"IPI", "IPI", ipi_tags, ARRAY_SIZE(ipi_tags)},
        {"PIS", "PIS", pis_tags, ARRAY_SIZE(pis_tags)},
        {"COFINS", "COFINS", cofins_tags, ARRAY_SIZE(cofins_tags)},
        {"II", "II", ii_tags, ARRAY_SIZE(ii_tags)},
        {"ISSQN", "ISSQN", issqn_tags, ARRAY_SIZE(issqn_tags)},
        {"ICMSTot", "ICMSTot", icmstot_tags, ARRAY_SIZE(icmstot_tags)},
        {"ICMSUFDest", "ICMSUFDest", difal_tags, ARRAY_SIZE(difal_tags)},
        {"impostoDevol", "impostoDevol", devolucao_tags, ARRAY_SIZE(devolucao_tags)},
        {"IBSCBS", "IBSCBS", ibscbs_tags, ARRAY_SIZE(ibscbs_tags)},
        {"IBSCBSTot", "IBSCBSTot", ibscbstot_tags, ARRAY_SIZE(ibscbstot_tags)}
    };

    if (MAKE_DIR("SAIDA") != 0 && errno != EEXIST) {
        fprintf(stderr, "Erro ao criar pasta SAIDA\n");
        return 1;
    }

    report_file = fopen("SAIDA/relatorio.txt", "w");
    if (report_file == NULL) {
        fprintf(stderr, "Erro ao criar arquivo SAIDA/relatorio.txt\n");
        return 1;
    }

    print_both(report_file, "Relatorio consolidado de XML NF-e\n");
    print_both(report_file, "Entrada: %s\n", target);

    if (path_is_directory(target) || !ends_with_xml(target)) {
        processed = process_xmls_in_directory(
            target,
            categories,
            ARRAY_SIZE(categories),
            report_file,
            &sum_impostos,
            &sum_vtottrib,
            &sum_vnf,
            &count_vnf
        );
    } else {
        processed = process_xml_file(
            target,
            categories,
            ARRAY_SIZE(categories),
            report_file,
            &sum_impostos,
            &sum_vtottrib,
            &sum_vnf,
            &count_vnf
        );
    }

    print_both(report_file, "\n############ RESUMO CONSOLIDADO ############\n");
    print_both(report_file, "Arquivos processados         : %14d\n", processed);
    print_both(report_file, "Soma impostos (itens)        : %14.2f\n", sum_impostos);
    print_both(report_file, "Soma tributos (vTotTrib)     : %14.2f\n", sum_vtottrib);
    if (count_vnf > 0) {
        print_both(report_file, "Soma valor NF-e (vNF)        : %14.2f\n", sum_vnf);
    } else {
        print_both(report_file, "Soma valor NF-e (vNF)        : nao informado\n");
    }
    print_both(report_file, "#############################################\n");

    if (processed == 0) {
        print_both(report_file, "\nNenhum XML processado. Informe um arquivo .xml ou uma pasta com XMLs, por exemplo 'xmls'.\n");
    }

    fclose(report_file);
    printf("\nRelatorio salvo em: SAIDA/relatorio.txt\n");
    return 0;
}
