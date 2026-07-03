#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <ctype.h>

#define MAX_PROD_NAME 256
#define MAX_ITEMS 1000

/* Estruturas de dados para organizar as informações da NFC-e/NF-e */

struct ICMS_Info {
    char orig[4];       // Origem da mercadoria (ex: 0, 1, 2...)
    char cst[4];        // CST (Regime Normal) ou CSOSN (Simples Nacional)
    double vBC;         // Valor da Base de Cálculo do ICMS
    double pICMS;       // Alíquota do ICMS (em porcentagem)
    double vICMS;       // Valor total do ICMS
};

struct PIS_Info {
    double vPIS;        // Valor total do PIS
};

struct COFINS_Info {
    double vCOFINS;     // Valor total do COFINS
};

struct ItemNota {
    int nItem;                          // Número do Item (nItem)
    char xProd[MAX_PROD_NAME];          // Nome do Produto
    double qCom;                        // Quantidade Comercial
    double vUnCom;                      // Valor Unitário Comercial
    double vProd;                       // Valor Total do Produto (qCom * vUnCom)
    double vTotTrib;                    // Valor Aproximado de Tributos do Item
    struct ICMS_Info icms;              // Informações de ICMS
    struct PIS_Info pis;                // Informações de PIS
    struct COFINS_Info cofins;          // Informações de COFINS
};

struct TotalNota {
    double vProd;       // Valor Total dos Produtos/Serviços
    double vDesc;       // Valor Total de Desconto
    double vICMS;       // Valor Total do ICMS
    double vNF;         // Valor Total da Nota Fiscal
    double vTotTrib;    // Valor Aproximado de Tributos
};

struct NotaFiscal {
    struct TotalNota total;
    struct ItemNota itens[MAX_ITEMS];
    int qtdItens;
};

/* Funções auxiliares para navegação na árvore DOM XML via busca textual */

// Encontra o início de uma tag, ignorando namespaces opcionais (ex: <ns2:prod> ou <prod>)
const char* find_tag_start_in_range(const char *start, const char *end, const char *tag) {
    const char *ptr = start;
    size_t tag_len = strlen(tag);
    while (ptr && ptr < end) {
        ptr = strchr(ptr, '<');
        if (!ptr || ptr >= end) return NULL;

        // Aponta para o caractere após '<'
        const char *t_ptr = ptr + 1;

        // Ignora prefixo de namespace (ex: "nfe:") se presente
        const char *colon = strchr(t_ptr, ':');
        const char *tag_end_char = strchr(t_ptr, '>');
        if (colon && colon < t_ptr + 15 && colon < end && (!tag_end_char || colon < tag_end_char)) {
            int valid_prefix = 1;
            for (const char *p = t_ptr; p < colon; p++) {
                if (*p == '>' || *p == ' ' || *p == '/' || *p == '<') {
                    valid_prefix = 0;
                    break;
                }
            }
            if (valid_prefix) {
                t_ptr = colon + 1;
            }
        }

        if (t_ptr + tag_len <= end && strncmp(t_ptr, tag, tag_len) == 0) {
            char next = t_ptr[tag_len];
            if (next == '>' || next == ' ' || next == '/' || next == '\r' || next == '\n' || next == '\t') {
                return ptr;
            }
        }
        ptr++;
    }
    return NULL;
}

// Obtém o início do conteúdo da tag (após o caractere '>')
const char* get_content_start(const char *tag_start) {
    if (!tag_start) return NULL;
    const char *ptr = strchr(tag_start, '>');
    if (ptr) return ptr + 1;
    return NULL;
}

// Encontra a tag de fechamento correspondente, ignorando namespaces (ex: </ns2:prod> ou </prod>)
const char* find_tag_end_in_range(const char *start, const char *end, const char *tag) {
    const char *ptr = start;
    size_t tag_len = strlen(tag);
    while (ptr && ptr < end) {
        ptr = strstr(ptr, "</");
        if (!ptr || ptr >= end) return NULL;

        const char *t_ptr = ptr + 2; // pula "</"

        // Ignora prefixo de namespace (ex: "nfe:") se presente
        const char *colon = strchr(t_ptr, ':');
        const char *tag_end_char = strchr(t_ptr, '>');
        if (colon && colon < t_ptr + 15 && colon < end && (!tag_end_char || colon < tag_end_char)) {
            int valid_prefix = 1;
            for (const char *p = t_ptr; p < colon; p++) {
                if (*p == '>' || *p == ' ' || *p == '/' || *p == '<') {
                    valid_prefix = 0;
                    break;
                }
            }
            if (valid_prefix) {
                t_ptr = colon + 1;
            }
        }

        if (t_ptr + tag_len <= end && strncmp(t_ptr, tag, tag_len) == 0) {
            char next = t_ptr[tag_len];
            if (next == '>' || next == ' ' || next == '\r' || next == '\n' || next == '\t') {
                return ptr;
            }
        }
        ptr += 2;
    }
    return NULL;
}

