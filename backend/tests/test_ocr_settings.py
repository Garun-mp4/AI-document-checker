from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.config import Settings
from app.schemas import OcrReprocessIn


@pytest.mark.parametrize(
    ("language", "quality", "pages"),
    [
        ("rus", "fast", [3, 1]),
        ("eng", "high", None),
        ("rus+eng", "balanced", [2]),
    ],
)
def test_ocr_reprocess_request_accepts_supported_choices_and_sorts_pages(language, quality, pages) -> None:
    request = OcrReprocessIn(language=language, quality=quality, pages=pages)
    assert request.language == language
    assert request.quality == quality
    assert request.pages == ([1, 3] if pages == [3, 1] else pages)


@pytest.mark.parametrize(
    "payload",
    [
        {"language": "fra"},
        {"quality": "ultra"},
        {"pages": []},
        {"pages": [0]},
        {"pages": [1, 1]},
        {"pages": list(range(1, 502))},
    ],
)
def test_ocr_reprocess_request_rejects_unsupported_or_unsafe_choices(payload) -> None:
    with pytest.raises(ValidationError):
        OcrReprocessIn(**payload)


@pytest.mark.parametrize("threshold", [0, 35.5, 60, 100])
def test_ocr_confidence_warning_threshold_is_configurable(threshold: float) -> None:
    config = Settings(_env_file=None, ocr_confidence_warning_threshold=threshold)
    assert config.ocr_confidence_warning_threshold == threshold


@pytest.mark.parametrize("threshold", [-0.1, 100.1])
def test_ocr_confidence_warning_threshold_rejects_values_outside_percentage_range(threshold: float) -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, ocr_confidence_warning_threshold=threshold)


@pytest.mark.parametrize("max_pages", [1, 100, 500])
def test_ocr_max_pages_accepts_safe_range(max_pages: int) -> None:
    config = Settings(_env_file=None, ocr_max_pages=max_pages)
    assert config.ocr_max_pages == max_pages


@pytest.mark.parametrize("max_pages", [0, 501])
def test_ocr_max_pages_rejects_unsafe_limits(max_pages: int) -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, ocr_max_pages=max_pages)
