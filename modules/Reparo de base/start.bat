@echo off
setlocal

set "SCRIPT_DIR=%~dp0..\..\scripts"
set "SCRIPT_PATH=%SCRIPT_DIR%\repair-base.mjs"
set "DB_PATH=%~dp0SMALL.FDB"

if not exist "%DB_PATH%" (
    echo ERRO: coloque o arquivo SMALL.FDB na mesma pasta do start.bat.
    pause
    exit /b 1
)

node "%SCRIPT_PATH%" "%DB_PATH%" "%~dp0runs\bat-%RANDOM%"
set "EXIT_CODE=%ERRORLEVEL%"

if not "%EXIT_CODE%"=="0" (
    echo.
    echo O reparo falhou. Consulte a saida acima.
)

pause
exit /b %EXIT_CODE%