from __future__ import annotations

from uuid import uuid4

from app.services.analysis import _csv_metrics, questions_for


def test_general_document_has_seven_distinct_default_questions() -> None:
    questions = questions_for("docx")

    assert len(questions) == 7
    assert len({question["key"] for question in questions}) == 7
    assert {"overview", "purpose", "people", "resources", "decisions", "timeline", "gaps"} == {
        question["key"] for question in questions
    }


def test_csv_has_seven_table_specific_questions_and_computes_metrics_locally() -> None:
    questions = questions_for("csv")
    hours_id = uuid4()
    cost_id = uuid4()
    table_id = uuid4()
    metadata = {
        "row_count": 3,
        "column_count": 3,
        "numeric_columns": [
            {"name": "Часы", "count": 3, "sum": "60", "average": "20", "minimum": "10", "maximum": "30"},
            {"name": "Стоимость", "count": 3, "sum": "300.00", "average": "100.00", "minimum": "80.25", "maximum": "125.25"},
        ],
    }

    assert len(questions) == 7
    answer, citations = _csv_metrics(metadata, {"Часы": hours_id, "Стоимость": cost_id, "__table__": table_id})

    assert "3 строк данных и 3 столбцов" in answer
    assert "сумма 60; среднее 20" in answer
    assert "сумма 300,00; среднее 100,00" in answer
    assert citations == [str(hours_id), str(cost_id)]


def test_csv_metrics_without_numeric_columns_cite_the_table_summary() -> None:
    answer, citations = _csv_metrics(
        {"row_count": 2, "column_count": 1, "numeric_columns": []},
        {"__table__": uuid4()},
    )

    assert "Числовых столбцов" in answer
    assert len(citations) == 1
