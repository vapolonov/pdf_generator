# Генератор кассовых чеков

Консольное приложение на Python 3.11+ ищет HTML-шаблоны и источники данных CSV/JSON во всех вложенных папках проекта, проверяет плейсхолдеры `{{имя_поля}}` и сохраняет отдельные HTML/PDF в `output/`. Файлы в `.git`, `.venv`, `venv`, `__pycache__` и `output` при поиске пропускаются. Шаблоны и данные можно сочетать произвольно, если поля совместимы.

## Установка

Создайте виртуальное окружение и установите Python-зависимости:

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

На Windows WeasyPrint требует Pango и связанные DLL. `pacman` — команда MSYS2: её нельзя запускать в PowerShell или обычной командной строке Windows.

1. Установите MSYS2 с настройками по умолчанию с [официального сайта](https://www.msys2.org/).
2. В меню «Пуск» откройте **MSYS2 UCRT64** (не PowerShell и не MSYS2 MSYS) и выполните там:

```bash
pacman -S mingw-w64-ucrt-x86_64-pango
```

3. После завершения установки вернитесь в PowerShell, перейдите в папку приложения, создайте виртуальное окружение и установите Python-зависимости командами выше. Если Python не находит DLL, задайте в PowerShell путь к библиотекам MSYS2 и проверьте установку:

```powershell
$env:WEASYPRINT_DLL_DIRECTORIES = 'C:\msys64\ucrt64\bin'
python -m weasyprint --info
```

На macOS:

```bash
brew install weasyprint
python3.11 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python -m weasyprint --info
```

Если macOS не находит Pango, добавьте Homebrew-библиотеки в `DYLD_FALLBACK_LIBRARY_PATH` (обычно `/opt/homebrew/lib` на Apple Silicon или `/usr/local/lib` на Intel). PDF использует системные шрифты с резервным стеком Arial/Liberation Sans/sans-serif. Для гарантированной кириллицы установите Roboto или Liberation Sans. Сам HTML-шаблон может подключать локальные CSS, изображения и шрифты относительными путями.

## Запуск

Из этой папки:

```bash
python generate_receipts.py
```

Меню позволяет обновить найденные файлы, выбрать шаблон и CSV, затем одну запись, список (`1,3`), диапазон (`1-5`) или все (`A`). Перед созданием показываются значения выбранных строк. После сохранения программа предлагает открыть первый PDF, все PDF или папку результата и остаётся доступной для следующей генерации.

```bash
python generate_receipts.py --list
python generate_receipts.py --template templates/receipts/receipt_income.html --csv test_data/receipts/income.csv --row 2
python generate_receipts.py --template templates/receipts/receipt_income.html --csv test_data/receipts/income.csv --rows 1,3
python generate_receipts.py --template templates/receipts/receipt_income.html --csv test_data/receipts/income.csv --all
python generate_receipts.py --template templates/documents/service_act.html --json test_data/documents/service_acts.json --row 1
python generate_receipts.py --template templates/documents/expense_report.html --json test_data/documents/expense_reports.json --all
```

`--row` начинается с 1 и не включает строку заголовка. Для выбора папки проекта используйте `--root PATH`, а результата — `--output PATH`. При совпадении имён добавляется суффикс `_2`, `_3` и т.д. Пакетная генерация продолжает работу после ошибки отдельной записи и сообщает итог успешных и неуспешных операций. В CLI PDF автоматически не открываются.

## Добавление шаблонов и данных

Поместите `.html`/`.htm`, `.csv` и `.json` в любую папку внутри проекта. Имена колонок CSV или ключи JSON должны совпадать с плейсхолдерами шаблона, например `{{id}}`, `{{amount}}`. Значения экранируются для HTML. CSV должен быть в UTF-8 (допускается BOM), с разделителем запятая или точка с запятой. JSON читается из массива объектов, одиночного объекта или из массива внутри поля `records`, `data`, `items` либо `documents`. Вложенные массивы и объекты JSON подставляются как компактный JSON-текст. Поле `id` используется в имени файла; если его нет, используется порядковый номер записи.

Образцы включают шаблон чека в `templates/receipts/`, шаблоны счёта, накладной, акта услуг и авансового отчёта в `templates/documents/`, а данные CSV/JSON — в `test_data/`.

## Тесты

```bash
python -m unittest discover -s tests -v
```

Интеграционный тест PDF пропускается, если WeasyPrint или системные зависимости недоступны. Тесты не запускают приложения для просмотра PDF.
