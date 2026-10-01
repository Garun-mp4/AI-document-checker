# M01 — матрица приёмки

Область: существующее поведение приложения. M02 и следующие milestones не реализуются в рамках этого этапа.

## Уровни проверки

- Backend unit: `backend/tests/test_*.py`, за исключением `test_compose_workflow.py`. Проверяют ветвления, ошибки, locators, выбор источников, точные расчёты и настройки; mocks применяются на границах конкретного unit-теста.
- Backend integration: семь тестов `test_compose_workflow.py` с настоящими PostgreSQL, файлами, embeddings, MarkItDown и disconnected Codex. Запускаются отдельно от unit-набора, без skip.
- Frontend contracts: `frontend/tests/ui-contract.test.mjs`, `motion.test.mjs`. Это проверки исходного кода, они не подтверждают работу браузера.
- Browser E2E: `frontend/e2e/*.spec.mjs`, настоящий production build, Chromium, отдельный `document-checker-e2e` Compose. Подменяется только облачный Codex. Отдельные сценарии явно вводят fault конвертера или браузерного fetch.
- Live Codex: отдельный config и отдельный Compose на 5175; включается осознанно, не входит в локальную приёмку.

## Требование → автоматическая проверка → ручной контроль

Пути E2E ниже относятся к `frontend/e2e/`. Результаты конкретного прогона фиксируются в `TESTING_REPORT.md`; матрица описывает воспроизводимое покрытие, а не обещает отсутствие любых багов.

