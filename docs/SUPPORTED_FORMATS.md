# Контракт поддерживаемых форматов

Эта матрица фиксирует возможности продукта на 3 октября 2026 года. Backend-контракт расположен в `backend/app/services/document_formats.py`, frontend-каталог — `frontend/src/document-formats.json`; `backend/tests/test_supported_formats.py` проверяет, что MIME, extensions и preview renderer совпадают. E2E-набор строит сквозную матрицу непосредственно из frontend-каталога. Если формат меняется, обновляются parser, viewer и синтетические fixtures одновременно.

Для каждого файла сначала проверяется расширение, MIME (если браузер сообщил его), размер и сигнатура/структура. Затем выполняется соответствующий native parser, MarkItDown пробует построить Markdown, а при его ошибке документ остаётся доступен через native fallback. MarkItDown и native processing ограничены дочерним процессом; plugins отключены, источники локальные. HTML и HTM показываются как безопасный исходный текст, пользовательская разметка не исполняется.

| Расширение | Допустимые MIME | Сигнатура / структурная проверка | Native parser | Original preview | Fixture полного потока |
| --- | --- | --- | --- | --- | --- |
| `.pdf` | `application/pdf` | `%PDF-`; PDF читает pypdf; зашифрованные и повреждённые файлы отклоняются | pypdf, PDF page/source locators; локальный OCR только для подходящих сканов | PDF.js canvas и текстовый слой / OCR | `sample.pdf` |
| `.docx` | `application/vnd.openxmlformats-officedocument.wordprocessingml.document` | ZIP с `[Content_Types].xml`, `word/document.xml`; безопасный XML и внутренние ссылки | python-docx: абзацы и таблицы | `docx-preview` | `sample.docx` |
| `.txt` | `text/plain` | Декодирование поддерживаемых текстовых кодировок; запрет двоичных управляющих байтов | plain-text parser, line locators | Экранированный исходный текст | `sample.txt` |
| `.md` | `text/markdown`, `text/x-markdown`, `text/plain` | Текстовая сигнатура/декодирование; запрет двоичных управляющих байтов | Markdown/plain-text parser, line locators | Экранированный исходный текст | `sample.md` |
| `.csv` | `text/csv`, `application/csv`, `application/vnd.ms-excel`, `text/plain` | Текстовое декодирование; определяется `,`, `;`, tab или `|`; таблица обязана содержать заголовок | CSV parser и табличный анализ | Страничная/виртуализированная таблица | `sample.csv` |
| `.xml` | `application/xml`, `text/xml`, `text/plain` | XML должен разбираться; DTD/entities/external entities запрещены | defused XML parser с путями элементов | Экранированный исходный XML/текст | `sample.xml` |
| `.xlsx` | `application/vnd.openxmlformats-officedocument.spreadsheetml.sheet` | ZIP с `[Content_Types].xml`, `xl/workbook.xml`; безопасные внутренние XML/ссылки | openpyxl, листы/ячейки; формулы не вычисляются | Таблица с выбором листа и пагинацией | `sample.xlsx` |
| `.xls` | `application/vnd.ms-excel` | OLE Compound File signature `D0 CF 11 E0 A1 B1 1A E1` | xlrd, листы/ячейки; формулы не вычисляются | Таблица с выбором листа и пагинацией | `sample.xls` |
| `.pptx` | `application/vnd.openxmlformats-officedocument.presentationml.presentation` | ZIP с `[Content_Types].xml`, `ppt/presentation.xml`; безопасный XML/ссылки | python-pptx, слайды и текстовые блоки | Карта исходных слайдов | `sample.pptx` |
| `.html` | `text/html`, `text/plain` | Текст; scripts/styles/iframe не индексируются native parser’ом | HTML parser извлекает безопасный видимый текст | Экранированный исходник, не активная web-страница | `sample.html` |
| `.htm` | `text/html`, `text/plain` | Те же проверки и безопасность, что для `.html` | HTML parser с locator type `htm` | Экранированный исходник, не активная web-страница | `sample.htm` |
| `.json` | `application/json`, `text/plain` | Должен разбираться как JSON; сохраняются raw ranges и JSON path | JSON parser | Экранированный исходный JSON/текст | `sample.json` |
| `.epub` | `application/epub+zip`, `application/epub`, `application/zip`, `application/x-zip-compressed` | ZIP с `META-INF/container.xml`; `mimetype` точно `application/epub+zip`; безопасный XML и внутренние ссылки | EPUB/HTML parser по главам, scripts не исполняются | Карта глав и абзацев | `sample.epub` |

