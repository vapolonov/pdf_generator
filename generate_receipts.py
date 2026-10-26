#!/usr/bin/env python3
"""Generate receipt HTML and PDF files from HTML templates and CSV data."""

from __future__ import annotations

import argparse
import csv
import html
import json
import os
import re
import subprocess
import sys
import webbrowser
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence


EXCLUDED_DIRS = {".git", ".venv", "venv", "__pycache__", "output"}
PLACEHOLDER_RE = re.compile(r"{{\s*([\w.-]+)\s*}}")
RUSSIAN_FILE_TITLES = {
    "receipt_income": "Кассовый чек — приход",
    "receipt_expense": "Кассовый чек — расход",
    "return_income": "Возврат прихода",
    "return_expense": "Возврат расхода",
    "correction_income": "Коррекция прихода",
    "correction_expense": "Коррекция расхода",
    "invoice": "Счёт на оплату",
    "invoices": "Примеры счетов",
    "delivery_note": "Товарная накладная",
    "delivery_notes": "Примеры накладных",
    "service_act": "Акт оказанных услуг",
    "service_acts": "Примеры актов оказанных услуг",
    "expense_report": "Авансовый отчёт",
    "expense_reports": "Примеры авансовых отчётов",
    "income": "Примеры данных чеков",
}


class ReceiptError(Exception):
    """An expected, user-facing application error."""


@dataclass
class DataSet:
    headers: list[str]
    rows: list[dict[str, str]]


class FileDiscovery:
    def __init__(self, root: Path):
        self.root = root.resolve()

    def _files(self, suffixes: set[str]) -> list[Path]:
        found: list[Path] = []
        for directory, dirs, files in os.walk(self.root):
            dirs[:] = sorted(d for d in dirs if d not in EXCLUDED_DIRS)
            for filename in files:
                path = Path(directory) / filename
                if path.suffix.lower() in suffixes:
                    found.append(path)
        return sorted(found, key=lambda p: self.relative(p).casefold())

    def templates(self) -> list[Path]:
        return self._files({".html", ".htm"})

    def csv_files(self) -> list[Path]:
        return self._files({".csv"})

    def json_files(self) -> list[Path]:
        return self._files({".json"})

    def data_files(self) -> list[Path]:
        return self._files({".csv", ".json"})

    def relative(self, path: Path) -> str:
        try:
            return path.resolve().relative_to(self.root).as_posix()
        except ValueError:
            return str(path)


def extract_placeholders(template: str) -> set[str]:
    return set(PLACEHOLDER_RE.findall(template))


def load_template(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise ReceiptError(f"Не удалось прочитать HTML-шаблон: {exc}") from exc


def load_csv(path: Path) -> DataSet:
    try:
        content = path.read_text(encoding="utf-8-sig")
    except UnicodeError as exc:
        raise ReceiptError("Не удалось прочитать CSV: поддерживается кодировка UTF-8 (с BOM или без него).") from exc
    except OSError as exc:
        raise ReceiptError(f"Не удалось открыть CSV-файл: {exc}") from exc
    if not content.strip():
        raise ReceiptError("CSV-файл пуст.")
    sample = content[:8192]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",;")
    except csv.Error:
        dialect = csv.excel
    try:
        reader = csv.DictReader(content.splitlines(), dialect=dialect, restkey="__extra__")
        headers = [str(name).strip() for name in (reader.fieldnames or [])]
        if not headers or not any(headers):
            raise ReceiptError("В CSV отсутствует строка заголовков.")
        if len(set(headers)) != len(headers):
            raise ReceiptError("В CSV есть повторяющиеся названия колонок.")
        rows: list[dict[str, str]] = []
        for line_number, row in enumerate(reader, start=2):
            if row.get("__extra__"):
                raise ReceiptError(f"Строка {line_number}: найдено больше значений, чем колонок в заголовке.")
            normalized = {header: (row.get(original) or "").strip() for header, original in zip(headers, reader.fieldnames or [])}
            if any(value.strip() for value in normalized.values()):
                rows.append(normalized)
    except csv.Error as exc:
        raise ReceiptError(f"Некорректный CSV: {exc}") from exc
    if not rows:
        raise ReceiptError("В CSV нет записей для генерации.")
    return DataSet(headers, rows)


