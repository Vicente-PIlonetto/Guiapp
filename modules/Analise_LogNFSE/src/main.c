#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <errno.h>

#if defined(_WIN32) || defined(_WIN64)
#include <direct.h>
#define mkdir_dir(path) _mkdir(path)
#else
#include <sys/stat.h>
#include <sys/types.h>
#define mkdir_dir(path) mkdir((path), 0755)
#endif

#define MAX_LINE 2048


static int append_safe(char **dest, size_t *cap, size_t *len, const char *src) {
    size_t add = strlen(src);
    if (*len + add + 1 > *cap) {
        size_t newcap = (*cap == 0) ? (*len + add + 1) : (*cap * 2);
        while (newcap < *len + add + 1) newcap *= 2;
        char *p = realloc(*dest, newcap);
        if (!p) return -1;
        *dest = p;
        *cap = newcap;
    }
    memcpy(*dest + *len, src, add + 1);
    *len += add;
    return 0;
}

char* read_log(const char *path){
    FILE *file = fopen(path, "r");
    if (!file) {
        fprintf(stderr, "Erro ao abrir o arquivo '%s': %s\n", path, strerror(errno));
        return NULL;
    }

    char line[MAX_LINE];
    char *result = NULL;
    size_t cap = 0;
    size_t len = 0;

    int capture = 0;

    while (fgets(line, sizeof(line), file)) {
        if (strstr(line , "Exception") || strstr(line, "Erro")){
            capture = 3;
            if (append_safe(&result, &cap, &len, "\n--- EXCECAO ENCONTRADA ---\n") < 0) goto oom;
            if (append_safe(&result, &cap, &len, line) < 0) goto oom;
        }
        else if (capture > 0){
            if (append_safe(&result, &cap, &len, line) < 0) goto oom;
            capture--;
        }
    }

    fclose(file);
    if (!result) {
        
        result = malloc(1);
        if (result) result[0] = '\0';
    }
    return result;

oom:
    fprintf(stderr, "Erro: memoria insuficiente ao ler o log\n");
    free(result);
    fclose(file);
    return NULL;
}

static int ensure_dir(const char *dir) {
    
    if (mkdir_dir(dir) == 0) return 0;
    if (errno == EEXIST || errno == EACCES) return 0;
    return -1;
}

void save_exit(const char *content){
    const char *outdir = "Saida";
    if (ensure_dir(outdir) != 0) {
        fprintf(stderr, "Aviso: nao foi possivel criar o diretorio '%s' (%s). Tentando continuar...\n", outdir, strerror(errno));
    }

    char outpath[512];
    snprintf(outpath, sizeof(outpath), "%s/resultado.txt", outdir);

    FILE *f = fopen(outpath, "w");
    if (!f) {
        fprintf(stderr, "Erro ao criar o arquivo de saida '%s': %s\n", outpath, strerror(errno));
        return;
    }

    fprintf(f, "%s", content ? content : "");
    fclose(f);
}

int main (){
    char *files = read_log("system.log");

    if (files != NULL){
        save_exit(files);
        free(files);
    }

    return 0;
}