# Отчёт о тестировании AI Document Checker

## M21 — сравнение явно выбранных документов

Дата: 4 октября 2026 года. M21 реализован и принят на изолированном Compose-проекте с собственными тестовыми volumes.

| Проверка | Результат |
| --- | --- |
| Backend unit suite | **445 passed, 11 integration deselected**; одно существующее предупреждение Alembic `path_separator` |
| M21 comparison unit tests | **7 passed**: границы/уникальность выбора, revision, точные владельцы citation, междокументные доказательства, safe snapshots и квоты retrieval |
| Frontend contract/unit tests | **37 passed** |
| Frontend production build | **PASS** (`tsc --noEmit`, Vite, initial gzip budget) |
| Стартовый JS bundle | **PASS**: 916,053 bytes raw / 269,785 gzip; на 25.7% меньше baseline 363,239 gzip; предел 290,591 gzip |
| Полный Compose Chromium E2E | **107 passed, 0 failed** (19.6 минуты); включает comparison flow, citations обоих файлов, смену состава/версий, reload, удаление выбранного источника и восстановление набора |
| Дополнительный comparison E2E | **1 passed**: одинаковые длинные имена различаются в picker, выбор проверен на 390×844 без горизонтального overflow |
| Responsive browser checks | **6/6 прошли**: 1440×900, 1280×720, 1024×768, 768×1024, 430×932, 390×844; мобильный comparison picker дополнительно проверен на 390×844 |
| Migration acceptance | **PASS**: upgrade/downgrade до M10 и возврат к head сохранили старые document chat, messages, citations и original bytes; downgrade запрещён при comparison chat, существующий application-chat guard отдельно проверен |
| API integration | **11 passed, 445 deselected** |
| Security audit | **10 assertions passed**: process/file limits, chunked upload/nginx limits, descriptor/link protections, converter error boundary |
| PostgreSQL queue | **13 базовых checks и 9 acceptance tests прошли** |
| Maintenance acceptance | **1 passed** |
| Backup/restore regression | **PASS** на отдельных временных database/document volumes |
| Ruff для изменённых backend-файлов | **PASS** |

Comparison acceptance подтверждает, что AI получает только retrieval-фрагменты документов из текущего выбранного набора; каждый document finding и citation проверяется на server-side принадлежность. Источники фиксируются по версии, набор ограничен 2–5 ready документов. При смене набора история сохраняется по `context_epoch`, предыдущие citation snapshots не теряют имя/locator, а удаление выбранного файла блокирует новые вопросы до восстановления набора. E2E suite тестировала только синтетические документы; live Codex не использовался.

## M20 — ленивая загрузка PDF.js и производительность старта

Дата: 4 октября 2026 года. M20 реализован и проверен. E2E прогоны используют синтетические документы и отдельные временные Compose volumes.

| Проверка | Результат |
| --- | --- |
| Frontend contract/unit tests (`npm test`) | **37 passed** |
| Frontend production build (`npm run build`) | **PASS** (`tsc --noEmit`, Vite и gzip budget) |
| Стартовый JS bundle | **PASS**: 900,570 bytes raw / 266,659 gzip; на 26.6% меньше baseline 363,239 gzip; предел 290,591 gzip |
| PDF assets | **PASS**: renderer chunk 334,596 bytes raw / 98,029 gzip; worker отдельный asset 1,375,838 bytes |
| Backend unit suite | **438 passed, 11 integration deselected** |
| Compose API integration | **11 passed, 438 deselected**; проверены dynamic manifest entry, отдельные renderer/worker assets, MIME и cache headers |
| Chromium E2E: форматы, OCR и M20 | **31 passed**: все 13 заявленных расширений, кодировки, многостраничные PDF/DOCX, много-листовые XLS/XLSX, PPTX/EPUB, таблица с пагинацией, русский OCR, viewer/worker по требованию, три viewport, повтор chunk/worker с сохранением citation; отдельно в целевом PDF rerun подтверждены mixed native/OCR word boxes и существующий render fallback |
| PostgreSQL queue checks / acceptance | **13 базовых проверок и 9 сценариев прошли** |
| Изолированный security acceptance | **4 Linux resource/output + 2 upload/nginx + 3 descriptor/link + 1 converter-boundary проверки прошли** |
| Migration acceptance на этапе M20 | В том фильтрованном browser run была штатно пропущена; позже выполнена в полном M21 acceptance и прошла, включая сохранность старых чатов, citations и original bytes |

Сборка по-прежнему показывает штатное предупреждение Vite о JavaScript chunk больше 500 kB в несжатом виде; M20 уменьшает стартовую gzip-загрузку и переносит PDF.js в динамический chunk, не скрывая предупреждение. Начальная страница, открытие PDF, восстановление после отказа динамического chunk/worker, citation/OCR подсветка и три целевых viewport прошли браузерную проверку. После основного прогона M20 focused suite повторно прошёл (**5 passed**): включён новый DOCX network check (без загрузки PDF assets) и проверено сохранение citation при отказе worker и повторе.

## M19 — единый контракт поддерживаемых форматов

Дата: 3 октября 2026 года. M19 реализован и проверен.

| Проверка | Результат |
| --- | --- |
| Backend unit/parser/security | **438 passed, 11 integration deselected**; одно существующее предупреждение Alembic `path_separator` |
| Frontend contract/unit tests | **37 passed** |
| Frontend production build | **PASS** (`tsc --noEmit` и Vite); штатное предупреждение о крупном JS bundle и PDF worker остаётся |
| Ruff изменённых backend-файлов | **PASS** |
| Ограниченный stress profile | **PASS**: DOCX 24.15 MB — 196 ms / 24.2 MB peak traced allocations; CSV 25,000 строк — 1.47 s / 12.1 MB; XLSX 5,000 строк — 2.16 s / 9.4 MB |
| Изолированный Compose security audit | **10 assertions passed**; сервисы healthy |
| Chromium E2E по форматам | **15 passed**: все 13 расширений, плюс большие CSV и XLSX; проверены original viewer, citations, скачивание Markdown, пагинация, ограниченный DOM и горизонтальный overflow |
| PostgreSQL queue acceptance | **13 базовых проверок и 9 сценариев прошли**; отмена/повтор, восстановление, сохранность документов и OCR child-process cleanup |
| API integration / maintenance | **11 API integration passed**, **1 maintenance acceptance passed** |
| M15 backup/restore regression | **PASS** на отдельных временных PostgreSQL/document volumes |
| Полная migration/browser matrix на этапе M19 | В том форматном прогоне не запускалась; позже выполнена в полном M21 acceptance, а полный Chromium набор прошёл |

Стресс-метрики получены для длительности и peak Python traced allocations (`tracemalloc`), а не RSS всего процесса. Большая таблица дополнительно проверена в Chromium на отсутствие горизонтального overflow и ограниченное число смонтированных строк. Тестовый Compose использовал уникальные имена проекта и собственные volumes; после завершения все его контейнеры, сети и временные volumes удалены, рабочие данные не затрагивались.

Список форматов теперь сверяется между backend-политикой и frontend-каталогом contract-тестом; стартовый экран, `accept`, upload-проверка и маршрутизация исходного viewer используют эти контракты. Capability-матрица и продуктовые лимиты описаны в [`docs/SUPPORTED_FORMATS.md`](docs/SUPPORTED_FORMATS.md). Добавлен отдельный `.htm` fixture. Полная команда и результаты E2E соответствуют запуску `scripts/test-e2e.ps1` с фильтром format/citation и large-table сценариев.

## M18 — приватный голосовой ввод в composer

Дата: 3 октября 2026 года.

| Проверка | Результат |
| --- | --- |
| Frontend unit/contract tests | **34 passed**; в набор входят 5 unit-тестов обработки транскрипта/ошибок и контракты приватного режима |
| Frontend production build | **PASS** (`tsc --noEmit` и Vite); осталось штатное предупреждение о крупном JS bundle |
| Backend unit suite | **393 passed, 11 integration deselected**; одно предупреждение Alembic `path_separator` |
| Изолированный Compose и security audit | **PASS**: сервисы healthy; проверки ресурсов/вывода 4, upload-limit 2, Linux path protections 3 и ошибка converter boundary 1 прошли |
| Chromium E2E M18 | **1 passed**: локальная настройка распознавания, вставка у курсора без автоотправки, Escape, закрытие чата, отказ микрофона, отсутствие языкового пакета, неподдерживаемый API и ширина 390 px |
| Первичная PostgreSQL queue acceptance в прогоне M18 | Исторически: 13 базовых проверок прошли; 2 из 9 acceptance-сценариев не прошли (partial-index recovery и ожидаемое число OCR restart attempts) |
| Повторная общая regression-приёмка M19/M21 | **PASS**: позднее все 9 queue acceptance и 13 базовых проверок прошли; финальный M21 прогон также прошёл 11 API integration, maintenance и backup/restore |

### Дополнение от 4 октября 2026 года: M1–M3 — визуальная шкала уровня микрофона

| Проверка | Результат |
| --- | --- |
| Frontend unit/contract (`npm test`) | **42 passed**; 5 тестов проверяют расчёт RMS/нормализацию сигнала, аудио-only запрос, отказ/отсутствие API и освобождение дорожек/AudioContext |
| Frontend production build (`npm run build`) | **PASS**: `tsc --noEmit`, Vite и проверка стартового gzip бюджета |
| Стартовый JS bundle | **PASS**: 921,102 bytes raw / 271,224 gzip; предел 290,591 gzip соблюдён |
| Изолированный Compose Chromium E2E | **1 passed**: реальный UI-поток запускается только после клика; синтетический Web Audio сигнал меняет высоту 29 полос при тихом/громком уровне; проверены отмена, курсор, закрытие чата, позднее разрешение микрофона, ошибка разрешения, fallback без измерителя, reduced motion и отсутствие автоотправки |
| Responsive voice composer | **6/6 размеров прошли**: 1440×900, 1280×720, 1024×768, 768×1024, 430×932 и 390×844; мобильный сценарий открывает чат штатной кнопкой перед записью и проверяет отсутствие горизонтального overflow |
| Compose health и изоляция | **PASS**: DB/API/worker/web были healthy; использовались отдельный тестовый проект и только его временные volumes, после успешного прогона они удалены |

