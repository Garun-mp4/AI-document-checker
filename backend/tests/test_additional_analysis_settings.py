import pytest
from pydantic import ValidationError

from app.config import Settings


@pytest.mark.parametrize("sources", [1, 8, 20])
def test_additional_analysis_source_limit_accepts_bounded_values(sources: int) -> None:
    assert Settings(_env_file=None, additional_analysis_max_sources=sources).additional_analysis_max_sources == sources


@pytest.mark.parametrize("sources", [0, 21])
def test_additional_analysis_source_limit_rejects_unbounded_values(sources: int) -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, additional_analysis_max_sources=sources)


@pytest.mark.parametrize("characters", [100, 1_200, 5_000])
def test_additional_analysis_excerpt_limit_accepts_bounded_values(characters: int) -> None:
    assert Settings(_env_file=None, additional_analysis_max_source_chars=characters).additional_analysis_max_source_chars == characters


@pytest.mark.parametrize("characters", [99, 5_001])
def test_additional_analysis_excerpt_limit_rejects_unbounded_values(characters: int) -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, additional_analysis_max_source_chars=characters)
