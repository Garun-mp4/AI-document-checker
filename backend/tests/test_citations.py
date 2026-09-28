from app.services.citations import format_source_markers


def test_source_markers_become_ordered_ui_citations() -> None:
    answer = "Факт из второго источника [S02], затем из первого [S01], снова [S02]."

    assert format_source_markers(answer, ["S02", "S01"]) == "Факт из второго источника 〔1〕, затем из первого 〔2〕, снова 〔1〕."


def test_invalid_model_source_markers_are_removed() -> None:
    assert format_source_markers("Неподтверждённое [S99] утверждение [S01].", ["S01"]) == "Неподтверждённое  утверждение 〔1〕."
