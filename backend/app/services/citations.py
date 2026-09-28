from __future__ import annotations

import re

SOURCE_MARKER = re.compile(r"\[(S\d{2})\]")


def format_source_markers(text: str, valid_labels: list[str]) -> str:
    positions = {label: index for index, label in enumerate(dict.fromkeys(valid_labels), start=1)}

    def replace(match: re.Match[str]) -> str:
        position = positions.get(match.group(1))
        return f"〔{position}〕" if position is not None else ""

    return SOURCE_MARKER.sub(replace, text)
