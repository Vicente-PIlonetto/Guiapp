param(
    [int]$Port = 8000,
    [string]$HostAddress = "0.0.0.0"
)

$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $Root

Write-Host "== GUINAPP Windows EXE build =="
Write-Host "Porta do pacote Windows: $Port"

Get-Process -Name "GUINAPP", "GUINAPP-TUI" -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
Start-Sleep -Milliseconds 500

foreach ($path in @("build\GUINAPP", "build\GUINAPP-TUI", "dist\GUINAPP", "dist\GUINAPP-TUI")) {
    if (Test-Path $path) {
        for ($i = 0; $i -lt 5; $i++) {
            try {
                Remove-Item -LiteralPath $path -Recurse -Force -ErrorAction Stop
                break
            } catch {
                Start-Sleep -Milliseconds 500
                if ($i -eq 4) {
                    throw
                }
            }
        }
    }
}

if (-not (Get-Command python -ErrorAction SilentlyContinue)) {
    throw "Python nao encontrado no PATH."
}

if (-not (Get-Command npm -ErrorAction SilentlyContinue)) {
    throw "npm nao encontrado no PATH."
}

if (-not (Test-Path ".venv")) {
    python -m venv .venv
}

& ".\.venv\Scripts\python.exe" -m pip install --upgrade pip
& ".\.venv\Scripts\pip.exe" install -r requirements.txt
& ".\.venv\Scripts\pip.exe" install pyinstaller

Push-Location frontend
npm install
$env:VITE_API_BASE = ""
npm run build
Pop-Location

New-Item -ItemType Directory -Force -Path "modules\build" | Out-Null

if (Get-Command gcc -ErrorAction SilentlyContinue) {
    gcc "modules\Analise_xml\src\main.c" -o "modules\build\analise_xml_nfe.exe"
    gcc "modules\Analise_LogNFSE\src\main.c" -o "modules\build\analise_log_nfse.exe"
    gcc "modules\Autoexec_automation\src\generator.c" -o "modules\build\autoexec_automation.exe"
    gcc "modules\analise_xml_nfce\src\main.c" -o "modules\build\analise_xml_nfce.exe"
} else {
    Write-Warning "gcc nao encontrado. Copiando binarios Windows existentes quando disponiveis."
    Copy-Item "modules\Analise_xml\src\main.exe" "modules\build\analise_xml_nfe.exe" -Force
    Copy-Item "modules\Analise_LogNFSE\src\main.exe" "modules\build\analise_log_nfse.exe" -Force
    Copy-Item "modules\Autoexec_automation\src\generator.exe" "modules\build\autoexec_automation.exe" -Force
    Write-Warning "Nenhum binario historico para analise_xml_nfce.exe disponivel."
}

& ".\.venv\Scripts\pyinstaller.exe" `
    --noconfirm `
    --name "GUINAPP" `
    --add-data "frontend\dist;frontend\dist" `
    --add-data "modules\build;modules\build" `
    --hidden-import "multipart" `
    windows_launcher.py

& ".\.venv\Scripts\pyinstaller.exe" `
    --noconfirm `
    --name "GUINAPP-TUI" `
    tui.py

New-Item -ItemType Directory -Force -Path "dist\GUINAPP\storage\uploads" | Out-Null
New-Item -ItemType Directory -Force -Path "dist\GUINAPP\storage\processing" | Out-Null
New-Item -ItemType Directory -Force -Path "dist\GUINAPP\storage\backups" | Out-Null
New-Item -ItemType Directory -Force -Path "dist\GUINAPP\storage\results" | Out-Null
New-Item -ItemType Directory -Force -Path "dist\GUINAPP\storage\logs" | Out-Null

if (-not (Test-Path "dist\GUINAPP\config.example.env")) {
    Copy-Item "config.example.env" "dist\GUINAPP\config.example.env" -Force
}

@"
APP_HOST=$HostAddress
APP_PORT=$Port
MAX_UPLOAD_MB=200
STORAGE_DIR=storage
GFIX_BIN=firebird\gfix.exe
GBAK_BIN=firebird\gbak.exe
FIREBIRD_USER=SYSDBA
FIREBIRD_PASSWORD=masterkey
"@ | Set-Content -Path "dist\GUINAPP\.env" -Encoding UTF8

New-Item -ItemType Directory -Force -Path "dist\GUINAPP\firebird" | Out-Null
foreach ($file in @("gfix.exe", "gbak.exe", "gsec.exe", "fbclient.dll")) {
    $source = Join-Path "modules\Reparo de base" $file
    if (Test-Path $source) {
        Copy-Item $source "dist\GUINAPP\firebird\$file" -Force
    }
}

if (Test-Path "dist\GUINAPP-TUI\GUINAPP-TUI.exe") {
    Copy-Item "dist\GUINAPP-TUI\GUINAPP-TUI.exe" "dist\GUINAPP\GUINAPP-TUI.exe" -Force
}

Write-Host ""
Write-Host "Build concluido:"
Write-Host "  dist\GUINAPP\GUINAPP.exe"
Write-Host "  dist\GUINAPP\GUINAPP-TUI.exe"
Write-Host ""
Write-Host "Execute o EXE. Ele inicia o servidor local em http://$HostAddress`:$Port"
Write-Host "O navegador nao sera aberto automaticamente."
