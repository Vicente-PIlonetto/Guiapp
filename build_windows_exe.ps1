$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $Root

Write-Host "== GUINAPP Windows EXE build =="

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
} else {
    Write-Warning "gcc nao encontrado. Copiando binarios Windows existentes quando disponiveis."
    Copy-Item "modules\Analise_xml\src\main.exe" "modules\build\analise_xml_nfe.exe" -Force
    Copy-Item "modules\Analise_LogNFSE\src\main.exe" "modules\build\analise_log_nfse.exe" -Force
    Copy-Item "modules\Autoexec_automation\src\generator.exe" "modules\build\autoexec_automation.exe" -Force
}

& ".\.venv\Scripts\pyinstaller.exe" `
    --noconfirm `
    --clean `
    --name "GUINAPP" `
    --add-data "frontend\dist;frontend\dist" `
    --add-data "modules\build;modules\build" `
    --hidden-import "multipart" `
    windows_launcher.py

New-Item -ItemType Directory -Force -Path "dist\GUINAPP\storage\uploads" | Out-Null
New-Item -ItemType Directory -Force -Path "dist\GUINAPP\storage\processing" | Out-Null
New-Item -ItemType Directory -Force -Path "dist\GUINAPP\storage\backups" | Out-Null
New-Item -ItemType Directory -Force -Path "dist\GUINAPP\storage\results" | Out-Null
New-Item -ItemType Directory -Force -Path "dist\GUINAPP\storage\logs" | Out-Null

if (-not (Test-Path "dist\GUINAPP\config.example.env")) {
    Copy-Item "config.example.env" "dist\GUINAPP\config.example.env" -Force
}

New-Item -ItemType Directory -Force -Path "dist\GUINAPP\firebird" | Out-Null
foreach ($file in @("gfix.exe", "gbak.exe", "gsec.exe", "fbclient.dll")) {
    $source = Join-Path "modules\Reparo de base" $file
    if (Test-Path $source) {
        Copy-Item $source "dist\GUINAPP\firebird\$file" -Force
    }
}

Write-Host ""
Write-Host "Build concluido:"
Write-Host "  dist\GUINAPP\GUINAPP.exe"
Write-Host ""
Write-Host "Execute o EXE. Ele inicia o servidor local e abre o navegador."
