from __future__ import annotations

import zipfile
from io import BytesIO
from pathlib import Path

import pytest
from openpyxl import Workbook
from openpyxl.worksheet.formula import ArrayFormula
from pydantic import ValidationError

from app.schemas import TableFilterIn
from app.services.numeric_values import parse_decimal
from app.services.parsing import DocumentParsingError, parse_document
from app.services.table_analysis import calculate_table, query_table


def _xlsx_with_formula_cache(*, cached: bool = True, cached_value: str = "1200.25") -> bytes:
    workbook = Workbook()
    sheet = workbook.active
    sheet.append(["Товар", "Цена", "Итог"])
    sheet.append(["A", "1 200,25", "=SUM(B2:B2)"])
    stream = BytesIO()
    workbook.save(stream)
    workbook.close()

    converted = BytesIO()
    with zipfile.ZipFile(BytesIO(stream.getvalue())) as source, zipfile.ZipFile(converted, "w") as target:
        for entry in source.infolist():
            content = source.read(entry.filename)
            if entry.filename == "xl/worksheets/sheet1.xml":
                xml = content.decode("utf-8")
                if cached:
                    xml = xml.replace("<f>SUM(B2:B2)</f><v></v>", f"<f>SUM(B2:B2)</f><v>{cached_value}</v>")
                content = xml.encode("utf-8")
            target.writestr(entry, content)
    return converted.getvalue()


def test_decimal_parser_uses_documented_localized_rules() -> None:
    assert parse_decimal("1 234,50 ₽") == parse_decimal("1234.50")
    assert parse_decimal("1.234,50") == parse_decimal("1,234.50")
    assert parse_decimal("1,234") == parse_decimal("1.234")
    assert parse_decimal("1,234,567") == parse_decimal("1234567")
    assert parse_decimal("1,23.456") is None
    assert parse_decimal("12 pcs") is None


def test_table_filter_rejects_out_of_range_columns() -> None:
    with pytest.raises(ValidationError):
        TableFilterIn(column_index=-1, kind="text", operator="contains", value="test")
    with pytest.raises(ValidationError):
        TableFilterIn(column_index=500, kind="text", operator="contains", value="test")
    with pytest.raises(DocumentParsingError, match="столбец фильтра не найден"):
        query_table(
            b"Name,Value\nAlpha,10\n",
            "csv",
            filter_column=-1,
            filter_kind="text",
            filter_operator="contains",
            filter_value="Alpha",
        )


def test_csv_filter_and_sort_run_over_all_rows_and_keep_original_numbers() -> None:
    data = ("Проект;Сумма;Дата\n" + "".join(
        f"Проект {index};{index},50;{2026 - index // 365:04d}-01-01\n" for index in range(1, 241)
    )).encode("utf-8")

    filtered = query_table(
        data,
        "csv",
        offset=0,
        limit=10,
        filter_column=0,
        filter_kind="text",
        filter_operator="equals",
        filter_value="ПРОЕКТ 237",
    )
    assert filtered["filtered_rows"] == 1
    assert filtered["rows"][0]["number"] == 238
    assert filtered["rows"][0]["cells"][0] == "Проект 237"

    sorted_page = query_table(data, "csv", limit=3, sort_column=1, sort_direction="desc")
    assert [(row["number"], row["cells"][0]) for row in sorted_page["rows"]] == [
        (241, "Проект 240"), (240, "Проект 239"), (239, "Проект 238"),
    ]
    assert sorted_page["offset"] == 0
    assert sorted_page["total_rows"] == 240


def test_csv_query_preserves_quoted_delimiters_and_cp1251_values() -> None:
    quoted = query_table(
        'Название;Комментарий;Сумма\n"Проект; Альфа";"Текст, с запятой";"1 234,50"\n'.encode(),
        "csv",
        filter_column=0,
        filter_kind="text",
        filter_operator="contains",
        filter_value="АЛЬФА",
    )
    assert quoted["delimiter"] == ";"
    assert quoted["rows"][0]["number"] == 2
    assert quoted["rows"][0]["cells"] == ["Проект; Альфа", "Текст, с запятой", "1 234,50"]

    cp1251 = query_table(
        "Название;Сумма\nТест;1 234,50\n".encode("cp1251"),
        "csv",
        filter_column=1,
        filter_kind="number",
        filter_operator="gte",
        filter_value="1200",
    )
    assert cp1251["rows"][0]["cells"] == ["Тест", "1 234,50"]


def test_csv_blank_records_do_not_shift_physical_row_numbers() -> None:
    table = query_table(b"Name,Value\nA,1\n\nB,2\n", "csv", limit=10)

    assert table["total_rows"] == 3
    assert [row["number"] for row in table["rows"]] == [2, 3, 4]
    assert table["rows"][1]["cells"] == ["", ""]
    parsed = parse_document("blank-rows.csv", b"Name,Value\nA,1\n\nB,2\n")
    source = next(block for block in parsed.blocks if "Name: B" in block.text)
    assert source.locator["row_start"] <= 4 <= source.locator["row_end"]