// Obtém o conteúdo textual de uma tag como string alocada dinamicamente, limpando espaços em branco
char* obter_conteudo_tag_in_range(const char *start, const char *end, const char *tag) {
    const char *tag_start = find_tag_start_in_range(start, end, tag);
    if (!tag_start) return NULL;
    const char *content_start = get_content_start(tag_start);
    if (!content_start || content_start >= end) return NULL;
    const char *tag_end = find_tag_end_in_range(content_start, end, tag);
    if (!tag_end || tag_end > end) return NULL;
    size_t len = tag_end - content_start;

    // Trim de espaços em branco
    while (len > 0 && isspace((unsigned char)*content_start)) {
        content_start++;
        len--;
    }
    while (len > 0 && isspace((unsigned char)content_start[len - 1])) {
        len--;
    }

    char *res = malloc(len + 1);
    if (!res) return NULL;
    memcpy(res, content_start, len);
    res[len] = '\0';
    return res;
}

// Obtém o valor real (double) de uma tag
double obter_valor_double_in_range(const char *start, const char *end, const char *tag) {
    char *str = obter_conteudo_tag_in_range(start, end, tag);
    if (!str) return 0.0;

    // Substitui vírgula por ponto para parsing correto de float
    for (char *p = str; *p; p++) {
        if (*p == ',') *p = '.';
    }

    double val = strtod(str, NULL);
    free(str);
    return val;
}

/* Funções principais de extração de dados */

// Extrai as informações de ICMS tratando as tags correspondentes
void extrair_dados_icms(const char *icms_content, const char *icms_end, struct ICMS_Info *icms) {
    memset(icms, 0, sizeof(struct ICMS_Info));

    char *orig = obter_conteudo_tag_in_range(icms_content, icms_end, "orig");
    if (orig) {
        strncpy(icms->orig, orig, sizeof(icms->orig) - 1);
        free(orig);
    }

    char *cst = obter_conteudo_tag_in_range(icms_content, icms_end, "CST");
    if (!cst) {
        cst = obter_conteudo_tag_in_range(icms_content, icms_end, "CSOSN");
    }
    if (cst) {
        strncpy(icms->cst, cst, sizeof(icms->cst) - 1);
        free(cst);
    }

    icms->vBC = obter_valor_double_in_range(icms_content, icms_end, "vBC");
    icms->pICMS = obter_valor_double_in_range(icms_content, icms_end, "pICMS");
    icms->vICMS = obter_valor_double_in_range(icms_content, icms_end, "vICMS");
}

// Extrai os totais da nota
void extrair_totais(const char *infNFe_start, const char *infNFe_end, struct NotaFiscal *nf) {
    memset(&nf->total, 0, sizeof(struct TotalNota));

    const char *total_start = find_tag_start_in_range(infNFe_start, infNFe_end, "total");
    if (total_start) {
        const char *total_content = get_content_start(total_start);
        const char *total_end = find_tag_end_in_range(total_content, infNFe_end, "total");
        if (total_end && total_end <= infNFe_end) {
            const char *icmstot_start = find_tag_start_in_range(total_content, total_end, "ICMSTot");
            if (icmstot_start) {
                const char *icmstot_content = get_content_start(icmstot_start);
                const char *icmstot_end = find_tag_end_in_range(icmstot_content, total_end, "ICMSTot");
                if (icmstot_end && icmstot_end <= total_end) {
                    nf->total.vProd = obter_valor_double_in_range(icmstot_content, icmstot_end, "vProd");
                    nf->total.vDesc = obter_valor_double_in_range(icmstot_content, icmstot_end, "vDesc");
                    nf->total.vICMS = obter_valor_double_in_range(icmstot_content, icmstot_end, "vICMS");
                    nf->total.vNF = obter_valor_double_in_range(icmstot_content, icmstot_end, "vNF");
                    nf->total.vTotTrib = obter_valor_double_in_range(icmstot_content, icmstot_end, "vTotTrib");
                }
            }
        }
    }
}