M1 добавляет локальный Web Audio RMS-анализатор, M2 показывает сигнал в общей composer-панели, M3 закрепляет жизненный цикл, возврат курсора, reduced-motion и responsive-поведение unit/E2E-проверками. В reduced-motion шкала остаётся индикатором текущей громкости, но все полосы имеют одинаковую высоту и обновляются не чаще раза в 180 мс вместо бегущей волны. E2E подменяет и `SpeechRecognition`, и генератор аудиосэмплов; он подтверждает механику и связь уровня с амплитудой, но **не проверяет физический микрофон и качество реального распознавания речи**. Аудиопоток для шкалы открывается только после нажатия, не записывается и завершается на отмене, остановке, закрытии чата, ошибке, уходе со страницы и размонтировании.

Первый M18 прогон остановился на двух queue acceptance ошибках до backend integration, maintenance и backup/restore; это исторический результат, а не текущий незакрытый дефект: повторные queue проверки, API integration, maintenance и backup/restore прошли в последующих приёмках M19/M21. Migration acceptance, пропущенная в отдельных фильтрованных запусках M19/M20, также прошла в полном M21 наборе. Изолированные тестовые Compose volumes удалялись своими runner-скриптами. M18 E2E использовал подменный `SpeechRecognition`: **реальный микрофон и качество распознавания речи в конкретном установленном браузере вручную не проверялись**. Подтверждены состояния UI и отсутствие отправки аудио; реальную доступность локального speech API и качество распознавания можно подтвердить только smoke-проверкой в целевом браузере с разрешённым микрофоном.

Голосовой ввод встроен в общий `AIComposer`: распознавание включается только после явного действия и только при доступном `ru-RU` с `processLocally=true`; пакет языка не скачивается автоматически и облачного fallback нет. Транскрипт вставляется в позицию курсора или заменяет выделение, остаётся редактируемым черновиком, а Escape и закрытие чата отменяют запись. Backend, API и хранение аудио не добавлялись. Подробности и browser-ограничения отражены в README и M18 дорожной карты.

## M17 — надёжность XLSX и точность формул

Дата: 3 октября 2026 года.

| Проверка | Результат |
| --- | --- |
| Полный backend unit suite | **393 passed, 11 integration deselected**; одно существующее предупреждение Alembic о `path_separator` |
| Изолированный Compose integration | **1 passed**: загрузка XLSX, обработка worker/MarkItDown, статусы формул, точная ссылка на ячейку, просмотр таблицы, Markdown GET/download и неизменность исходных байтов |
| Frontend contract tests | **29 passed** |
| Frontend production build | **PASS**; осталось штатное предупреждение Vite о крупном bundle с PDF.js |
| Ruff изменённых backend-файлов | **PASS** |
| Полный Ruff для `app tests` | **2 существующих замечания вне изменённых файлов**: BLE001 в `app/private_errors.py:24` и I001 в `tests/test_markdown_integration.py:5` |
| Предоставленная XLSX-книга | Локально прошла MIME/OOXML-проверку, native parsing и MarkItDown; upload handler принял её и сохранил исходные байты. Обнаружено 122 строки данных и 0 формул; значения ячеек не выводились и файл не добавлялся в репозиторий |

XLSX-парсер теперь объединяет формулы и сохранённые Excel результаты из отдельных потоков чтения, сохраняя лист/строку/колонку/ячейку как source locator. Результат формул не вычисляется приложением: нулевой cached value остаётся корректным значением, а отсутствующий отмечается явно. Для пустых ячеек MarkItDown создавал `NaN`; нормализация теперь сверяется с native-источниками и не смешивает пустые ячейки с буквальным текстом `NaN`. Formula lookup индексирован по листу и адресу ячейки. Результат XLSX-конвертации проверяется на лимит после обогащения формульным контекстом; превышение переключает документ на native fallback без частичного Markdown-артефакта. Кэш изолированных артефактов получил отдельную XLSX-версию.

Пользовательская книга не содержит формул и сама по себе не воспроизводит первоначальный отказ загрузки в браузере. В текущем окружении её формат и backend upload-путь прошли; исходная браузерная проблема не повторилась, поэтому нельзя утверждать, что причина именно той ошибки установлена. Формулы, включая формулы без cached value и array formulas, проверены синтетической книгой в parser/table unit и Compose integration тестах. Изолированный Compose-проект вместе с временными контейнерами, сетью и volumes удалён после проверки; рабочие Compose данные не затрагивались.

## M10 — проверки базового экспорта

Дата: 2 октября 2026 года. Эта запись фиксирует первоначальную проверку базового экспорта до завершения M09; финальная совместная приёмка M09/M10 приведена ниже.

| Проверка | Результат |
| --- | --- |
| Экспортные backend unit tests | **8 passed**: Markdown, источники и версии, безопасность HTML/ссылок, Unicode filename, PDF кириллица, длинные таблицы и страницы |
| Compose API integration | **1 passed**: Markdown/PDF и три области экспорта; количество вызовов модели не меняется после скачиваний; проверены заголовки и привязки к сохранённым source IDs |
| Chromium export E2E | **1 passed**: выбор ответов, скачивание Markdown и PDF полного анализа через UI |
| Frontend contract tests | **19 passed** |
| TypeScript / production build | **PASS**; остаётся штатное предупреждение Vite о размере JS bundle |
| Isolated Compose | API, DB и worker прошли health-check; web отвечает HTTP 200; использован только `document-checker-e2e` |

Экспорт строится из сохранённого snapshot текущей готовой версии. В Markdown и PDF указаны модель/reasoning, версия обработки, статус MarkItDown/OCR, ссылки-источники с excerpt и локаторами; в переписке сохраняются хронология и настройки конкретного ответа. В PDF используются встроенные Unicode-шрифты, повторяемые заголовки таблиц, переносы страниц, код и безопасные кликабельные ссылки. Ошибка генерации возвращает отдельный ответ и не изменяет документ или чат. Первичная проверка выявила потерю excerpt внутри вложенного списка PDF и некорректную обработку скобок в небезопасной ссылке; оба дефекта исправлены и закреплены тестами.

## M08 — поиск по оригиналу и Markdown

Дата: 2 октября 2026 года. Эта запись относится к моменту завершения M08; последующие результаты M09 и M10 приведены ниже.

| Проверка | Результат |
| --- | --- |
| Backend unit/parser/API | **262 passed, 9 integration deselected** |
| M08 backend search tests | **20 passed**; ranges, OCR boxes, Markdown offsets, CSV/XLS/XLSX locators, format-specific source mapping and bounds |
| Ruff изменённых backend-файлов | **PASS** |
| Frontend contract tests | **19 passed** |
| TypeScript / production build | **PASS**; остаётся предупреждение Vite о крупном JS bundle с PDF.js |
| Chromium search E2E | **4 passed**: полный TXT/Markdown поиск и пагинация, stale/error handling, citation preservation, OCR PDF + CSV/XLSX cells + PPTX slide + EPUB chapter |
| Citation regression E2E | **PASS** для PDF после разведения сброса поиска и выбранного источника |
| Isolated Compose | API, DB и worker прошли health-check; web отвечает HTTP 200 и проверен Playwright. Search сценарии работали с реальными парсерами/OCR и тестовым provider |

В viewer добавлена буквальная строка поиска с отдельными областями «Оригинал» и «Markdown», дебаунсом, отменой устаревших запросов, полным счётчиком, пагинацией и переходом по Enter/Shift+Enter; Escape и кнопка очищают запрос. Сервер выполняет полный поиск, возвращая точные source/Markdown ranges. PDF OCR ограничивает подсветку совпавшими word boxes; таблицы ищутся в ячейках с локаторами строк, колонок и листов; DOCX/PDF/PPTX/EPUB сохраняют свои paragraph/page/slide/chapter locator’ы. Выбор citation очищает активную строку поиска, но оставляет citation источником текущей подсветки.

Первый широкий E2E запуск обнаружил именно эту регрессию citation/search; исправление подтверждено браузерным тестом. Дополнительный mixed-format тест выявил два неточных перехода: PPTX объединял несколько shape locator’ов в одном блоке, EPUB содержал несколько блоков на одну главу. Viewer теперь сопоставляет вложенные source locator’ы PPTX и точные символные диапазоны EPUB; повторный OCR/table/slide/chapter сценарий прошёл. Полная несвязанная E2E матрица из `documents.spec.mjs` в этом M08 прогоне не запускалась целиком; отдельно повторно прошёл PDF citation сценарий. Тестовый Compose-проект изолирован от рабочего приложения.

## M07 — единый механизм citations и точная подсветка

Дата: 1 октября 2026 года. M07 реализован и проверен; M08 не начинался.

| Проверка | Результат |
| --- | --- |
| Backend unit/parser/API | **242 passed, 9 integration skipped** без `AI_CHECKER_BASE_URL` |
| Ruff `app` и `tests` | **PASS** |
| Frontend contract tests | **19 passed** |
| TypeScript / production build | **PASS**; остаётся предупреждение Vite о крупном JS bundle с PDF.js |
| Isolated Compose security acceptance | **10 passed**: сетевые/ресурсные лимиты, chunked upload, nginx body limit, Linux file pinning и безопасная ошибка конвертера |
| Chromium E2E | **71 passed**; 13 форматов, citation по длинной CSV-таблице, переход по листам XLS/XLSX, TXT/MD/XML/HTML ranges, PDF native/OCR, mobile, восстановление и restart |
| PostgreSQL queue acceptance | **13 passed**; concurrency, leases, fencing, retry/deletion и сохранность результатов старой версии |
| Compose API integration | **9 passed** |

Добавлен единый versioned locator с идентификатором документа и версией обработки. Locator сохраняет диапазоны строк/символов и координатное пространство; существующие поля и citation ID остаются совместимыми. Для PDF native текст выделяется через CSS Highlights, а OCR-карта выводит координатные подсветки поверх исходной страницы. DOCX выбирает нужный абзац/табличную строку, текстовые форматы используют сохранённые диапазоны, а CSV/XLS/XLSX переключают лист и догружают порцию, содержащую цитируемую строку. Для PPTX/EPUB доступны структурированные исходные блоки с честно ограниченным уровнем точности. Вычисленные значения показываются как расчёты с исходным диапазоном, без поиска вычисленного текста в оригинале.