def _display_value(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def load_json(path: Path) -> DataSet:
    """Load JSON records from an array or a common records/data/items wrapper."""
    try:
        content = path.read_text(encoding="utf-8-sig")
    except UnicodeError as exc:
        raise ReceiptError("Не удалось прочитать JSON: поддерживается кодировка UTF-8 (с BOM или без него).") from exc
    except OSError as exc:
        raise ReceiptError(f"Не удалось открыть JSON-файл: {exc}") from exc
    if not content.strip():
        raise ReceiptError("JSON-файл пуст.")
    try:
        payload = json.loads(content)
    except json.JSONDecodeError as exc:
        raise ReceiptError(f"Некорректный JSON, строка {exc.lineno}, столбец {exc.colno}: {exc.msg}.") from exc

    if isinstance(payload, dict):
        records: object = payload
        for key in ("records", "data", "items", "documents"):
            if key in payload and isinstance(payload[key], list):
                records = payload[key]
                break
        if isinstance(records, dict):
            records = [records]
    else:
        records = payload
    if not isinstance(records, list) or not records:
        raise ReceiptError("JSON должен содержать объект или непустой массив объектов.")
    if any(not isinstance(record, dict) for record in records):
        raise ReceiptError("Каждая запись JSON должна быть объектом с полями и значениями.")

    headers: list[str] = []
    rows: list[dict[str, str]] = []
    for record in records:
        normalized = {str(key).strip(): _display_value(value) for key, value in record.items()}
        for key in normalized:
            if key not in headers:
                headers.append(key)
        rows.append(normalized)
    headers = [header for header in headers if header]
    if not headers:
        raise ReceiptError("В JSON-записях нет названий полей.")
    rows = [{header: row.get(header, "") for header in headers} for row in rows]
    return DataSet(headers, rows)


def load_data(path: Path) -> DataSet:
    suffix = path.suffix.casefold()
    if suffix == ".csv":
        return load_csv(path)
    if suffix == ".json":
        return load_json(path)
    raise ReceiptError("Поддерживаются только файлы CSV и JSON.")


def validate_compatibility(template: str, headers: Sequence[str]) -> set[str]:
    placeholders = extract_placeholders(template)
    if not placeholders:
        raise ReceiptError("В HTML-шаблоне не найдены плейсхолдеры вида {{название_колонки}}.")
    missing = placeholders - set(headers)
    if missing:
        raise ReceiptError("В источнике данных отсутствуют поля для плейсхолдеров: " + ", ".join(sorted(missing)))
    return placeholders


def render_html(template: str, record: dict[str, str]) -> str:
    def replace(match: re.Match[str]) -> str:
        key = match.group(1)
        if key not in record:
            raise ReceiptError(f"Не найдено значение для поля «{key}».")
        return html.escape(str(record[key]), quote=True)

    return PLACEHOLDER_RE.sub(replace, template)


def parse_selection(value: str, row_count: int) -> list[int]:
    """Parse a single number, comma list, inclusive range, A/all or R/range."""
    value = value.strip().lower()
    if value in {"a", "all"}:
        return list(range(row_count))
    if value.startswith("r"):
        value = value[1:].strip()
    if not value:
        raise ReceiptError("Введите номер записи, список, диапазон или A.")
    try:
        if "," in value:
            numbers = [int(part.strip()) for part in value.split(",")]
        elif "-" in value:
            start_text, end_text = value.split("-", 1)
            start, end = int(start_text.strip()), int(end_text.strip())
            if end < start:
                raise ReceiptError("В диапазоне конечный номер должен быть не меньше начального.")
            numbers = list(range(start, end + 1))
        else:
            numbers = [int(value)]
    except ValueError as exc:
        raise ReceiptError("Некорректный выбор. Пример: 2,4 или 1-5.") from exc
    if len(set(numbers)) != len(numbers):
        numbers = list(dict.fromkeys(numbers))
    if any(number < 1 or number > row_count for number in numbers):
        raise ReceiptError(f"Номер записи должен быть от 1 до {row_count}.")
    return [number - 1 for number in numbers]


def safe_name(value: str) -> str:
    cleaned = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", value).strip(" ._")
    cleaned = re.sub(r"_+", "_", cleaned)
    return cleaned[:100] or "receipt"


def unique_path(path: Path) -> Path:
    if not path.exists():
        return path
    index = 2
    while True:
        candidate = path.with_name(f"{path.stem}_{index}{path.suffix}")
        if not candidate.exists():
            return candidate
        index += 1


def convert_to_pdf(html_path: Path, pdf_path: Path) -> None:
    try:
        from weasyprint import HTML  # type: ignore[import-not-found]
    except (ImportError, OSError) as exc:
        raise ReceiptError(
            "WeasyPrint не установлен или недоступны его системные зависимости. "
            "Установите зависимости по инструкции в README.md. HTML-файл сохранён."
        ) from exc
    try:
        HTML(filename=str(html_path), base_url=str(html_path.parent)).write_pdf(str(pdf_path))
    except Exception as exc:  # WeasyPrint raises several platform-specific exceptions.
        raise ReceiptError(f"Не удалось создать PDF: {exc}") from exc


def generate_one(template: str, source_template: Path, record: dict[str, str], row_number: int, output: Path) -> tuple[Path, Path]:
    identifier = record.get("id", "").strip() or str(row_number)
    stem = safe_name(f"{source_template.stem}_{identifier}")
    html_path = output / f"{stem}.html"
    pdf_path = output / f"{stem}.pdf"
    suffix = 2
    while html_path.exists() or pdf_path.exists():
        html_path = output / f"{stem}_{suffix}.html"
        pdf_path = output / f"{stem}_{suffix}.pdf"
        suffix += 1
    try:
        html_path.write_text(render_html(template, record), encoding="utf-8")
    except OSError as exc:
        raise ReceiptError(f"Не удалось сохранить HTML в {output}: {exc}") from exc
    convert_to_pdf(html_path, pdf_path)
    return html_path, pdf_path


def recommend_data(template_path: Path, data_paths: Sequence[Path], discovery: FileDiscovery) -> list[Path]:
    template_parts = {p.casefold() for p in template_path.parts}
    template_stem = re.sub(r"^(receipt|return|correction)_?", "", template_path.stem.casefold())
    def score(path: Path) -> tuple[int, str]:
        common = len(template_parts & {p.casefold() for p in path.parts})
        stem = path.stem.casefold()
        matching_name = int(stem in template_stem or template_stem in stem)
        return (-(common + matching_name * 3), discovery.relative(path).casefold())
    return sorted(data_paths, key=score)


def compatible_data_paths(template: str, data_paths: Sequence[Path]) -> list[Path]:
    """Return only readable CSV/JSON sources whose fields satisfy the template."""
    compatible: list[Path] = []
    for path in data_paths:
        try:
            data = load_data(path)
            validate_compatibility(template, data.headers)
        except ReceiptError:
            continue
        compatible.append(path)
    return compatible


def display_files(title: str, paths: Sequence[Path]) -> None:
    print(f"\n{title}")
    for i, path in enumerate(paths, 1):
        russian_title = RUSSIAN_FILE_TITLES.get(path.stem.casefold())
        if russian_title is None:
            russian_title = "HTML-шаблон документа" if path.suffix.casefold() in {".html", ".htm"} else "Набор данных"
        print(f"[{i}] {russian_title} ({path.name})")


def select_file(paths: Sequence[Path], title: str, allow_refresh: bool = True) -> Path | None:
    while True:
        if not paths:
            print(f"Подходящие файлы не найдены: {title.lower()}.")
            return None
        display_files(title, paths)
        print("[0] Назад" + ("   [U] Обновить список" if allow_refresh else ""))
        answer = input("Ваш выбор: ").strip().lower()
        if answer == "0":
            return None
        if answer == "u" and allow_refresh:
            return Path("__refresh__")
        try:
            index = int(answer) - 1
            if 0 <= index < len(paths):
                return paths[index]
        except ValueError:
            pass
        print("Некорректный выбор. Введите номер из списка.")


def preview(records: list[dict[str, str]], indices: Sequence[int]) -> None:
    for index in indices:
        print(f"\n{'=' * 16} Предпросмотр записи {index + 1} {'=' * 16}")
        for key, value in records[index].items():
            print(f"{key}: {value}")
    print("\nПроверьте данные выше перед генерацией.")


def open_path(path: Path) -> None:
    try:
        if sys.platform == "win32":
            os.startfile(str(path))  # type: ignore[attr-defined]
        elif sys.platform == "darwin":
            subprocess.run(["open", str(path)], check=True)
        else:
            webbrowser.open(path.resolve().as_uri())
    except (OSError, subprocess.SubprocessError) as exc:
        print(f"Не удалось открыть файл: {exc}")


def open_generated(pdf_paths: Sequence[Path], output: Path) -> None:
    if not pdf_paths:
        return
    print("\nОткрыть PDF? [1] Первый  [2] Все  [3] Папку output  [0] Нет")
    answer = input("> ").strip()
    if answer == "1":
        open_path(pdf_paths[0])
    elif answer == "2":
        if len(pdf_paths) > 5:
            if input(f"Будет открыто файлов: {len(pdf_paths)}. Продолжить? [y/N] ").strip().lower() != "y":
                return
        for path in pdf_paths:
            open_path(path)
    elif answer == "3":
        open_path(output)


def generate_batch(template_path: Path, data_path: Path, indices: Sequence[int], root: Path, output: Path) -> tuple[list[Path], int]:
    template = load_template(template_path)
    data = load_data(data_path)
    validate_compatibility(template, data.headers)
    try:
        output.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise ReceiptError(f"Не удалось создать папку output: {exc}") from exc
    successful: list[Path] = []
    failed = 0
    for index in indices:
        try:
            _, pdf = generate_one(template, template_path, data.rows[index], index + 1, output)
            successful.append(pdf)
            print(f"Создан: {pdf}")
        except ReceiptError as exc:
            failed += 1
            print(f"Запись {index + 1}: {exc}")
    print(f"Итого: успешно — {len(successful)}, ошибок — {failed}.")
    return successful, failed


def run_interactive(root: Path, output: Path) -> int:
    discovery = FileDiscovery(root)
    print("=" * 44 + "\n      ГЕНЕРАТОР ЧЕКОВ И ДОКУМЕНТОВ\n" + "=" * 44)
    while True:
        templates = discovery.templates()
        selected_template = select_file(templates, "Доступные HTML-шаблоны:")
        if selected_template is None:
            return 0
        if selected_template.name == "__refresh__":
            continue
        try:
            template_text = load_template(selected_template)
            placeholders = validate_compatibility_header_only(template_text)
            print("Поля шаблона: " + ", ".join(sorted(placeholders)))
        except ReceiptError as exc:
            print(exc)
            continue
        while True:
            all_data_paths = discovery.data_files()
            data_paths = compatible_data_paths(template_text, all_data_paths)
            if not data_paths:
                print("Не найдено CSV/JSON-файлов, совместимых с выбранным шаблоном.")
                break
            data_paths = recommend_data(selected_template, data_paths, discovery)
            selected_data = select_file(data_paths, "Совместимые источники данных (CSV/JSON):")
            if selected_data is None:
                break
            if selected_data.name == "__refresh__":
                continue
            try:
                data = load_data(selected_data)
                validate_compatibility(template_text, data.headers)
            except ReceiptError as exc:
                print(f"Несовместимый файл: {exc}")
                continue
            while True:
                print(f"\nВыбрано: {discovery.relative(selected_data)}\nЗаписей: {len(data.rows)}")
                for i, row in enumerate(data.rows, 1):
                    summary = " | ".join(f"{key}: {value}" for key, value in list(row.items())[:4])
                    print(f"[{i}] {summary}")
                print("\nВведите номер, список (1,3), диапазон (1-5), A — все, 0 — другой источник данных.")
                selection = input("> ").strip()
                if selection == "0":
                    break
                try:
                    indices = parse_selection(selection, len(data.rows))
                except ReceiptError as exc:
                    print(exc)
                    continue
                preview(data.rows, indices)
                print("[1] Сгенерировать  [2] Выбрать записи заново  [3] Другой источник  [4] Другой шаблон  [0] Выход")
                action = input("> ").strip()
                if action == "2":
                    continue
                if action == "3":
                    break
                if action == "4":
                    return run_interactive(root, output)
                if action == "0":
                    return 0
                if action != "1":
                    print("Некорректный выбор.")
                    continue
                pdfs, _ = generate_batch(selected_template, selected_data, indices, root, output)
                open_generated(pdfs, output)
                print("\nМожно сформировать следующий чек.")
                if input("[Enter] Продолжить  [0] Выход: ").strip() == "0":
                    return 0
                break


def validate_compatibility_header_only(template: str) -> set[str]:
    placeholders = extract_placeholders(template)
    if not placeholders:
        raise ReceiptError("В HTML-шаблоне не найдены плейсхолдеры вида {{название_колонки}}.")
    return placeholders


def cli_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Генератор документов из HTML-шаблонов, CSV и JSON.")
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parent, help="Корневая папка проекта (по умолчанию — папка скрипта).")
    parser.add_argument("--output", type=Path, help="Папка для HTML и PDF (по умолчанию ROOT/output).")
    parser.add_argument("--list", action="store_true", help="Показать найденные шаблоны, CSV и JSON.")
    parser.add_argument("--template", help="Путь шаблона относительно ROOT или абсолютный путь.")
    data_group = parser.add_mutually_exclusive_group()
    data_group.add_argument("--csv", dest="data_path", help="Путь CSV относительно ROOT или абсолютный путь.")
    data_group.add_argument("--json", dest="data_path", help="Путь JSON относительно ROOT или абсолютный путь.")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--row", type=int, help="Номер записи в CSV/JSON, начиная с 1.")
    group.add_argument("--rows", help="Выбор записей, например 1,3 или 1-5.")
    group.add_argument("--all", action="store_true", help="Сгенерировать чеки для всех записей.")
    return parser


