"""Backend capability contract for document ingestion and preview."""

from __future__ import annotations

# The first MIME is the stable response type; the full tuple is accepted on upload.
MIME_TYPES: dict[str, tuple[str, ...]] = {
    ".pdf": ("application/pdf",),
    ".docx": ("application/vnd.openxmlformats-officedocument.wordprocessingml.document",),
    ".txt": ("text/plain",),
    ".md": ("text/markdown", "text/x-markdown", "text/plain"),
    ".csv": ("text/csv", "application/csv", "application/vnd.ms-excel", "text/plain"),
    ".xml": ("application/xml", "text/xml", "text/plain"),
    ".xlsx": ("application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",),
    ".xls": ("application/vnd.ms-excel",),
    ".pptx": ("application/vnd.openxmlformats-officedocument.presentationml.presentation",),
    ".html": ("text/html", "text/plain"),
    ".htm": ("text/html", "text/plain"),
    ".json": ("application/json", "text/plain"),
    ".epub": ("application/epub+zip", "application/epub", "application/zip", "application/x-zip-compressed"),
}

SUPPORTED_EXTENSIONS = frozenset(MIME_TYPES)
PREFERRED_MIME_TYPES = {extension: values[0] for extension, values in MIME_TYPES.items()}

PACKAGE_PARTS = {
    ".docx": "word/document.xml",
    ".xlsx": "xl/workbook.xml",
    ".pptx": "ppt/presentation.xml",
    ".epub": "META-INF/container.xml",
}

PREVIEW_RENDERERS = {
    "pdf": "pdf",
    "docx": "docx",
    "txt": "text",
    "md": "text",
    "csv": "csv",
    "xml": "xml",
    "xlsx": "xlsx",
    "xls": "xls",
    "pptx": "pptx",
    "html": "html",
    "htm": "html",
    "json": "json",
    "epub": "epub",
}