// Itera sobre as tags <det> para extrair os itens
void extrair_produtos(const char *infNFe_start, const char *infNFe_end, struct NotaFiscal *nf) {
    const char *cursor = infNFe_start;
    nf->qtdItens = 0;

    while (cursor < infNFe_end && nf->qtdItens < MAX_ITEMS) {
        const char *det_start = find_tag_start_in_range(cursor, infNFe_end, "det");
        if (!det_start) break;

        const char *content_start = get_content_start(det_start);
        if (!content_start) break;

        const char *det_end = find_tag_end_in_range(content_start, infNFe_end, "det");
        if (!det_end || det_end > infNFe_end) break;

        struct ItemNota *item = &nf->itens[nf->qtdItens];
        memset(item, 0, sizeof(struct ItemNota));

        // Extrai o atributo nItem (Número do Item) na tag de abertura (ex: <det nItem="1">)
        const char *nItem_attr = strstr(det_start, "nItem=\"");
        if (nItem_attr && nItem_attr < content_start) {
            item->nItem = atoi(nItem_attr + 7);
        } else {
            nItem_attr = strstr(det_start, "nItem=");
            if (nItem_attr && nItem_attr < content_start) {
                item->nItem = atoi(nItem_attr + 6);
            }
        }

        // Extrai dados do produto (<prod>)
        const char *prod_start = find_tag_start_in_range(content_start, det_end, "prod");
        if (prod_start) {
            const char *prod_content = get_content_start(prod_start);
            const char *prod_end = find_tag_end_in_range(prod_content, det_end, "prod");
            if (prod_end && prod_end <= det_end) {
                char *xProd = obter_conteudo_tag_in_range(prod_content, prod_end, "xProd");
                if (xProd) {
                    strncpy(item->xProd, xProd, sizeof(item->xProd) - 1);
                    free(xProd);
                }
                item->qCom = obter_valor_double_in_range(prod_content, prod_end, "qCom");
                item->vUnCom = obter_valor_double_in_range(prod_content, prod_end, "vUnCom");
                item->vProd = obter_valor_double_in_range(prod_content, prod_end, "vProd");
            }
        }

        // Extrai dados de impostos (<imposto>)
        const char *imp_start = find_tag_start_in_range(content_start, det_end, "imposto");
        if (imp_start) {
            const char *imp_content = get_content_start(imp_start);
            const char *imp_end = find_tag_end_in_range(imp_content, det_end, "imposto");
            if (imp_end && imp_end <= det_end) {
                item->vTotTrib = obter_valor_double_in_range(imp_content, imp_end, "vTotTrib");

                // ICMS
                const char *icms_start = find_tag_start_in_range(imp_content, imp_end, "ICMS");
                if (icms_start) {
                    const char *icms_content = get_content_start(icms_start);
                    const char *icms_end = find_tag_end_in_range(icms_content, imp_end, "ICMS");
                    if (icms_end && icms_end <= imp_end) {
                        extrair_dados_icms(icms_content, icms_end, &item->icms);
                    }
                }

                // PIS
                const char *pis_start = find_tag_start_in_range(imp_content, imp_end, "PIS");
                if (pis_start) {
                    const char *pis_content = get_content_start(pis_start);
                    const char *pis_end = find_tag_end_in_range(pis_content, imp_end, "PIS");
                    if (pis_end && pis_end <= imp_end) {
                        item->pis.vPIS = obter_valor_double_in_range(pis_content, pis_end, "vPIS");
                    }
                }

                // COFINS
                const char *cofins_start = find_tag_start_in_range(imp_content, imp_end, "COFINS");
                if (cofins_start) {
                    const char *cofins_content = get_content_start(cofins_start);
                    const char *cofins_end = find_tag_end_in_range(cofins_content, imp_end, "COFINS");
                    if (cofins_end && cofins_end <= imp_end) {
                        item->cofins.vCOFINS = obter_valor_double_in_range(cofins_content, cofins_end, "vCOFINS");
                    }
                }
            }
        }

        nf->qtdItens++;
        cursor = det_end + 6; // avança após </det>
    }
}

