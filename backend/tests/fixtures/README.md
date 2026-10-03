# Synthetic test documents

These generated documents contain invented project details and no personal or university data. The positive parser and E2E matrix covers all 13 advertised extensions: PDF, DOCX, TXT, MD, CSV, XML, XLSX, XLS, PPTX, HTML, HTM, JSON, and EPUB. HTML and HTM use equivalent safe source content but are tested as separate upload extensions. `sample.csv` uses a UTF-8 BOM and semicolon delimiter and has exact expected aggregates: 3 rows, 3 numeric values per column, `Часы` sum 60 / average 20, and `Стоимость` sum 300 / average 100.

Regenerate backend unit fixtures from the repository root with:

```powershell
python backend/tests/fixtures/generate_fixtures.py
```

The E2E fixture set is generated inside the isolated browser-test project by `backend/tests/e2e_support/generate.py`; it derives the 13 positive sample files from the same base generator and adds malformed/security/performance cases. Do not commit generated binaries under the ignored fixture directories.

The PDF is a minimal text PDF generated in Python; DOCX is generated with `python-docx`, spreadsheets with `openpyxl`/`xlwt`, slides with `python-pptx`, and EPUB with a small ZIP package. XML fixtures contain no DTD or external entities.
