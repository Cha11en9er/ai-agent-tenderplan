# ai-agent-tenderplan
агент для анализа тендеров по критериям

## Установка окружения (venv)

### 1) Создать виртуальное окружение
```bash
python -m venv .venv
```

### 2) Активировать venv (Windows PowerShell)
```powershell
.\.venv\Scripts\Activate.ps1
```

Если PowerShell блокирует выполнение скриптов:
```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.\.venv\Scripts\Activate.ps1
```

### 3) Установить нужные библиотеки
```bash
pip install --upgrade pip
pip install playwright python-dotenv
```

### 4) Установить браузер для Playwright
```bash
python -m playwright install chromium
```

### 5) Проверка
```bash
python --version
pip --version
python -m playwright --version
```

## Быстрый старт

1. Создайте `.env` с переменными:
   - `LOGIN=...`
   - `PASSWORD=...`
   - `TARGET_KEY_TEXT=ремонт маленький` (или любое другое название ключа)
2. Запустите скрипт:
```bash
python mian_parse_exactly_key.py
```