// Imprime o relatório fiscal tabular formatado
void imprimir_relatorio(const struct NotaFiscal *nf) {
    printf("=========================================================================================\n");
    printf("                               RELATORIO FISCAL DA NFC-e / NF-e                         \n");
    printf("=========================================================================================\n\n");

    printf("--- ITENS DA NOTA FISCAL ---\n");
    printf("%-4s | %-35s | %-8s | %-12s | %-12s | %-10s\n",
           "Item", "Descricao do Produto", "Qtd", "Val. Unit R$", "Val. Prod R$", "Trib. Item");
    printf("-----------------------------------------------------------------------------------------\n");

    for (int i = 0; i < nf->qtdItens; i++) {
        const struct ItemNota *item = &nf->itens[i];
        printf("%03d  | %-35.35s | %8.3f | %12.2f | %12.2f | %10.2f\n",
               item->nItem, item->xProd, item->qCom, item->vUnCom, item->vProd, item->vTotTrib);

        // Detalhes fiscais na linha inferior do produto para fins estéticos e de organização
        printf("      [ICMS] Orig: %s | CST/CSOSN: %-3s | BC: %7.2f | Aliq: %5.2f%% | ICMS: %7.2f\n",
               item->icms.orig[0] ? item->icms.orig : "-",
               item->icms.cst[0] ? item->icms.cst : "-",
               item->icms.vBC,
               item->icms.pICMS,
               item->icms.vICMS);

        printf("      [Outros] PIS: %7.2f | COFINS: %7.2f\n",
               item->pis.vPIS,
               item->cofins.vCOFINS);

        printf("-----------------------------------------------------------------------------------------\n");
    }

    printf("\n--- TOTAIS CONSOLIDADOS DA NOTA FISCAL ---\n");
    printf("  (+) Valor Total dos Produtos (vProd):     R$ %12.2f\n", nf->total.vProd);
    printf("  (-) Valor Total dos Descontos (vDesc):    R$ %12.2f\n", nf->total.vDesc);
    printf("  (=) Valor Total da Nota Fiscal (vNF):     R$ %12.2f\n", nf->total.vNF);
    printf("  -----------------------------------------------------\n");
    printf("  (*) Valor Total de ICMS (vICMS):          R$ %12.2f\n", nf->total.vICMS);
    printf("  (*) Valor Aprox. Tributos (vTotTrib):     R$ %12.2f\n", nf->total.vTotTrib);
    printf("=========================================================================================\n");
}

char* ler_arquivo_completo(const char *caminho) {
    FILE *f = fopen(caminho, "rb");
    if (!f) return NULL;
    fseek(f, 0, SEEK_END);
    long size = ftell(f);
    if (size < 0) {
        fclose(f);
        return NULL;
    }
    fseek(f, 0, SEEK_SET);
    char *buf = malloc(size + 1);
    if (!buf) {
        fclose(f);
        return NULL;
    }
    size_t read_bytes = fread(buf, 1, size, f);
    buf[read_bytes] = '\0';
    fclose(f);
    return buf;
}

int main(int argc, char **argv) {
    if (argc < 2) {
        fprintf(stderr, "Uso: %s <caminho_para_o_xml_da_nfe>\n", argv[0]);
        return 1;
    }

    const char *caminho_xml = argv[1];

    char *xml = ler_arquivo_completo(caminho_xml);
    if (!xml) {
        fprintf(stderr, "Erro: Nao foi possivel ler ou interpretar o arquivo XML: %s\n", caminho_xml);
        return 1;
    }

    // Busca a tag <infNFe> a partir da qual as informações fiscais estruturadas estão localizadas
    const char *xml_end = xml + strlen(xml);
    const char *infNFe_start = find_tag_start_in_range(xml, xml_end, "infNFe");
    if (!infNFe_start) {
        fprintf(stderr, "Erro: A tag essencial <infNFe> nao foi encontrada na NF-e/NFC-e fornecida.\n");
        free(xml);
        return 1;
    }

    const char *infNFe_content = get_content_start(infNFe_start);
    const char *infNFe_end = find_tag_end_in_range(infNFe_content, xml_end, "infNFe");
    if (!infNFe_end) {
        fprintf(stderr, "Erro: Fim da tag <infNFe> nao encontrado na NF-e/NFC-e fornecida.\n");
        free(xml);
        return 1;
    }

    struct NotaFiscal nf;
    memset(&nf, 0, sizeof(struct NotaFiscal));

    // Extrai os totais e produtos
    extrair_totais(infNFe_content, infNFe_end, &nf);
    extrair_produtos(infNFe_content, infNFe_end, &nf);

    // Exibe o relatório gerado
    imprimir_relatorio(&nf);

    free(xml);
    return 0;
}
