# GUINAPP

Aplicacao web modular para analise, validacao, correcao e reparo de arquivos. O frontend usa React/Vite e o backend usa Python/FastAPI. Os codigos existentes em `modules/` foram preservados e sao executados por runners controlados.

## Estrutura

- `frontend/`: interface React com tema escuro, upload por clique ou drag and drop, status de jobs, logs resumidos e downloads.
- `backend/`: API FastAPI, registro de modulos, controle de jobs, upload seguro e runners.
- `modules/`: codigos C existentes, binarios Windows historicos e `Makefile` Linux.
- `storage/`: uploads, processamento, backups, resultados e logs.
- `tui.py`: menu simples para operar no servidor Linux.

## Modulos

- `analise-xml-nfe`: compila `modules/Analise_xml/src/main.c`, aceita `.xml` e gera `relatorio.txt`.
- `analise-log-nfse`: compila `modules/Analise_LogNFSE/src/main.c`, aceita `.log`/`.txt` e gera `resultado.txt`.
- `autoexec-automation`: compila `modules/Autoexec_automation/src/generator.c`, aceita `.xml` e gera `autoexec.sql`.
- `reparo-base-firebird`: usa `gfix` e `gbak` no Linux, aceita `.fdb`, cria backup e repara apenas uma copia.
- `analise-xml-nfce`: registrado como indisponivel porque `src/main.c` esta vazio.

## Instalar

```bash
cp config.example.env .env
python -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
cd frontend
npm install
cd ..
```

Dependencias Linux esperadas:

```bash
sudo apt install build-essential make nodejs npm
sudo apt install firebird-utils
```

O nome do pacote Firebird pode variar conforme a distribuicao. Ajuste `GFIX_BIN` e `GBAK_BIN` no `.env` se necessario.

Instalacao geral automatizada em Linux:

```bash
chmod +x setup_linux.sh
./setup_linux.sh
```

O script tenta detectar `apt`, `dnf`, `yum`, `pacman` ou `apk`, instala dependencias base, prepara Python, compila os modulos C e gera o build do frontend.

Se voce ja instalou os pacotes do sistema:

```bash
SKIP_SYSTEM_DEPS=1 ./setup_linux.sh
```

## Compilar Modulos C

```bash
make modules
```

Os binarios Linux sao gerados em `modules/build/`. Os `.exe` existentes sao historicos do Windows e nao sao usados no servidor Linux.

## Iniciar

Backend:

```bash
python -m uvicorn backend.main:app --host 0.0.0.0 --port 8000
```

Frontend em desenvolvimento:

```bash
cd frontend
npm run dev -- --host 0.0.0.0
```

TUI:

```bash
python tui.py
```

A TUI permite iniciar, parar, reiniciar, ver status, configurar porta/storage, ver logs, compilar modulos C e checar dependencias.

## Gerar EXE para Windows

No Windows, execute:

```powershell
powershell -ExecutionPolicy Bypass -File .\build_windows_exe.ps1
```

Para gerar em uma porta especifica e permitir acesso por outros PCs na rede:

```powershell
powershell -ExecutionPolicy Bypass -File .\build_windows_exe.ps1 -Port 8010 -HostAddress 0.0.0.0
```

O pacote fica em:

```text
dist\GUINAPP\GUINAPP.exe
```

Ao abrir o EXE, ele inicia o servidor local e abre o navegador. O frontend React buildado e os binarios C ficam empacotados junto da aplicacao. As ferramentas Firebird Windows encontradas em `modules/Reparo de base` sao copiadas para `dist\GUINAPP\firebird`.

Em outro PC da rede, acesse pelo IP do servidor:

```text
http://IP_DO_SERVIDOR:8010
```

No pacote Windows atual, o navegador nao abre automaticamente; use `GUINAPP-TUI.exe` para iniciar/parar e ver a URL.

## Tailscale Funnel

A aplicacao aceita porta configuravel por `APP_PORT`. Depois de iniciar o backend na porta desejada:

```bash
tailscale funnel 8000
```

O Tailscale nao e configurado automaticamente pelo projeto.

## Reparo Firebird

O arquivo `modules/Reparo de base/start.bat` foi mantido como referencia historica Windows. No Linux, o reparo e feito pelo backend:

1. O upload `.fdb` e salvo em `storage/uploads/<job_id>/`.
2. Uma copia de processamento e criada em `storage/processing/<job_id>/`.
3. Um backup do original e criado em `storage/backups/<job_id>/`.
4. `gfix` valida e tenta reparar a copia.
5. `gbak` gera backup e restaura em novo `.fdb`.
6. A base reparada fica em `storage/results/<job_id>/repaired.fdb`.

O arquivo original enviado nunca e alterado diretamente.