Пустой, чрезмерный, структурно повреждённый или несовместимый по MIME файл отклоняется до создания записи документа; частично загруженный файл удаляется. Отсутствующий MIME, `application/octet-stream` и `binary/octet-stream` допустимы, но сигнатура/структура всё равно проверяется. MIME mismatch отклоняется. Расширение сравнивается без учёта регистра.

## Продуктовые пределы

| Область | Текущий предел | Источник |
| --- | --- | --- |
| HTTP body / исходный файл | 25 MiB на файл | `backend/app/config.py:max_upload_bytes`; frontend отображает тот же подтверждаемый contract default |
| Текст документа | 5,000,000 символов | `document_max_chars` |
| Markdown | 5,000,000 символов; обработка до 120 секунд | `markdown_max_chars`, `markdown_timeout_seconds` |
| PDF страницы / PPTX слайды | 500; OCR ограничен 100 страницами за запуск по умолчанию | `document_max_pages`, `ocr_max_pages` |
| Office/EPUB ZIP | 5,000 записей, 100 MiB распакованных суммарно, 32 MiB на участника | `archive_max_entries`, `archive_max_bytes`, `archive_member_max_bytes` |
| CSV/XLS/XLSX native parse | 100,000 строк и 500 столбцов | `parsing.py` spreadsheet/CSV limits |
| Табличный анализ | 500,000 строк, 500 столбцов, 2,000,000 непустых ячеек | `table_analysis.py` |
| Original preview | до 2,000 source blocks; таблица загружается страницами по 100 строк и виртуализируется в браузере | `preview.py`, `OriginalDocumentViewer.tsx` |

Границы отличаются намеренно: файл может пройти загрузочный размер, но позднее быть отклонён из-за количества строк, страниц, распакованного объёма или лимита текста. Значение `max_upload_bytes` меняется конфигурацией; значение на стартовом экране обозначает default Compose-конфигурации.

## Положительная и отрицательная проверка

- `frontend/e2e/documents.spec.mjs` выполняет upload → ready → original viewer → citation → Markdown download для каждого каталожного extension и сверяет ответный renderer. Для CSV/XLSX дополнительно проверяются bounded DOM rows и пагинация на больших synthetic tables.
- `backend/tests/test_fixtures.py` и `test_markdown_integration.py` запускают native parser и MarkItDown для всех 13 fixtures, включая отдельный `.htm`.
- `backend/tests/test_supported_formats.py` проверяет полный набор extensions/MIME/renderer, uppercase extensions, отсутствующий и неверный MIME, пустые данные без оставшейся записи/файла и исключённые `.xlsm`, `.xlsb`, изображения и ZIP. Дополнительные тесты `test_document_security.py` закрепляют package traversal, DTD/entity, бинарные подмены, размер и encryption.
- `backend/tests/performance/document_format_stress.py` — ограниченный ручной profile, не запускаемый в каждом unit suite. Проверка 3 октября 2026 года: DOCX 24,154,409 байт обработан за 195.7 ms с peak traced allocation 24,216,764 байта; CSV 25,000 строк / 477,802 байта — 1,474.1 ms / 12,113,507 байт; XLSX 5,000 строк / 73,838 байт — 2,157.4 ms / 9,375,743 байта. Это `tracemalloc` Python allocations, не RSS всего процесса и не гарантия максимального времени для всех файлов. E2E проверяет число отображённых строк и отсутствие горизонтального overflow.