def test_numeric_and_empty_filters_preserve_source_rows_and_focus_sorted_results() -> None:
    data = "Название;Сумма\nA;1 234,50\nB;12,5\nC;\nD;не число\n".encode()

    numeric = query_table(
        data,
        "csv",
        filter_column=1,
        filter_kind="number",
        filter_operator="gte",
        filter_value="1000",
    )
    assert numeric["filtered_rows"] == 1
    assert numeric["rows"][0]["number"] == 2
    assert numeric["rows"][0]["cells"][0] == "A"

    empty = query_table(
        data,
        "csv",
        filter_column=1,
        filter_kind="empty",
        filter_operator="is_empty",
    )
    assert [(row["number"], row["cells"][0]) for row in empty["rows"]] == [(4, "C")]

    focused = query_table(data, "csv", limit=2, sort_column=1, sort_direction="desc", focus_row=3)
    assert focused["focus_row_visible"] is True
    assert any(row["number"] == 3 for row in focused["rows"])


def test_date_sort_uses_date_values_not_lexical_order() -> None:
    data = "Событие;Дата\nA;05.01.2025\nB;2024-12-31\nC;10.02.2024\n".encode()

    table = query_table(data, "csv", limit=10, sort_column=1)

    assert [row["number"] for row in table["rows"]] == [4, 3, 2]
    assert table["column_kinds"] == ["text", "date"]


def test_formula_view_shows_cached_result_without_running_formula() -> None:
    table = query_table(_xlsx_with_formula_cache(), "xlsx", limit=10)
    assert table["formula_policy"] == "detected"
    formula_row = next(row for row in table["rows"] if row["number"] == 2)
    assert formula_row["formula_cells"] == [{
        "column_index": 2,
        "formula": "=SUM(B2:B2)",
        "has_cached_value": True,
    }]
    assert formula_row["cells"][2] == "1200.25"


def test_formula_view_preserves_zero_cached_result() -> None:
    table = query_table(_xlsx_with_formula_cache(cached_value="0"), "xlsx", limit=10)

    formula_row = next(row for row in table["rows"] if row["number"] == 2)
    assert formula_row["cells"][2] == "0"
    assert formula_row["formula_cells"][0]["has_cached_value"] is True


def test_formula_view_exposes_array_formula_without_evaluation() -> None:
    workbook = Workbook()
    sheet = workbook.active
    sheet.append(["Input", "Result"])
    sheet.append([2, None])
    sheet["B2"] = ArrayFormula(ref="B2:B2", text="=A2*2")
    stream = BytesIO()
    workbook.save(stream)
    workbook.close()

    table = query_table(stream.getvalue(), "xlsx", limit=10)
    row = next(row for row in table["rows"] if row["number"] == 2)

    assert row["formula_cells"] == [{
        "column_index": 1,
        "formula": "=A2*2",
        "has_cached_value": False,
    }]
    assert row["cells"][1] == ""


def test_uncached_formula_is_not_mistaken_for_an_empty_cell_or_calculated() -> None:
    data = _xlsx_with_formula_cache(cached=False)
    table = query_table(data, "xlsx", limit=10, filter_column=2, filter_kind="empty", filter_operator="is_empty")
    assert table["filtered_rows"] == 0
    formula_row = query_table(data, "xlsx", limit=10)["rows"][0]
    assert formula_row["formula_cells"][0]["has_cached_value"] is False
    assert formula_row["cells"][2] == ""

    metrics = calculate_table(data, "xlsx", sheet=None, column_index=2)
    assert metrics["document"]["formula_count"] == 1
    assert metrics["document"]["formula_cache_missing_count"] == 1
    assert metrics["document"]["numeric_count"] == 0


def test_xls_table_query_sorts_and_filters_cached_source_rows() -> None:
    fixture = Path(__file__).parent / "fixtures" / "sample.xls"
    table = query_table(fixture.read_bytes(), "xls", limit=10, sort_column=1, sort_direction="desc")

    assert table["formula_policy"] == "cached_values_only"
    assert table["available_sheets"] == ["Данные"]
    assert [row["number"] for row in table["rows"]] == [3, 2]
    filtered = query_table(
        fixture.read_bytes(), "xls", limit=10,
        filter_column=0, filter_kind="text", filter_operator="equals", filter_value="Бета",
    )
    assert [(row["number"], row["cells"][0]) for row in filtered["rows"]] == [(3, "Бета")]


def test_calculations_use_decimal_and_compare_full_table_with_current_filter() -> None:
    data = "Товар;Сумма\nA;0,10\nB;0,20\nC;не число\nD;\n".encode()

    result = calculate_table(
        data,
        "csv",
        sheet=None,
        column_index=1,
        filter_spec={"column_index": 0, "kind": "text", "operator": "contains", "value": "B"},
    )

    assert result["document"] == {
        "count": 4,
        "non_empty_count": 3,
        "numeric_count": 2,
        "nonnumeric_count": 1,
        "formula_count": 0,
        "formula_cache_missing_count": 0,
        "sum": "0.30",
        "average": "0.15",
        "minimum": "0.10",
        "maximum": "0.20",
        "scope": "document",
        "source_row_count": 4,
        "source_row_start": 2,
        "source_row_end": 5,
    }
    assert result["filtered"] == {
        "count": 1,
        "non_empty_count": 1,
        "numeric_count": 1,
        "nonnumeric_count": 0,
        "formula_count": 0,
        "formula_cache_missing_count": 0,
        "sum": "0.20",
        "average": "0.20",
        "minimum": "0.20",
        "maximum": "0.20",
        "scope": "current_filter",
        "source_row_count": 1,
        "source_row_start": 3,
        "source_row_end": 3,
    }
    assert "ROUND_HALF_UP" in result["rounding_rule"]
