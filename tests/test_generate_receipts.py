import io
import tempfile
import types
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import Mock, patch

import generate_receipts as app


class FileDiscoveryTests(unittest.TestCase):
    def test_recursive_search_excludes_service_dirs_and_disambiguates_names(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as directory:
            root = Path(directory)
            for relative in ("a/same.html", "b/same.html", ".git/hidden.html", "output/old.csv", "nested/data.csv", "nested/object.json"):
                path = root / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("x", encoding="utf-8")
            finder = app.FileDiscovery(root)
            self.assertEqual(["a/same.html", "b/same.html"], [finder.relative(p) for p in finder.templates()])
            self.assertEqual(["nested/data.csv"], [finder.relative(p) for p in finder.csv_files()])
            self.assertEqual(["nested/data.csv", "nested/object.json"], [finder.relative(p) for p in finder.data_files()])

    def test_file_menu_displays_names_without_paths(self):
        paths = [Path("folder_a") / "same.html", Path("folder_b") / "other.html"]
        output = io.StringIO()
        with redirect_stdout(output):
            app.display_files("Шаблоны", paths)
        self.assertIn("[1] HTML-шаблон документа (same.html)", output.getvalue())
        self.assertIn("[2] HTML-шаблон документа (other.html)", output.getvalue())
        self.assertNotIn("folder_a", output.getvalue())
        self.assertNotIn("folder_b", output.getvalue())

    def test_known_filenames_have_russian_menu_labels(self):
        output = io.StringIO()
        with redirect_stdout(output):
            app.display_files("Документы", [Path("service_act.html"), Path("expense_reports.json")])
        self.assertIn("Акт оказанных услуг (service_act.html)", output.getvalue())
        self.assertIn("Примеры авансовых отчётов (expense_reports.json)", output.getvalue())


class CsvTests(unittest.TestCase):
    def test_reads_utf8_bom_comma_and_semicolon(self):
        for content, expected in (("\ufeffid,name\n1,Тест\n", "Тест"), ("id;name\n1;Пример\n", "Пример")):
            with self.subTest(content=content), tempfile.TemporaryDirectory(dir=Path(__file__).parent) as directory:
                path = Path(directory) / "data.csv"
                path.write_text(content, encoding="utf-8")
                self.assertEqual(expected, app.load_csv(path).rows[0]["name"])

    def test_empty_missing_header_and_extra_values(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as directory:
            path = Path(directory) / "data.csv"
            for content, expected in (("", "пуст"), ("\n", "пуст"), ("id,name\n1,a,b\n", "больше значений")):
                path.write_text(content, encoding="utf-8")
                with self.assertRaisesRegex(app.ReceiptError, expected):
                    app.load_csv(path)


class JsonTests(unittest.TestCase):
    def test_reads_utf8_bom_wrapped_records_and_scalar_values(self):
        content = '\ufeff{"records":[{"id":7,"name":"Анна","active":true,"note":null,"meta":{"x":1}}]}'
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as directory:
            path = Path(directory) / "records.json"
            path.write_text(content, encoding="utf-8")
            data = app.load_json(path)
            self.assertEqual(["id", "name", "active", "note", "meta"], data.headers)
            self.assertEqual("Анна", data.rows[0]["name"])
            self.assertEqual("true", data.rows[0]["active"])
            self.assertEqual("", data.rows[0]["note"])
            self.assertEqual('{"x":1}', data.rows[0]["meta"])

    def test_reads_json_array_single_object_and_reports_invalid_data(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as directory:
            path = Path(directory) / "records.json"
            path.write_text('[{"id":1},{"name":"Вторая"}]', encoding="utf-8")
            data = app.load_data(path)
            self.assertEqual(["id", "name"], data.headers)
            self.assertEqual("", data.rows[0]["name"])
            path.write_text('{"id":2,"name":"Одна запись"}', encoding="utf-8")
            self.assertEqual(1, len(app.load_json(path).rows))
            path.write_text('{broken', encoding="utf-8")
            with self.assertRaisesRegex(app.ReceiptError, "Некорректный JSON"):
                app.load_json(path)
            path.write_text('[1,2]', encoding="utf-8")
            with self.assertRaisesRegex(app.ReceiptError, "Каждая запись"):
                app.load_json(path)

    def test_compatible_source_filter_hides_missing_and_invalid_data(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as directory:
            root = Path(directory)
            good_csv = root / "good.csv"
            bad_json = root / "bad.json"
            broken_json = root / "broken.json"
            good_csv.write_text("id,name\n1,Тест\n", encoding="utf-8")
            bad_json.write_text('[{"id":2,"other":"x"}]', encoding="utf-8")
            broken_json.write_text("{broken", encoding="utf-8")
            compatible = app.compatible_data_paths("<p>{{id}} {{name}}</p>", [good_csv, bad_json, broken_json])
            self.assertEqual([good_csv], compatible)


class InteractiveMenuTests(unittest.TestCase):
    def test_zero_at_record_prompt_returns_to_data_sources(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as directory:
            root = Path(directory)
            (root / "templates").mkdir()
            (root / "data").mkdir()
            (root / "templates" / "example.html").write_text("<p>{{id}}</p>", encoding="utf-8")
            (root / "data" / "example.csv").write_text("id\n1\n", encoding="utf-8")
            output = io.StringIO()
            with patch("builtins.input", side_effect=["1", "1", "0", "0", "0"]), redirect_stdout(output):
                self.assertEqual(0, app.run_interactive(root, root / "output"))
            self.assertGreaterEqual(output.getvalue().count("Совместимые источники данных"), 2)


class TemplateAndSelectionTests(unittest.TestCase):
    def test_placeholders_compatibility_substitution_and_escaping(self):
        template = "<p>{{name}}</p><p>{{amount}}</p>"
        self.assertEqual({"name", "amount"}, app.extract_placeholders(template))
        app.validate_compatibility(template, ["name", "amount"])
        with self.assertRaisesRegex(app.ReceiptError, "name"):
            app.validate_compatibility(template, ["amount"])
        self.assertEqual("<p>&lt;&amp;&quot;</p><p>2</p>", app.render_html(template, {"name": '<&"', "amount": "2"}))

    def test_selection_single_multiple_range_all_and_invalid(self):
        self.assertEqual([1], app.parse_selection("2", 5))
        self.assertEqual([0, 2, 4], app.parse_selection("1,3,5", 5))
        self.assertEqual([1, 2, 3], app.parse_selection("2-4", 5))
        self.assertEqual([0, 1, 2], app.parse_selection("A", 3))
        self.assertEqual([0, 1], app.parse_selection("R 1-2", 3))
        for value in ("no", "0", "1-9", "4-2"):
            with self.subTest(value=value), self.assertRaises(app.ReceiptError):
                app.parse_selection(value, 3)

    def test_unique_and_safe_names(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as directory:
            first = Path(directory) / "receipt.pdf"
            first.touch()
            self.assertEqual("receipt_2.pdf", app.unique_path(first).name)
        self.assertEqual("unsafe_name", app.safe_name('unsafe:/name?'))

    def test_generation_avoids_existing_pdf_even_when_html_is_missing(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as directory:
            output = Path(directory)
            (output / "receipt_7.pdf").touch()
            module = types.SimpleNamespace(HTML=Mock())
            module.HTML.return_value.write_pdf = Mock()
            with patch.dict("sys.modules", {"weasyprint": module}):
                html_path, pdf_path = app.generate_one("<p>{{id}}</p>", Path("receipt.html"), {"id": "7"}, 1, output)
            self.assertEqual("receipt_7_2.html", html_path.name)
            self.assertEqual("receipt_7_2.pdf", pdf_path.name)


class PdfTests(unittest.TestCase):
    def test_conversion_reports_missing_or_converter_error(self):
        with patch.dict("sys.modules", {"weasyprint": None}):
            with self.assertRaisesRegex(app.ReceiptError, "WeasyPrint"):
                app.convert_to_pdf(Path("x.html"), Path("x.pdf"))
        module = types.SimpleNamespace(HTML=Mock(side_effect=RuntimeError("renderer failed")))
        with patch.dict("sys.modules", {"weasyprint": module}):
            with self.assertRaisesRegex(app.ReceiptError, "Не удалось создать PDF"):
                app.convert_to_pdf(Path("x.html"), Path("x.pdf"))

    def test_weasyprint_integration_when_available(self):
        try:
            from weasyprint import HTML
            HTML(string="<p>Тест</p>").write_pdf()
        except (ImportError, OSError, Exception) as exc:
            self.skipTest(f"WeasyPrint или системные зависимости недоступны: {exc}")
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as directory:
            source, target = Path(directory) / "source.html", Path(directory) / "receipt.pdf"
            source.write_text("<meta charset='utf-8'><p>Тест</p>", encoding="utf-8")
            app.convert_to_pdf(source, target)
            self.assertTrue(target.is_file())
            self.assertGreater(target.stat().st_size, 0)


if __name__ == "__main__":
    unittest.main()