Новые проверки закрепляют версию и систему координат preview locators, исходные offsets TXT/XML/JSON/HTML, диапазоны глав EPUB, листовую пагинацию и переходы citation в длинные/не загруженные таблицы. E2E-тесты используют тестовый endpoint только в приложении поддержки тестов, чтобы выбрать настоящий сохранённый locator; production API не расширяется тестовым маршрутом. Перезапуск Compose выполняется последовательно, чтобы исключить гонку DNS между nginx и API.

## M06 — настройки и повторный OCR

Дата: 1 октября 2026 года. M06 реализован, проверен в unit-тестах и в изолированном Docker Compose; следующий milestone не начинался. Описание поведения и ограничений: [docs/M06_OCR_SETTINGS.md](docs/M06_OCR_SETTINGS.md).

| Проверка | Результат |
| --- | --- |
| Backend unit/parser/API | **231 passed, 9 integration deselected** |
| M06 Compose API integration | **1 passed**; проверены валидация выбора, параметры OCR, ошибка повторного анализа и сохранение прежней активной версии; cloud Codex не использовался |
| Frontend contract tests | **19 passed** |
| TypeScript / production build | **PASS**; остаётся штатное предупреждение Vite о размере JS bundle |
| Ruff `app` и `tests` | **PASS** |
| Ruff `app`, `migrations` и `tests` | 5 существующих I001-проверок порядка импортов в `migrations/versions/0002`–`0006`; эти миграции в M06 не изменялись |
| M06 Chromium E2E | **2 passed**; реальный mixed PDF, настройки `eng`/300 DPI и одной страницы, отмена без потери прежней версии, отказ параллельному запуску, успешная новая версия, прежние citations после reload, mobile 390×844 без горизонтального overflow; текстовый PDF показывает 0 автоматически подходящих страниц и оставляет ручной выбор |
| Compose E2E services | DB/API/worker/web healthy; тестовый объём изолирован от production |

При первом браузерном прогоне обнаружена потеря сводных OCR-настроек при сборке `pdf_page_map`. Передача `ocr_settings` восстановлена и закреплена unit- и E2E-проверками. Дополнительно уточнён счётчик автоматического OCR: текстовые страницы больше не показываются как страницы, ожидающие распознавания. Итоговый прогон прошёл.

## M05 — OCR смешанных PDF и карта координат

Дата: 1 октября 2026 года. M05 завершён и проверен. Подробное описание реализации: [docs/M05_OCR_MIXED_PDFS.md](docs/M05_OCR_MIXED_PDFS.md).

| Проверка | Результат |
| --- | --- |
| Backend unit | 205 passed, 8 integration deselected |
| Compose API integration | 8 passed, 205 unit deselected; тестовый provider отключён, live Codex не использовался |
| Frontend contracts | 17 passed |
| TypeScript / production build | Успешно; осталось штатное предупреждение Vite о размере JS bundle |
| Ruff `app` и `tests` | Успешно |
| Полный Chromium E2E | 65 passed, 0 failed, 0 skipped (18,7 минуты) |
| Responsive E2E | 6 размеров от 1440×900 до 390×844 прошли |
| Compose test services | DB/API/worker healthy; тестовые volumes сохранены |

Mixed PDF acceptance использует файл с нативной страницей, русско-английским сканом, пустой страницей и повёрнутой/CropBox-страницей. Реальная обработка через Compose проверила классификацию страниц, отсутствие дубликатов и пустых листов, порядок Markdown, диапазоны символов, координаты слов/строк, поворот, неизменность исходных байтов и сохранение карты в API. Chromium проверил клик по citation и геометрию подсветки на PDF canvas. Отдельный тест покрывает частично нераспознанные страницы и сохранение доступного текста.

## M04 — прогресс обработки и проверка версии приложения

Дата: 1 октября 2026 года. M04 завершён до начала M05. Реализация и эксплуатационный запуск: [docs/M04_PROGRESS_VERSION.md](docs/M04_PROGRESS_VERSION.md).

| Проверка | Результат |
| --- | --- |
| Backend unit | 199 passed, 7 integration deselected |
| Frontend contracts | 17 passed |
| TypeScript / production build | Успешно; штатное предупреждение Vite о размере JS bundle |
| Ruff `app` и `tests` | Успешно |
| PostgreSQL queue audit | 13 passed |
| Queue acceptance | 9 passed; OCR cancel test повторно прошёл после исправления reader прогресса |
| Compose API integration | 7 passed, 198 unit deselected; синтетический provider отключён, live Codex не использовался |
| Полный Chromium E2E | 63 passed; единственный сбой был гонкой мгновенного измерения fit-to-width. После перевода проверки на ожидание применённого масштаба все 6 responsive-вариантов прошли |
| M04 Chromium E2E | 3 passed: cache/version manifest, stale-build notice, progress restore/cancel/retry |
| Production Compose deploy smoke | **PASS**: DB/API/worker healthy; frontend доступен, build metadata и cache headers согласованы; Chromium desktop/mobile smoke прошёл. Build ID: `5a1edd8b942d-20261001140028`; volumes сохранены |

После отмены OCR на устаревшем lease reader перестаёт отправлять прогресс, но продолжает читать stderr до остановки изолированного worker. Это убирает необработанное исключение из журнала и сохраняет очистку дочерних OCR-процессов. Регрессионный unit и реальный Compose OCR-cancel сценарии прошли после исправления.

Полный первоначальный браузерный прогон завершился с одним нестабильным измерением страницы DOCX до следующего animation frame. Условие теста теперь ожидает фактическую подгонку листа. На разрешениях 1440×900, 1280×720, 1024×768, 768×1024, 430×932 и 390×844 повторно прошёл responsive-сценарий; весь 64-тестовый набор после этой тестовой поправки целиком не перезапускался.

В deployment-скрипте устранено ложное несовпадение `built_at`: PowerShell автоматически преобразует ISO-строку из JSON в `DateTime`, поэтому время теперь сравнивается как UTC-момент. После исправления `scripts/deploy-compose.ps1` успешно пересобрал рабочий Compose, проверил метаданные frontend/API и HTTP cache headers, дождался health checks, проверил доступность сайта и прошёл браузерный smoke на desktop и mobile. Build ID работающей сборки — `5a1edd8b942d-20261001140028`; именованные volumes сохранены.

## M03 — очередь задач, восстановление, отмена и версии обработки

Дата: 1 октября 2026 года. M03 завершён. Реализация и эксплуатационные правила: [docs/M03_PROCESSING.md](docs/M03_PROCESSING.md).

| Проверка | Результат |
| --- | --- |
| Backend unit | 198 passed, 7 integration deselected, 0 skipped |
| Frontend contracts | 15 passed |
| TypeScript / production build | Успешно (с штатным предупреждением Vite о размере bundle) |
| PostgreSQL queue audit | 13 passed |
| M03 queue acceptance | 8 passed: конкурентные заявки, restart OCR/indexing/analysis, fencing, cancel, delete и versioned replacement |
| Полный Chromium E2E | 61 passed, 0 retries, 0 skipped |
| Compose API integration | 7 passed, 198 unit deselected, 0 skipped |
| Linux/HTTP/security audit | 10 passed |
| Ruff `app` и `tests` | Успешно |
| Unit statement coverage `app/` | 2273 / 3336, 68%; worker и E2E покрываются отдельными runtime-тестами |

Проверено, что задания хранятся в PostgreSQL, захватываются через lease и fencing, восстанавливаются после истечения lease, а повторная заявка идемпотентна. Незавершённый локальный этап повторяется, прерванный внешний запрос получает явную ошибку и требует явного retry. Новая обработка создаёт версию и активируется атомарно; до этого готовая версия, оригинал, chunks, insights, citations и чат остаются доступны. Отмена OCR действительно завершает Poppler/Tesseract и удаляет только временную версию. Worker имеет отдельный Compose-сервис и healthcheck.

Общий скрипт `scripts/test-e2e.ps1 -KeepRunning` завершился с exit code 0: 61 браузерный сценарий, 8 M03 acceptance-сценариев и 7 integration-тестов. Тестовые volumes изолированы проектом `document-checker-e2e`; рабочие документы и Codex-авторизация не используются. M04 и M05 в этом прогоне не реализовывались.

## M02 — безопасность загрузки и артефактов

Дата: 1 октября 2026 года. M02 завершён. Полный `scripts/test-e2e.ps1 -KeepRunning` прошёл с exit code 0; после последнего уточнения безопасной ошибки конвертера повторены весь backend, Linux-аудит, 24 сценария ошибок/безопасности и API-интеграция. M03 выполнен отдельным прогоном и описан выше.

Реализованы потоковые ограничения HTTP/API, проверка MIME/сигнатуры/архивов, безопасные дескрипторы артефактов и привязка к UUID документа, ограниченные дочерние процессы parsing/MarkItDown/OCR/mapping/table, запрет сетевых syscalls worker, ограничения контейнера и приватные сообщения ошибок. Подробные границы и лимиты: [docs/M02_SECURITY.md](docs/M02_SECURITY.md).

Добавлены 55 backend-тестов, 11 браузерных сценариев и 10 проверок реального Linux/HTTP окружения. Генератор создаёт 47 синтетических файлов, включая traversal в четырёх форматах, ZIP bomb, избыточное число частей, внешние Office-ресурсы, XML entity и бинарный файл с текстовым расширением. Рабочие документы и Codex-авторизация не используются.

Диагностические прогоны выявили и исправили два дефекта: Chromium/Windows использует MIME `application/epub`; частичный ответ Range дублировал `Content-Length` из-за регистра ключей. Для обоих добавлена защита от регрессии. Unit-тесты также проверяют отмену и DB commit, отсутствие чужого удаления, таймаут/cleanup worker, безопасные заголовки, синтетические приватные строки в ошибках и их отсутствие в логах.

