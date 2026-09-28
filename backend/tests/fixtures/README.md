# Synthetic test documents

These six small documents contain invented project details and no personal or university data. They cover the supported PDF, DOCX, TXT, MD, CSV, and XML formats. `sample.csv` uses a UTF-8 BOM and semicolon delimiter and has exact expected aggregates: 3 rows, 3 numeric values per column, `Часы` sum 60 / average 20, and `Стоимость` sum 300 / average 100.

Regenerate all fixtures from the backend directory with:

```powershell
python tests/fixtures/generate_fixtures.py
```

The PDF is a minimal text PDF generated in Python; DOCX is generated with `python-docx`. The XML contains no DTD or external entities.