def resolve_input(value: str, root: Path) -> Path:
    path = Path(value)
    return (path if path.is_absolute() else root / path).resolve()


def main(argv: Sequence[str] | None = None) -> int:
    args = cli_parser().parse_args(argv)
    root = args.root.resolve()
    output = (args.output or root / "output").resolve()
    discovery = FileDiscovery(root)
    if args.list:
        display_files("HTML-шаблоны:", discovery.templates())
        display_files("Файлы данных (CSV/JSON):", discovery.data_files())
        return 0
    if not args.template and not args.data_path and args.row is None and args.rows is None and not args.all:
        return run_interactive(root, output)
    if not args.template or not args.data_path:
        print("Для CLI-генерации укажите --template и один источник: --csv или --json.", file=sys.stderr)
        return 2
    try:
        template_path = resolve_input(args.template, root)
        data_path = resolve_input(args.data_path, root)
        if not template_path.is_file():
            raise ReceiptError(f"Шаблон не найден: {template_path}")
        if not data_path.is_file():
            raise ReceiptError(f"Файл данных не найден: {data_path}")
        data = load_data(data_path)
        if args.all:
            indices = list(range(len(data.rows)))
        elif args.rows:
            indices = parse_selection(args.rows, len(data.rows))
        elif args.row is not None:
            indices = parse_selection(str(args.row), len(data.rows))
        else:
            raise ReceiptError("Для CLI-генерации укажите --row, --rows или --all.")
        pdfs, failed = generate_batch(template_path, data_path, indices, root, output)
        return 1 if failed or not pdfs else 0
    except ReceiptError as exc:
        print(f"Ошибка: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