| Проверка | Результат |
| --- | --- |
| Backend unit, финальный код | 195 passed, 7 integration deselected, 0 skipped |
| Frontend contracts | 15 passed |
| TypeScript / production build | Успешно |
| Полный Chromium E2E | 61 passed, 0 retries, 0 skipped (7,2 минуты) |
| Финальный повтор errors + security | 24 passed, 0 retries, 0 skipped (1,5 минуты) |
| Финальная Compose API integration | 7 passed, 195 unit deselected, 0 skipped |
| Linux kernel/HTTP/файловый аудит | 10 проверок passed |
| Ruff всех изменённых backend/test Python-файлов | Успешно |
| Unit statement coverage `app/` | 2089 / 2943, 70,98%; процессы worker и E2E отдельно не включены |
| Рабочий Compose на 5173 | API/БД/web healthy; HTTP 200 `text/html`; Codex authenticated без ошибки |
| Изоляция тестового control endpoint | Production возвращает 404 |
| Live-анализ настоящим Codex | Не запускался |

Итого: 278 различных тестов и 10 runtime-проверок; 24 критических E2E-сценария дополнительно повторены на финальном backend. Проверены все 13 расширений, OCR настоящими Poppler/Tesseract и размеры 1440×900, 1280×720, 1024×768, 768×1024, 430×932, 390×844. Облачный провайдер заменён только в изолированном тестовом окружении.

Вызов API-интеграции при connected fake provider сначала дал три штатных safety skip. После переключения **тестового** провайдера в disconnected согласно `scripts/test-e2e.ps1` все семь тестов выполнены без skip. Защитный guard тестов не изменялся.

Локальные журналы: `e2e-artifacts/m02-verified.log`, `m02-final-regression.log`, `backend-coverage.json`. Рабочие образы API/web пересобраны и подняты без удаления пользовательских volumes; окружение и volumes `document-checker-e2e` удалены после проверки. Vite по-прежнему предупреждает о размере JS bundle; это не ошибка сборки, оптимизация относится к будущим milestones. Linux-защиты не являются отдельной файловой песочницей; границы описаны в M02_SECURITY.

Результаты M01 ниже сохранены как исторический baseline.

## M01 — приёмка и браузерные E2E

Дата: 1 октября 2026 года. M01 завершён. Заключительный запуск `pwsh -File scripts/test-e2e.ps1 -KeepRunning` с чистыми тестовыми volumes завершился с exit code 0.

| Проверка | Результат |
| --- | --- |
| Backend unit | 140 passed, 7 integration deselected, 0 skipped |
| Frontend contracts | 15 passed |
| TypeScript / production build | Успешно |
| Chromium browser E2E | 50 passed, 0 retries, 0 skipped (4,4 минуты) |
| Compose API integration | 7 passed, 140 unit deselected, 0 skipped |
| Ruff изменённых backend/test файлов | Успешно |
| Unit statement coverage `app/` | 1707 / 2410, 70,83% (интеграция/E2E не включены в эту цифру) |
| Docker Compose | Изолированные образы пересобраны, сервисы прошли healthcheck |
| Изоляция fake provider | `e2e_app` отсутствует в штатном backend-образе |
| Live Codex | Не запускался; отказ без ACK проверен |

Итого: 212 успешно выполненных тестов разных уровней. Python 3.12.10, Node 22.22.2, Playwright 1.63.0, Docker Engine 29.6.1. Рабочий сайт на 5173 дополнительно подтвердил HTTP 200 и `text/html`; пользовательские документы не использовались для приёмки. После успешной приёмки рабочие API/web пересобраны без удаления их volumes: API/БД healthy, web running; сайт отвечает HTTP 200, тестовый control endpoint — 404. Все volumes проекта document-checker-e2e удалены, диагностические артефакты сохранены локально.

### Объём и воспроизведение

Матрица требований: [docs/M01_ACCEPTANCE_MATRIX.md](docs/M01_ACCEPTANCE_MATRIX.md). Инструкция и opt-in live Codex: [docs/E2E_TESTING.md](docs/E2E_TESTING.md).

```powershell
pwsh -File scripts/test-e2e.ps1
```

Скрипт генерирует 37 синтетических файлов, проверяет unit/contract/build, пересобирает отдельный Compose-проект `document-checker-e2e` на 5174, запускает Chromium E2E и API-интеграцию. Его база, документы, preferences и embedding cache изолированы. Рабочая библиотека и авторизация Codex не подключаются. Очищаются только volumes тестового проекта.

Проверены PDF, DOCX, TXT, MD, CSV, XML, XLSX, XLS, PPTX, HTML, HTM, JSON, EPUB, cp1251/UTF-8, разные CSV-разделители и кавычки, большие таблицы, несколько страниц, листов, слайдов и глав. Негативные сценарии включают пустой, сверхлимитный, повреждённый, зашифрованный, неверно названный файл, нечитаемый скан, DTD/XXE и активный HTML.

Парсеры, MarkItDown, embeddings, PostgreSQL, OCR, сохранение истории и браузерные renderer работают реально. Только облачный Codex заменён детерминированным тестовым провайдером; отдельные fault-сценарии намеренно вызывают ошибку конвертера или загрузки original. Test provider исключён из штатного Docker-образа и подключается только отдельным entrypoint и read-only mount.

### Найденные и исправленные регрессии

1. Конвертация MD записывала производный файл по пути оригинала `<id>.md`, меняла кодировку; cleanup fallback мог удалить оригинал. Артефакт теперь `<id>.markdown.md`. Unit проверяет неизменность байтов при success/fallback; E2E сравнивает original с каждым успешным upload.
2. Комбинированные XML/JSON sources не подсвечивались, поскольку их текст не является непрерывной строкой в исходнике. Viewer ищет отдельные значения; XML учитывает native-аннотации атрибутов.
3. Быстрый rebuild Markdown между двумя опросами API не обновлял содержимое viewer. Derived data теперь перечитываются при изменении `updated_at`, история чата остаётся доступной.
4. Карта XLS/XLSX пропускала строки из-за native подписей колонок и различия `10.0`/`10`. Сопоставляются упорядоченные значения ячеек; связанный диапазон строк расширяется до всех совпавших строк одного листа.
5. Карта XML пропускала узлы с атрибутами: native `(role=executor)` отличалось от атрибута перед текстом в XML. Сопоставление сохраняет все четыре узла тестового документа, включая автора и срок.

Также добавлена явная настройка разрешённых локальных origins для отдельного тестового порта; production default 5173 не меняется.

### Диагностика и границы

- В обычном наборе нет ретраев и skip. Состояния ожидаются по API/DOM; управляемый stream gate подтверждает промежуточный ответ до завершения.
- Проверяются canvas с настоящими пикселями PDF, DOCX renderer, таблицы и исходные строки, переходы original/Markdown и citations, семь insights, GFM и безопасность ответа, reload и реальный restart тестовых api/db/web, новый чат, удаление/cancel, модели/reasoning и обе панели.
- Размеры: 1440×900, 1280×720, 1024×768, 768×1024, 430×932, 390×844. Сохраняются screenshots; проверяются fit DOCX, отсутствие общего horizontal overflow и reduced motion. Дополнительный просмотр CLI screenshots выполнен на desktop/mobile.
- На ошибках сохраняются trace, video, screenshot и browser-log; uncaught pageerror проваливает тест. Compose logs, JUnit/HTML и unit coverage остаются локальными артефактами, не попадают в Git.
- Облачный live Codex не запускался: он требует отдельного входа и явного `E2E_LIVE_CODEX_ACK`. Проверен отказ конфигурации без ACK. Fake ответы подтверждают механику, а не качество настоящей модели.
- Build сохраняет предупреждение Vite о JS-bundle больше 500 kB; оптимизация больших документов и загрузки кода относится к последующим milestones, предупреждение не отключалось.
- Покрытие не равно 100%. Не заявляются все возможные документы, другие браузерные движки, pixel-perfect Word/PowerPoint/EPUB, OCR любой сложности, word boxes, переключение листов таблицы и незавершённые задачи следующих milestones.
- Уже утраченные до исправления байты MD нельзя восстановить автоматически. Перезапуск/повторный анализ не возвращает отсутствующий оригинал.
- В рамках M01 M02 и последующие milestones не реализовывались. Результаты M02 приведены отдельно в начале отчёта.

---
## Исторический отчёт — 29 сентября 2026 года
Дата прогона: 29 сентября 2026 года.

## 1. Цель проверки

Проверить причину сообщения «Не удалось обработать документ», воспроизвести её на поддерживаемых форматах, проверить локальное извлечение текста и индексацию, а также убедиться, что после исправлений работают анализ документа, цитаты и потоковый чат.

Тестовые файлы синтетические: они содержат только вымышленные названия, имена и даты. Реальные пользовательские документы в тесты не включались.

## 2. Найденные причины исходной ошибки

### 2.1. Неподдерживаемая модель эмбеддингов

В логах API ошибка возникала до анализа документа, во время построения локальных векторов:

```text
ValueError: Model intfloat/multilingual-e5-small is not supported in TextEmbedding.
```

Идентификатор `intfloat/multilingual-e5-small` отсутствовал в реестре моделей установленного FastEmbed 0.8.1. Поэтому любой успешно распарсенный документ падал на одном общем этапе индексации. Обработчик превращал исключение в общее сообщение, из-за чего в интерфейсе не было видно технической причины.

### 2.2. Несовместимость проверки reasoning в Codex SDK

У текущей версии `openai-codex` поле варианта reasoning называется `reasoning_effort`, а код обращался к старому имени `effort`. Endpoint `/api/v1/codex/status` завершался с `500`, поэтому интерфейс показывал некорректный статус подключения.

### 2.3. Права кэша Hugging Face в контейнере

При первой загрузке исправленной модели FastEmbed пытался создать служебные файлы в `/app/.cache`. API работает от непривилегированного пользователя, поэтому скачивание завершалось `Permission denied`. Это было отдельной причиной, которая проявлялась после исправления имени модели.

## 3. Выполненные исправления

