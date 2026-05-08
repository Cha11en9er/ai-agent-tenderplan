# Запуск основного парсера на K новых тендерах за один прогон (одно окно Chromium).
# Пример: .\run_parse_n_tenders.ps1 -N 5

param(
    [Parameter(Mandatory = $false)]
    [ValidateRange(1, 999)]
    [int]$N = 5
)

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

if (-not (Test-Path ".\venv\Scripts\Activate.ps1")) {
    Write-Error "Нет venv. Создайте: python -m venv venv"
}

. .\venv\Scripts\Activate.ps1
python .\mian_parse_exactly_key.py --process $N