| Требование | Автоматическая проверка | Дополнительный ручной контроль / предел |
| --- | --- | --- |
| Сайт открывается как HTML; JS и PDF worker имеют правильный MIME | integration `test_compose_serves_frontend_assets_with_browser_mime_types`; все E2E используют production nginx | Проверить открытие в привычном браузере пользователя |
| PDF: обработка, canvas со значимыми пикселями, текстовый слой | `documents.spec.mjs`, `pdf: upload`; `multipage.pdf`; backend parsing/preview | Нестандартные шрифты и PDF из реальных редакторов не исчерпываются синтетическим PDF |
| DOCX: настоящий браузерный renderer, страницы и таблицы | `docx: upload`, `multipage.docx`; fit на 6 размерах | HTML docx-preview не обещает пиксельную копию Word |
| TXT и MD: оригинальные строки, Markdown, citations | `txt: upload`, `md: upload`, `cp1251.txt`, `cp1251.md`, `long.txt` | Длинные строки прокручиваются внутри viewer |
| CSV: оригинальные ячейки и заголовки | `csv: upload`, `quoted.csv`, `tab.csv`, `cp1251.csv` | Кавычки, запятые, `;`, tab, multiline и cp1251 покрыты |
| CSV: точные агрегаты, 100 → 200 → 240 строк | `CSV aggregates and actual row pagination`; backend analysis/parsing | Производительность таблиц на 100000 строк относится к последующим milestones |
| XML: исходные теги, безопасный парсер, подсветка нескольких значений | `xml: upload` (четыре подсвеченные строки, включая узлы с атрибутами), `dangerous.xml`; backend parsing/preview/mapping | Структурные точные ranges и расширенная карта цитат остаются областью M07 |
| XLSX и XLS: настоящая таблица, источники разных листов в карте | `xlsx/xls: upload`, `multisheet.xlsx/xls`; backend preview/parsing | Viewer пока показывает первый лист; переключение листов предусмотрено M07 |
| PPTX: структурированный read-only оригинал, второй слайд и источники | `pptx: upload`, `multislide.pptx`; backend parsing | Это текстовые блоки слайдов, не визуальная копия PowerPoint |
| HTML и HTM: исходный экранированный текст и Markdown | `html/htm: upload`, `HTML is source text`; backend parsing | Скрипт, event handler и внешний tracker не выполняются |
| JSON: исходный текст, подсветка значений, Markdown | `json: upload`; backend parsing/preview | Расширенная адресация повторяющихся значений — M07 |
| EPUB: главы/абзацы, второй chapter, source selection | `epub: upload`, `multichapter.epub`; backend parsing | Структурированный текст, не исходная вёрстка книги |
| Оригинал неизменен после конвертации | `upload()` сравнивает все байты с фикстурой; backend success/fallback для MD/TXT | Старые уже перезаписанные оригиналы не могут быть восстановлены из отсутствующих байтов |
| Локальный русско-английский OCR с точными областями | `Real local Russian OCR` и смешанный PDF с реальным Poppler/Tesseract; backend OCR и viewer citations | Качество сложных сканов и настройки повторного OCR — M06 |
| Пустой, oversized, unsupported upload | `Rejected upload` + независимые запросы API | Client guard и server validation проверяются раздельно |
| Повреждённый PDF/DOCX, неверный PDF, encrypted PDF, unreadable scan, DTD | `Processing failure is recoverable`; backend parsing/OCR | Ошибка сохраняется в библиотеке; новый чат остаётся доступен |
| Семь карточек и серверные идентификаторы sources | Все успешные `upload()` + `documents.spec.mjs` citations; backend analysis/citations | Ответы fake provider проверяют механику, не интеллект настоящего Codex |
| Citation открывает original, highlight, затем связанный Markdown | 13 format E2E + смешанный PDF E2E с проверкой OCR word boxes; backend mapping/preview | Для legacy OCR-источников без карты предлагается точное восстановление через повторную обработку |
| Ошибки PDF/DOCX renderer не дают белого экрана | `render failure` fault fetch; ready и семь карточек сохраняются; reload восстанавливает viewer | PDF.js использует native iframe fallback, DOCX — текстовый fallback |
| Ошибка MarkItDown → native fallback → rebuild | `markdown.spec.mjs`; backend processing/markitdown | Fault в единственном сценарии; остальные конвертации настоящие |
| Потоковый ответ показывается до окончания, GFM безопасен | `Stream Markdown...`: gate между delta, bold/list/table/code/blockquote, отключённый script | Test gate исключает sleep как способ скрыть гонку |
| Сохранённые сообщения и citations переживают reload/restart | `Stream Markdown...`, реальный restart test api/db/web, сравнение API messages до/после | Завершённые сообщения; recovery незавершённого job — M03 |
| Отсутствие доказательств / stream error / disconnected / unavailable | workspace no-evidence, errors Codex states; backend analysis/Codex | Не выдаём синтетический ответ за проверку облачного провайдера |
| Новый чат, выбор истории и удаление | workspace stream/new chat + delete/cancel, backend chat library | Исходная рабочая библиотека пользователя не используется |
| Модель и reasoning; keyboard/Escape/focus, сохранение | workspace 6 viewports; backend preferences/Codex | Два разрешённых model IDs и High сохраняются после reload |
| Библиотека 240/72 (mobile 300/72), чат доступен в обоих состояниях | workspace 6 viewports; frontend contracts | Reduced-motion включён в responsive сценариях; исходные motion contracts сохранены |
| Fit-to-width, bounded viewer, отсутствие общего horizontal overflow | `originalVisible()` + fit DOCX в workspace; сохранённые 6 screenshots | Pixel-perfect visual goldens не вводятся; скриншоты требуют человеческой оценки |
| Test provider не доступен штатному приложению | `.dockerignore` исключает tests; проверка production image import; отдельный entrypoint | Ни host MCP/CLI, ни production Codex auth не подключаются |
| Диагностика сбоя | retained trace/video/screenshot, browser-log attachment, compose.log, JUnit/HTML | Browser pageerror приводит к падению; ожидаемые HTTP ошибки сохраняются в логах |

## Что матрица намеренно не утверждает

100% coverage, работу каждого возможного документа, всех движков браузеров, качество ответов облачного Codex, качество OCR на любом скане и завершённость M02–M16. Пропуски обозначены границами, а не скрыты через `test.skip` или ретраи.