- Заменена модель на зарегистрированную мультиязычную `sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2` размерности 384. Размерность совпадает с колонкой `pgvector`.
- Перед скачиванием модели добавлена проверка её наличия в реестре FastEmbed и соответствия размерности. При ошибке показывается конкретное сообщение о конфигурации.
- Убраны неподходящие для выбранной модели префиксы `passage:` и `query:`; векторизуется исходный текст.
- Ошибка конфигурации эмбеддингов больше не маскируется общим текстом обработки.
- Проверка reasoning в Codex поддерживает новое поле SDK и сохраняет совместимость со старым полем.
- HF-кэш направлен в `/data/embeddings/huggingface`, входящий в writable Docker volume и доступный пользователю API-контейнера.
- В Compose добавлена переменная `WEB_PORT`, чтобы поднимать изолированный тестовый стек рядом с рабочим приложением.
- Интеграционные тесты обращаются к локальному стенду без наследования системного HTTP-прокси (`trust_env=False`). Это важно для Windows-сред, где `localhost` может быть ошибочно направлен через прокси.
- Добавлены детерминированные тестовые документы и подробный набор unit/integration-проверок.

Реестр и реализация FastEmbed проверялись по официальному исходному коду проекта: [TextEmbedding](https://github.com/qdrant/fastembed/blob/main/fastembed/text/text_embedding.py), [README FastEmbed](https://github.com/qdrant/fastembed/blob/main/README.md).

## 4. Тестовые документы

В `backend/tests/fixtures/` добавлены:

| Файл | Что проверяется |
|---|---|
| `sample.pdf` | Текстовый PDF, координаты страницы, автор, проверяющий, срок |
| `sample.docx` | Абзацы, заголовок, таблица и координаты строк |
| `sample.txt` | UTF-8 и обычные диапазоны строк |
| `sample.md` | Заголовки Markdown и диапазоны строк |
| `sample.csv` | UTF-8 BOM, разделитель `;`, строки и точные агрегаты |
| `sample.xml` | Вложенные элементы, атрибуты и XPath-подобные локаторы |

Дополнительно тест создаёт DOCX размером около 2,8 МБ с синтетическим бинарным вложением. Он воспроизводит размер документа со скриншота, не изменяя его содержимое и не добавляя реальных данных.

## 5. Матрица автоматических проверок

### 5.1. Unit и parser tests

Команда:

```powershell
$testsPath = "$(Get-Location)\backend\tests"
docker compose run --rm --no-deps --user root -v "${testsPath}:/app/tests:ro" api sh -c "pip install --no-cache-dir pytest >/dev/null && PYTHONPATH=/app pytest -q"
```

Актуальный прогон в API-контейнере (с тестами, смонтированными в контейнер) завершился с результатом **51 passed, 6 skipped** за 1,82 секунды. Пропущены только интеграционные тесты, потому что в этой команде не задан `AI_CHECKER_BASE_URL`.

Проверены:

- извлечение и координаты источников для PDF, DOCX, TXT, MD, CSV и XML;
- абзацы и таблицы DOCX;
- разделитель CSV, BOM, кодировка CP1251, большие строки и точные `sum/average/minimum/maximum` через `Decimal`;
- вложенные XML-пути и отклонение внешних сущностей;
- повреждённый, сканированный и зашифрованный PDF;
- пустые и некорректные входные данные;
- семь общих вопросов и семь вопросов, адаптированных к CSV;
- отсутствие числовых столбцов в CSV;
- проверка зарегистрированной модели эмбеддингов и размерности 384;
- отказ до скачивания при неизвестной модели или неверной размерности;
- преобразование и фильтрация ссылок на источники.

### 5.2. Реальный API в Docker Compose

Для этой проверки использовался отдельный Compose-проект с отдельными PostgreSQL, upload, Codex и embedding volumes. В нём нет авторизации Codex, поэтому синтетический текст не отправлялся в облачную модель.

Команда:

```powershell
$env:WEB_PORT = "5174"
docker compose -p document-checker-integration up --build -d
$env:AI_CHECKER_BASE_URL = "http://localhost:5174"
python -m pytest -q backend/tests/test_compose_workflow.py -m integration
docker compose -p document-checker-integration down -v
```

Актуальный прогон целевого файла интеграционных проверок завершился с результатом **6 passed** за 36,32 секунды.

| Проверка | Результат |
|---|---|
| Контракт `/api/v1/codex/status` | PASS; возвращаются authenticated, модель, reasoning и флаги доступности |
| Все 6 поддерживаемых форматов | PASS; каждый файл распарсен, проиндексирован, получил фрагменты и локаторы; ожидаемое состояние без входа Codex — `needs_auth` |
| DOCX около 2,8 МБ | PASS; размер сохранён, текст и таблица извлечены |
| Неподдерживаемый `.exe` и файл больше 25 MiB | PASS; получены 415 и 413, записи в БД не появились |

После прогона тестовый проект остановлен командой `docker compose -p document-checker-integration down -v`. Рабочие volumes проекта `lab2ai_document-checker` не удалялись.

### 5.3. Локальная модель в рабочем контейнере

В рабочем API-контейнере выполнен smoke-test:

- модель: `sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2`;
- вектор фрагмента: 384 значения;
- вектор запроса: 384 значения;
- значения конечные, без `NaN`;
- модель скачана в `/data/embeddings` без ошибок прав доступа.

### 5.4. Полный AI smoke-test

На рабочем Compose с подключённым Codex был загружен один синтетический DOCX. Проверены все облачные этапы:

```text
status=ready
chunks=7
insights=7
cited_insights=7
chat_events=['delta', 'done', 'sources', 'thread']
chat_messages=2
assistant_citations=2
```

То есть семь карточек созданы, каждая имеет подтверждающий источник, SSE-чат отдал потоковые события `sources/thread/delta/done`, история содержит вопрос и ответ, ответ имеет две цитаты. Тестовый документ после проверки удалён.

### 5.5. Frontend и статические проверки

- `npm run build` в `frontend/`: **PASS** (`tsc --noEmit` и `vite build`);
- `ruff check app migrations tests`: **PASS**;
- `python -m compileall -q app migrations tests`: **PASS**;
- `docker compose config --quiet`: **PASS**;
- генерация всех шести fixtures: **PASS**.

### 5.6. Предпросмотр оригинала и переходы по цитатам

Добавлен endpoint `GET /api/v1/documents/{id}/preview` и защищённая отдача исходного файла `GET /api/v1/documents/{id}/file`.

| Формат | Представление | Проверка |
|---|---|---|
| PDF | исходный файл с переходом по странице и A4/реальным соотношением сторон | PASS; `application/pdf`, `page_count`, `page_width/page_height`, citation page jump |
| DOCX | бумажное полотно, абзацы и строки таблиц | PASS; якорь ведёт к подсвеченному абзацу/строке |
| TXT/MD | бумажное полотно с диапазонами строк | PASS; сохранены line locators |
| CSV | таблица с заголовками, строками и отдельными локальными расчётами | PASS; ячейки восстановлены без участия модели, расчёты помечены |
| XML | дерево значений с XPath-подобными локаторами | PASS; переход ведёт к узлу |

Рабочий Docker Compose smoke-test загрузил синтетические `sample.pdf`, `sample.docx`, `sample.txt`, `sample.md`, `sample.csv` и `sample.xml`: каждый файл получил непустой preview, ожидаемый layout и доступный оригинал. Файлы удалены после проверки, рабочая библиотека оставлена пустой.

В браузере дополнительно проверены:

- переход по citation `Абзац 3` с автопрокруткой и выделением блока;
- переход к расчёту CSV с прокруткой к расчётному источнику;
- встроенный PDF с fallback-текстом для окружений без встроенного PDF-плагина;
- пустое состояние, загрузочная панель и отсутствие ошибок `console`.

### 5.7. Устойчивые чаты и обновление страницы

Добавлен endpoint `GET /api/v1/chats`, который собирает библиотеку из сохранённых чатов и документов. Новый чат создаётся сразу после загрузки файла, поэтому в истории видны также документы, которые ещё обрабатываются или требуют входа Codex. Для старых документов без записи чата связь восстанавливается при запуске API.

Проверено:

- интеграционный тест загрузил документ, получил его в библиотеке чатов, повторно запросил библиотеку новым HTTP-запросом и подтвердил тот же `chat_id`, название, статус и нулевую историю сообщений;
- unit-тесты проверяют название по первому вопросу, последнюю активность, число сообщений и fallback на имя файла;
- фронтенд сохраняет выбранный `document_id` в `localStorage`, восстанавливает его после reload и загружает сообщения из серверной истории;
- после завершения ответа список чатов обновляется, поэтому новая активность и заголовок видны без ручного обновления.

### 5.8. Повторная браузерная проверка и адаптивная верстка

На рабочем стенде `http://localhost:5173` вручную проверены пустое состояние и готовый чат с синтетическим `sample.txt`. После обработки отображаются семь карточек, оригинальное полотно, цитаты с переходом к подсвеченному фрагменту и быстрые вопросы. После `reload` выбранный чат и все результаты восстановились из PostgreSQL и `localStorage`. Тестовый документ удалён после проверки.

Проверены размеры CSS viewport 1440, 960, 800 и 588 px:

- на широком экране видны библиотека, рабочая область и панель чата; чат меняет ширину мышью и клавиатурой и раскрывается на всю рабочую область;
- на планшетной ширине библиотека сворачивается до 72 px, а чат становится отдельной панелью;
- на мобильной ширине остаётся одна активная область, библиотека и чат открываются выдвижными панелями с семантическим затемняющим слоем;
- в проверенных состояниях `document` не имел горизонтального переполнения (`scrollWidth === clientWidth`), панели не выходили за границы viewport;
- модальное окно Codex помещается в viewport, получает фокус на кнопке закрытия, удерживает Tab-фокус внутри и закрывается по Escape;
- быстрый вопрос переводит фокус в поле сообщения, а skip-link переводит фокус на `main`.

### 5.9. Аудит дизайн-системы и доступности

Сверка выполнена с `DESIGN-uber.md` и актуальными правилами [Web Interface Guidelines](https://raw.githubusercontent.com/vercel-labs/web-interface-guidelines/main/command.md). В интерфейсе сохранены Inter, монохромная палитра без акцентного цвета, базовый шаг 4 px, карточки с радиусом 16 px, поля с радиусом 8 px, интерактивные пилюли и плоская иерархия поверхностей.

В ходе аудита внесены исправления:

- библиотека получила ширину 240 px по ТЗ и сворачивается до 72 px; центральная область ограничена 1200 px;
- добавлены skip-link, `main` с идентификатором, явные labels для file input, `name`/`autocomplete` для чат-поля, `aria-describedby` для диалогов и `aria-live` для toast;
- мобильный scrim заменён на доступную кнопку, у мобильного чата добавлены `aria-expanded` и `aria-controls`;
- модальные окна получили ограничение высоты, внутреннюю прокрутку, overscroll containment, фокус-трап и видимую клавиатурную навигацию;
- заголовки используют sentence case и `text-wrap: balance`, быстрые вопросы и вторичные действия оформлены пилюлями, для модального окна и toast добавлена зарезервированная системой тень;
- сохранена поддержка `prefers-reduced-motion`, горизонтальное переполнение и `transition: all` отсутствуют.

## 6. Итог

Исходная ошибка воспроизводилась и была устранена на уровне причины: приложение больше не использует отсутствующую модель FastEmbed, проверяет конфигурацию до индексации и сохраняет кэш модели в доступном volume. Рабочий Docker Compose остаётся запущенным на `http://localhost:5173`; после тестов библиотека чатов пуста, тестовые документы удалены. История каждого документа теперь представлена отдельным чатом и восстанавливается после обновления страницы. Просмотр документа не ограничивается списком извлечённых фрагментов: у всех поддерживаемых форматов есть citation-aware полотно, а PDF дополнительно отдаётся как исходный файл.

Облачная генерация проверена на синтетическом DOCX с текущими `gpt-6-luna` и `medium`. Оставшееся внешнее условие работы — интернет при первом скачивании локальной модели и действующая авторизация Codex; при недоступности аккаунта приложение теперь показывает отдельное состояние `needs_auth`, а не общее «Не удалось обработать документ».

## 7. Финальная проверка надёжности — 30 сентября 2026 года

После доработки просмотрщика и пересборки Compose выполнены актуальные проверки:

| Проверка | Результат |
|---|---|
| Backend unit/parser/API tests | **128 passed, 7 skipped**; пропуски — только интеграционные сценарии без `AI_CHECKER_BASE_URL` |
| Форматы, preview и MarkItDown | **67 passed**; проверены PDF, DOCX, TXT, MD, CSV, XML, XLSX, XLS, PPTX, HTML, JSON и EPUB |
| Frontend contract tests | **11 passed** |
| Frontend TypeScript/Vite build | **PASS**; остаётся только информационное предупреждение о размере PDF.js bundle |
| Ruff | **PASS** |
| Compose integration | **4 passed, 3 skipped**; три fixture-сценария не отправляются в подключённую облачную модель |
| Рабочий Compose | **PASS**; API healthy, web доступен на `http://localhost:5173` |

В ходе проверки исправлены две причины нестабильности:

- endpoint исходного файла теперь возвращает корректный MIME для каждого поддерживаемого расширения. В частности, DOCX отдаётся как `application/vnd.openxmlformats-officedocument.wordprocessingml.document`, поэтому браузер не должен ошибочно предлагать скачать файл вместо открытия;
- DOCX-renderer больше не изменяет React-узел напрямую. Рендер выполняется во временном detached host, поддерживает отмену устаревшего запроса и переносит готовые узлы в viewer только после успешного завершения. Это устраняет гонку, которая приводила к `Failed to execute 'removeChild' on 'Node'` и белому экрану;
- запросы таблиц CSV/XLSX/XLS защищены от устаревших ответов при быстрых переходах по citation и пагинации.

В браузере после пересборки проверены готовый чат с DOCX, оригинальное полотно, Markdown-вкладка, citation-переход, сворачивание библиотеки, мобильное состояние, отсутствие горизонтального overflow и отсутствие ошибок в console. Реальное содержимое DOCX отображается как документ с абзацами и таблицами, а не как список chunks.

## 8. Масштабирование оригинального DOCX в узкой колонке

Для DOCX добавлено вычисление масштаба страницы по фактической ширине viewer через `ResizeObserver`. Страница сохраняет пропорции, уменьшается до доступной ширины левой колонки и продолжает прокручиваться по вертикали внутри ограниченного окна. При расширении рабочей области масштаб автоматически возвращается к 100%.

Проверено в браузере при свернутой библиотеке и открытом чате:

- 1920×1080: страница DOCX — 531 px внутри viewer шириной 567 px;
- 430×932: страница DOCX — 346 px внутри viewer шириной 382 px;
- горизонтальное переполнение viewer и страницы отсутствует.

## 9. M11 — организация библиотеки чатов — 2 октября 2026 года

Добавлены серверная пагинация и поиск по названию чата, имени исходного файла и истории сообщений. Результат показывает snippet; открытие совпадения загружает нужную переписку, прокручивает к сообщению и временно выделяет его. Название чата можно изменить или сбросить к исходному; закреплённые чаты сортируются выше остальных. PATCH использует `expected_revision`, чтобы обнаруживать параллельные изменения и сохранять введённый текст при конфликте. При загрузке приложения запрашиваются только страницы сводок, а не тела переписки всех чатов. Действия доступны в компактной библиотеке с клавиатуры и на touch-экранах.

Проверки:

| Проверка | Результат |
|---|---|
| Backend M11 unit tests (`test_chat_library.py`) | **6 passed**; запуск в одноразовом контейнере на runtime-образе API |
| Frontend unit/contracts (`npm test`) | **19 passed** |
| Frontend build (`npm run build`) | **PASS**; только штатное предупреждение Vite о размере крупного bundle |
| M11 Playwright E2E (`chat-library.spec.mjs`) | **1 passed**; поиск сообщения за первой страницей, переход/highlight, пагинация, pin, rename, конфликт revision, компактная и мобильная библиотека |
| Compose migration/API | **PASS**; миграция `0008_chat_library_management` применена, paginated endpoint возвращает страницу и общее число |

Локальный полный backend suite в этой среде не запускался: системный Python не содержит полного набора backend-зависимостей (`pgvector` отсутствует), а API Compose работает с read-only rootfs. Целевые M11-тесты запущены в изолированном одноразовом контейнере с кодом теста, смонтированным только для чтения. Проверка M11 не закрывает ожидающую зависимость M09 и не меняет статус приёмки M10.

## 10. M12 — сообщения, контекст и повторный анализ — 2 октября 2026 года

M12 добавляет долговечные состояния генерации сообщений, связь вариантов ответа с исходным вопросом, границы контекста, физическое удаление Q&A-пары, остановку/повтор ответа и версии анализа. Для истории prompt используются сообщения только текущего контекста, не более 32 элементов и 12 000 символов; неполные ответы исключены. Ответы и версии анализа хранят выбранную модель, reasoning и версию источников.

Повторный анализ использует текущую индексированную версию и не запускает снова OCR, MarkItDown или embeddings. Предыдущие карточки остаются активными до завершения новой версии. API отклоняет отсутствующую в каталоге модель, проверяет исходную версию и не изменяет сохранённые предпочтения при одноразовом выборе.

Проверки:

| Проверка | Результат |
|---|---|
| Backend unit suite (`python -m pytest -q`) | **279 passed, 10 skipped**; пропущены Compose-интеграционные тесты без `AI_CHECKER_BASE_URL` |
| Backend M12 context/Codex/processing tests | **47 passed**; включены проверки одноразового выбора модели и отказа без fallback |
| Frontend unit/contracts (`npm test`) | **19 passed** |
| Frontend TypeScript/Vite build (`npm run build`) | **PASS**; только существующее предупреждение о крупном PDF.js bundle |
| Ruff и Python compileall | **PASS** на изменённых backend-файлах |
| Compose migration и health | **PASS**; изолированный стек здоров, Alembic `0009_chat_context_analysis` — head |
| Browser E2E: M12 + библиотека | **3 уникальных теста прошли**: граница контекста, удаление пары по ID ответа, переанализ с выбранными `gpt-6.1-sol/high`, удержание старых карточек, stop/retry и регрессия библиотеки |
| Mobile layout | **PASS**; на viewport 390×844 viewer/control отображаются без горизонтального overflow |

Браузерные проверки выполнялись на отдельном Compose-стеке с синтетическим Codex-провайдером. Они подтверждают API/UI-поведение и передачу выбранных параметров через worker, но не заменяют проверку доступности настоящего аккаунта Codex. Сохранённые версии и сообщения проверены после reload. Рабочие volumes и основное приложение не затрагивались.

API-контракт и семантика удаления/контекста/повторного анализа задокументированы в [docs/M12_CHAT_CONTEXT.md](docs/M12_CHAT_CONTEXT.md). Статус следующего milestone не менялся.

## 11. M13 — сортировка, фильтры и проверяемые расчёты таблиц — 2 октября 2026 года

Для CSV/XLSX/XLS добавлены backend-запросы над полным набором строк, выбор листа, сортировка по числу/дате/тексту, фильтры по тексту/числу/пустым ячейкам и переход к исходной строке после сортировки. Цитаты и поиск сохраняют те же физические номера строк даже при пустых CSV-записях. Расчёты количества строк, непустых/числовых/нечисловых значений, суммы, среднего, минимума и максимума выполняются в приложении с Decimal; среднее округляется до 2 знаков по ROUND_HALF_UP. Суммы для всего документа и фильтра сохраняются раздельными derived sources с листом, диапазоном строк, фильтром, контрольной суммой и правилом округления. Формулы XLSX не исполняются, различаются формула и кэш. В XLS отображаются сохранённые значения; библиотека не раскрывает формулы XLS для отдельного определения их кэша.

Проверки:

| Проверка | Результат |
|---|---|
| Backend unit suite (`python -m pytest -q`) | **291 passed, 10 skipped**; пропуски — Compose integration без `AI_CHECKER_BASE_URL` |
| Табличные, поисковые и parser regression tests | **32 passed**; локализованные Decimal, даты, пустые/нечисловые строки, CSV с quoted delimiters и CP1251, XLS, формулы и cached values, координаты после сортировки/фильтра |
| Frontend unit/contracts (`npm test`) | **19 passed** |
| Frontend TypeScript/Vite build (`npm run build`) | **PASS**; сохраняется предупреждение Vite о крупном PDF.js bundle |
| Ruff и Python compileall | **PASS** для изменённых backend-файлов |
| Compose E2E browser | **5 passed**: полный поиск/фильтрация/сортировка/два сохранённых расчёта/reload и mobile width; переключение документа сбрасывает состояние viewer; CSV-цитата за первой страницей сбрасывает скрывающий фильтр; XLSX/XLS переходят на правильный лист и исходную строку |
| Backend Compose integration (`AI_CHECKER_BASE_URL=http://localhost:5174`) | **5 passed, 5 skipped**; пропуски исключают синтетический текст fixture из вызова подключённой облачной модели |
| Compose health | **PASS**; API, worker, web и база здоровы; именованные volumes тестового проекта сохранены |

Браузерная проверка подтвердила, что таблица и расчётные элементы доступны на viewport 390×844 без горизонтального переполнения. При цитате фильтр, скрывающий исходную строку, сбрасывается с понятным уведомлением. Для XLSX отсутствие cached value обозначается явно, а формулы не рассчитываются. Следующим milestone M14 не начинался.

## 12. M14 — пакетная обработка и производительность — 2 октября 2026 года

Для нескольких файлов добавлена очередь с независимым прогрессом, отменой, повтором, заменой невалидного локального файла и восстановлением состояния после reload. Общий лимит — не более двух одновременных upload POST даже при добавлении второй партии в уже работающую очередь. Backend сохраняет multipart upload чтением по 1 MiB с проверкой лимита 25 MiB до записи лишних данных; отдельный resumable-протокол при этом размере файла не понадобился.

Виртуализированы длинные Markdown-документы и CSV-таблицы. CSV viewer ограничен адаптивной высотой, поэтому область прокрутки больше не разрастается вместе с таблицей. PDF viewer отображает текущий canvas страницы и меняет/освобождает его при навигации. Кеши с ограниченным размером теперь покрывают native parser, OCR, MarkItDown, карту соответствий, embeddings, preview, CSV table preview и CSV table search. Ключи отделяют checksum и версии зависимых parser/OCR/converter/embedding-конфигураций; удаление документа очищает его ответы из процессных кешей.

### Проверки

| Проверка | Результат |
|---|---|
| Backend unit suite в одноразовом runtime-контейнере | **309 passed, 10 skipped**; пропуски — integration tests, запускаемые отдельной Compose-командой |
| Compose integration (`AI_CHECKER_BASE_URL=http://web`) | **5 passed, 5 skipped**; пропущены сценарии, которым потребовался бы настоящий подключённый Codex |
| Frontend unit/contracts (`npm test`) | **19 passed** |
| Frontend TypeScript/Vite build (`npm run build`) | **PASS**; остаётся предупреждение Vite о bundle больше 500 kB |
| Полный Playwright E2E, 84 теста | **83 passed**; один тест зафиксировал устаревшее ожидание фиксированной модели Luna при runtime-настройке Sol. Проверка исправлена на фактическую модель `/codex/status` и отдельно прошла повторно. Все 84 сценария проверены; полный прогон после изменения только этого ожидания не перезапускался |
| M14 browser regressions | **3 passed**: независимые результаты/повтор после reload; две параллельные партии с измеренным глобальным лимитом `<= 2`; виртуализация 1 200 строк Markdown с переходом к последней строке |
| Responsive и исправленные ранее UI-проверки | **PASS**: 1440×900, 1280×720, 1024×768, 768×1024, 430×932 и 390×844; CSV viewport, экспорт, ошибки очереди и dropdown-проверки проверены после исправлений |
| Изолированный Compose после пересборки | **PASS**; API, worker, web и PostgreSQL healthy на `localhost:5174`, пользовательский стек и volumes не затрагивались |

Первый полный запуск завершился 83/84: единственный сбой был в assertion, жёстко ожидавшем `gpt-6-luna`, тогда как E2E runtime настроен на `gpt-6.1-sol`. UI корректно отражал серверную настройку; тест теперь сравнивает значение с `/codex/status.model`, после чего сценарий повторно прошёл. Итого каждый из 84 случаев прошёл хотя бы один раз; после правки теста запускался только этот сценарий, не полный набор.

### Измерения

Benchmark выполнил две одновременные загрузки (`mixed.pdf`, 100 697 байт; `large.csv`, 4 846 байт), измерил стадии обработки, по четыре запроса к preview/Markdown/search/table, первый рендер PDF и непрерывно семплировал Docker memory примерно раз в секунду. Оборудование, версии, ограничения набора и сравнительная таблица приведены в [docs/performance/M14.md](docs/performance/M14.md); сырые данные — [baseline](docs/performance/m14-before.json) и [после](docs/performance/m14-after.json).

Повторный поиск и чтение страницы таблицы CSV уменьшились с медиан около 700/656 ms до 7/7 ms благодаря кешу ответов; первое чтение осталось около 668/669 ms, то есть результат не скрывает время первого холодного прохода. Обработка небольших PDF/CSV regression fixtures уменьшилась примерно с 10,7/11,2 s до 2,6/2,1 s на уже прогретых артефактах; отдельный холодный прогон занял 12,1/11,2 s, а его исходные данные сохранены вместе с benchmark отчетом. Пиковая выборка памяти worker снизилась с 2,17 GB до 0,90 GB. Эти документы не являются нагрузочным тестом файла 25 MiB; большие области DOM отдельно проверены на 1 200 строках Markdown и 240 строках CSV.

Браузерные E2E используют только изолированный синтетический provider: результаты подтверждают локальный путь parsing/OCR/cache/queue/UI, но не являются benchmark настоящего облачного ответа Codex.

## 13. M15 — локальные данные, обслуживание и диагностика — 2 октября 2026 года

Реализованы сводка локального хранилища, приватный диагностический ZIP, журнал обслуживания, предварительный просмотр и явное подтверждение очистки/удаления, выбор документов с пагинацией, отмена связанных jobs, очистка временных файлов и восстанавливаемого кэша, а также документация резервного копирования и восстановления. Удаление сверяется с актуальными DB/file fingerprints и ограничивает затрагиваемые объекты. Архив диагностики содержит только версии/build IDs, health-состояние, агрегированные стадии/error codes и timings.

Проверки выявили и исправили два дефекта: `secrets.compare_digest` не сравнивает кириллические строки напрямую, поэтому подтверждение теперь сравнивается как UTF-8 bytes; очистка кэша ранее рекурсивно удаляла веса локальной модели, хотя индексатор запущен без доступа к сети. Теперь очистка охватывает только allowlist кэшей парсеров и векторов, а модель показывается по размеру и защищена для непрерывной офлайн-обработки.

| Проверка | Результат |
|---|---|
| Backend unit suite (`python -m coverage run --source=app -m pytest -q -m "not integration"`) | **320 passed, 10 deselected**; integration/E2E запускаются из Compose |
| M15 unit + приватное логирование ошибок | **12 passed**: каталог кэша, защита model files, файловые границы и подпись оригиналов, кириллическое подтверждение, privacy-safe locations |
| Frontend contracts (`npm test`) | **22 passed** |
| TypeScript / production build (`npm run build`) | **PASS**; предупреждение Vite о bundle более 500 kB остаётся |
| Linux security acceptance | **10 passed**: сетевые/ресурсные лимиты, multipart, descriptor/symlink/hardlink защиты и безопасная ошибка worker |
| M15 Chromium acceptance | **1 passed**: точный предпросмотр и отмена без изменений, diagnostics download, desktop и mobile viewport |
| M15 Compose API acceptance | **1 passed**: диагностический ZIP без секретов, temp/cache cleanup, сохранность originals/chat/auth, rebuild после очистки, неверная фраза, выборочное и полное удаление, отмена queued job и пустая библиотека |
| Изолированный PostgreSQL + documents backup/restore | **PASS** на отдельных временных volumes; подтверждены восстановление строки и файла с контрольными маркерами |
| Diff whitespace (`git diff --check`) | **PASS** |

Тесты выполнялись только в Compose-проектах с уникальными GUID и отдельными volumes. Рабочие данные не использовались. M16 и итоговая проверка M09/M10 выполнены после этого этапа; результаты зафиксированы ниже.

## 14. M09 и M10 — закладки, дополнительные анализы и экспорт — 2 октября 2026 года

M09 завершён: источники сохраняются как закладки с точным source ID и версией обработки, заметки редактируются отдельно, переход возвращает к исходному фрагменту. Режимы «Кратко», «Подробно», «Задачи», «Риски и неясности» запускаются только по явному действию, не заменяют семь базовых карточек и сохраняют модель, reasoning, версии и подтверждённые citation IDs. Отсутствующие исполнитель/срок не выдумываются, повторный анализ создаёт новый результат. Добавлены отдельные настройки максимального количества источников и длины каждого источника.

M10 завершён: экспорт Markdown/PDF строится из сохранённой версии без вызова Codex. Пользователь выбирает весь анализ, отдельные карточки или переписку; при экспорте анализа можно включить/исключить отдельные сохранённые результаты M09. Источники и выдержки сверяются с серверными IDs/версиями, HTML и ссылки обрабатываются безопасно, имена файлов очищаются.

| Проверка | Результат |
|---|---|
| Backend unit suite в изолированной venv | **336 passed, 10 deselected** (`-m "not integration"`); предупреждение Alembic только о неуказанном `path_separator` |
| Frontend contracts (`npm test`) | **25 passed** |
| TypeScript / Vite production build | **PASS**; остаётся предупреждение о JS chunk >500 kB, обусловленное PDF.js |
| Полный Playwright Chromium | **88 passed**; среди них закладки/заметки, четыре режима анализа, citations, экспорт M09/PDF/Markdown, оригиналы и Markdown для 13 форматов, OCR, error/fallback, reload/restart и сетки |
| Responsive viewports | **PASS** на 1440×900, 1280×720, 1024×768, 768×1024, 430×932 и 390×844; отдельно проверена малая высота desktop без прокрутки всей страницы |
| Linux security acceptance | **10 passed**: ограничения сети/ресурсов и вывода, multipart upload, descriptor/symlink/hardlink, граница ошибок worker |
| Compose migration acceptance | **PASS**: downgrade `0011 → 0010`, последующий upgrade до `0011`; сохранены исходные байты, legacy document, чат, сообщения, citations и locator без OCR boxes |
| Queue acceptance | **9 passed**: конкурентные заявки/позиции, lease/fencing/retry, restart, OCR cancellation, сохранение активной версии и удаление |
| API integration | **10 passed, 336 deselected**; все выполнялись с disconnected test provider |
| M15 maintenance API acceptance | **1 passed**; backup/restore PostgreSQL и document volume — **PASS** на одноразовых проектах и отдельных volumes |
| Live Codex account | **Не запускался**: отдельная opt-in проверка требует авторизованного пользовательского Codex; synthetic provider не считается live-проверкой |

## 15. M16 — финальная приёмка и обновление установки — 2 октября 2026 года

Полный `scripts/test-e2e.ps1` прошёл на уникальном Compose-проекте `document-checker-e2e-0cdd47e6`; тестовые volumes очищены самим скриптом. Проверка охватила пользовательский поток upload → original/Markdown → seven insights → citation/search → chat/reload/container restart → export. Страница проверялась во всех шести согласованных разрешениях. Playwright fixture завершает тест ошибкой при uncaught `pageerror`; M09/M10 E2E дополнительно проверили отсутствие console errors в закладках, citations, reload и экспортном потоке.

Миграция проверялась на созданных E2E документах/чатах, а не на пустой БД. Отдельный M15 backup/restore тест применял собственные временные volumes. Рабочие volumes и Codex auth не использовались тестами. Полная live-проверка настоящего Codex намеренно не запускалась и не объявляется пройденной.

Targeted browser reruns допускаются через `-PlaywrightGrep`. Migration acceptance в таком режиме явно пропускается, так как ограниченный набор тестов не обязан создавать сохраняемый чат с citation; полный запуск остаётся единственным режимом общей миграционной приёмки.

| Проверка | Результат |
|---|---|
| Полный acceptance runner | **PASS**: frontend contracts/build, backend unit, Compose/Linux security, Playwright, migration, queue, API integration, maintenance и backup/restore |
| Актуальный Compose образ приложения (`lab2ai_document-checker`) | **PASS**: пересобраны API/worker/web, PostgreSQL/API/worker/web прошли health/wait; миграция `0011` применилась к прежнему DB volume |
| Фактически отданная сборка | **PASS**: `build_id=m16-20261002-cd4d52d`, commit `cd4d52dc8af0fb1a9b0e6d0088ec8c276c34756d`; `/api/v1/version` и `/build-info.json` совпали, index и JS asset отвечали HTTP 200 |
| Проверка после restart production-сервисов | **PASS**: API и worker снова healthy, web running; build IDs/commit совпали, количество существующих документов и чатов не изменилось |
| Chromium smoke фактически установленного приложения | **PASS**: на 1440×900 и 390×844 React workspace отрисован, горизонтального overflow, `pageerror` и console errors нет |
| Git delivery | **PASS**: commit `cd4d52d` находится на `main` и успешно отправлен в `origin/main`; финальная запись M16 включена в следующий документационный commit |

## APP-M01 — каталог возможностей и AI-контракт — 2 октября 2026 года

Добавлен серверный каталог из 13 проверенных возможностей приложения. В записях хранятся стабильные IDs, русские фразы для поиска, инструкции и ограничения, ссылки на исходники, поверхность интерфейса и условия доступности. Каталог привязан к `APP_HELP_CATALOG_VERSION=1` и build ID. Retrieval ограничивает результат и не выбирает случайную функцию для опечатанного, неоднозначного или неподтверждённого вопроса. Pydantic/JSON Schema и серверная проверка ограничивают citations и UI targets текущими доступными доказательствами. Пользовательский поток документа не был переключён на новый контракт.

Для privacy проверена текущая граница отправки: анализ получает ограниченные найденные фрагменты; документный чат дополнительно передаёт вопрос и до 32 предыдущих сообщений этого же чата. Полный оригинал, полный индекс, другие чаты и пользовательские файлы не включаются в payload помощи по приложению. `openai-codex==0.158.0` подтверждён локально; contract tests зафиксировали `output_schema` для `.run()` и `.turn()`, потоковые JSON deltas и явный `ephemeral=True` для нового ephemeral вызова. Из-за частичных JSON deltas APP-M02 должен валидировать полный ответ до отправки текста клиенту. `ephemeral` не трактуется как гарантия отсутствия внешнего хранения.

| Проверка | Результат |
|---|---|
| Targeted backend tests (`test_app_help.py`, `test_codex_service.py`) | **53 passed** |
| Backend unit suite (`pytest -q -m "not integration"`) | **376 passed, 10 deselected**; единственное предупреждение — существующая настройка Alembic `path_separator` |
| Ruff для изменённых backend-файлов | **PASS** |
| `git diff --check` | **PASS**; Git вывел только предупреждения о нормализации CRLF |
| Compose/API/browser | **Не запускались**: milestone не меняет API, БД, миграции, контейнеры или frontend; интеграционные тесты остаются отдельной категорией |

## APP-M02 — помощь по приложению и документу в одном чате — 2 октября 2026 года

Реализованы самостоятельные persistent app-help чаты без `document_id`, миграция `0012_application_chats`, отдельный retrieval каталога возможностей и документных chunks, смешанные ответы с раздельными server-resolved citations и детерминированный отказ без выдуманного источника для неизвестной функции. App-help отображается в общей библиотеке, поддерживает rename/pin/delete и восстанавливается после reload; API не разрешает клиенту выбрать для него произвольный документ. Ответ доходит до UI только после полной проверки структурированного результата, а остановленный поток сохраняется как interrupted без частичного JSON-ответа. Проверка sources выполняется до фиксации ответа.

Дополнительно закрыта гонка при переключении с документа на app-help: позднее завершение document preview/insights/messages не перезаписывает app-chat state. Регрессионный браузерный тест удерживает запрос insights, переключает чат и завершает запрос после этого. README и [план APP assistant](docs/APP_ASSISTANT_PLAN.md) обновлены с границами контекста и ограничениями `ephemeral=True`. Реестр targets/highlight и автоматический поиск UI не реализовывались; это APP-M03.

| Проверка | Результат |
|---|---|
| Полный backend suite (`python -m pytest -q`) | **382 passed, 10 skipped**; skipped tests требуют Compose integration base URL |
| Frontend contracts (`npm test`) | **25 passed** |
| Frontend production build (`npm run build`) | **PASS**; Vite сообщает о bundle >500 kB, как и до M02 |
| App-help Chromium E2E | **4 passed**: persistent chat, citations, stop/retry, rename/pin/delete, reload, mobile layout, async race, app-only/mixed/document-only separation |
| API integration на изолированном Compose | **5 passed, 5 skipped**; пропуски требуют opt-in live Codex |
| Migration acceptance на изолированном Compose | **PASS**: upgrade сохраняет прежние document chats/messages/citations/original bytes; создание app-help без документа; downgrade с app-help сессией безопасно отклонён |
| `node --check` и `git diff --check` | **PASS** |

AI E2E выполнялись с синтетическим provider; проверка качества ответа на живом Codex не заявляется.

## APP-M03 — безопасные подсказки к элементам интерфейса — 3 октября 2026 года

Сохранённая сервером подсказка теперь включает стабильный UI target, версию каталога и build ID. `Message` ограничивает это поле согласованностью всех трёх nullable значений; миграция `0013_ui_target_metadata` безопасно отказывается терять сохранённые targets при downgrade. Frontend показывает действие только для известного ID при точном совпадении каталога и build. Цели регистрируются через React refs и semantic IDs; перед подсветкой проверяются уникальность, видимость, disabled/busy, перекрытие и текущая сборка. Действие пользователя только прокручивает к доступной цели и показывает нейтральную обводку на 1,8 секунды: оно не нажимает целевой элемент, не открывает закрытые интерфейсы и не перемещает фокус. Для недоступных и неоднозначных целей остаётся текстовый ответ и доступное status-сообщение.

Проверки полного изолированного прогона подтвердили регрессию по чатам, документам, OCR, цитатам, таблицам, поиску, безопасности загрузок и всем шести responsive-размерам. В отдельном тестовом Compose исправлена гонка готовности PostgreSQL: `pg_isready` заменён на healthcheck с успешным SQL `SELECT 1`, после чего изолированный backup/restore повторно прошёл. Рабочие данные и volumes при этом не затрагивались.

| Проверка | Результат |
|---|---|
| Frontend contract tests (`npm test`) | **28 passed** |
| Frontend production build (`npm run build`) | **PASS**; остаётся предупреждение Vite о бандле >500 kB |
| Backend unit suite (`coverage run ... -m pytest -q -m not integration`) | **385 passed, 10 deselected**; одно существующее предупреждение Alembic `path_separator` |
| Backend Compose integration (`pytest -q -m integration`) | **10 passed, 385 deselected** |
| Chromium E2E | **95 passed**; включая 3 APP-M03 сценария и размеры 1440×900, 1280×720, 1024×768, 768×1024, 430×932, 390×844 |
| Migration acceptance | **PASS**; upgrade/downgrade/re-upgrade прошли, существующие чаты и документный поток остались доступны |
| PostgreSQL queue checks / queue acceptance | **13 passed / 9 passed** |
| Linux/resource/upload/converter acceptance | **4 + 2 + 3 + 1 passed** |
| Local-data maintenance acceptance | **1 passed** |
| Isolated PostgreSQL + document-volume backup/restore | **PASS после исправления healthcheck**; повторно запускалась только эта часть полного скрипта |
| Production Compose после APP-M03 | **PASS**: API, DB и worker healthy; web отвечает HTTP 200; `/api/v1/version` и `/build-info.json` совпали (`app-m03-5ed2b26b3383` / `5ed2b26b3383feba8002a52f6b8fe684a85e4409`), Alembic `0013_ui_target_metadata (head)` |
| Impeccable detector (`frontend/src/App.tsx`) | **0 findings** |

Первый запуск `scripts/test-e2e.ps1` завершил все 95 Chromium E2E, migration, queue, integration и maintenance проверки, но его последняя backup/restore-проверка поймала неверно раннее состояние healthcheck. После замены healthcheck указанная проверка прошла изолированно. Последующая попытка повторить весь 40-минутный E2E была прервана пользователем; поэтому не заявляется, что единый повторный запуск скрипта завершился с кодом 0. После APP-M03-коммита `5ed2b26` production Compose был пересобран через `up --build --wait`; пользовательские тома сохранены, новые образы и миграция проверены по совпадающим build IDs и актуальной версии схемы.
