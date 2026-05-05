# Запуск основного парсера ровно на одном новом тендере (без правки .env).
# Использование: .\run_parse_one_tender.ps1

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

if (-not (Test-Path ".\venv\Scripts\Activate.ps1")) {
    Write-Error "Нет venv. Создайте: python -m venv venv"
}

. .\venv\Scripts\Activate.ps1
# --process перекрывает .env (если в .env стоит 3, всё равно возьмём 1)
python .\mian_parse_exactly_key.py --process 1
