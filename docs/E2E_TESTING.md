# Воспроизводимая приёмка M01

## Требования и запуск

Запущенный Docker Engine, Compose v2 с поддержкой `!override`, Node.js 22, Python 3.12 и PowerShell. На Windows запуск/восстановление Docker Desktop выполняется по процедуре хоста; скрипт тестов не перезапускает Desktop.

Из корня проекта:

```powershell
py -3.12 -m venv backend/.venv
backend/.venv/Scripts/python.exe -m pip install -r backend/requirements.txt -r backend/requirements-test.txt
pwsh -File scripts/test-e2e.ps1
```

Чтобы прогнать весь backend unit-набор и только M15 UI/API/backup acceptance, не запуская посторонние браузерные и queue-сценарии:

```powershell
pwsh -File scripts/test-e2e.ps1 -M15Only
```

Обычный запуск генерирует 48 синтетических файлов и выполняет полную приёмку: backend unit с coverage, frontend contracts/build, production Compose под новым случайным именем `document-checker-e2e-<8 hex>` на **5175**, Linux-аудит защит, Chromium E2E, queue acceptance, общие API-интеграционные тесты, приёмку M15 и изолированную проверку восстановления backup. Режим `-M15Only` выполняет backend unit, frontend contracts/build, Compose/Linux security acceptance, M15 Chromium и API acceptance и изолированное восстановление backup; общие Chromium, queue и API integration сценарии в нём пропускаются. На каждый запуск создаются отдельные PostgreSQL, originals, Markdown, preferences и embeddings volumes; после прогона удаляются только volumes проекта с этим новым случайным именем. Скрипт отказывается запускаться, если 5175 уже занят. Рабочий проект и прежнее тестовое окружение на 5173/5174 не затрагиваются.

Для targeted browser rerun скрипт принимает `-PlaywrightGrep "часть названия теста"`. Это запускает только совпавшие Playwright-сценарии, но сохраняет последующие независимые queue/API/M15/backup acceptance; migration acceptance пропускается, поскольку сокращённый браузерный набор не гарантирует существование сохранённого чата с citation. Такой запуск не заменяет полную приёмку.

`-KeepRunning` оставляет окружение для диагностики; следующий запуск всё равно начинает с чистой тестовой базы. Первый запуск скачивает Chromium и локальную multilingual embedding model. OCR работает настоящими Poppler/Tesseract rus+eng. Текст документов не передаётся в облако.

Тестовый Codex существует только в исключённом из production-образа каталоге `backend/tests/e2e_support`; штатный entrypoint его не импортирует. Подменён только облачный провайдер. Парсеры, MarkItDown, embeddings, OCR, БД, сохранение сообщений и renderer настоящие. Fault-сценарии отдельно подменяют один вызов конвертации или браузерный fetch. Synthetic answers проверяют механику, а не интеллект Codex.

`LOCAL_UI_ORIGINS` задаёт точный JSON-список разрешённых origins; production default остаётся localhost/127.0.0.1:5173, изолированный E2E разрешает только 5175.

Unit и integration запускаются раздельно (`-m "not integration"` / `-m integration`), поэтому полная приёмка не скрывает отсутствие Compose через skip. Интеграционные тесты выполняются с disconnected provider, браузерные — с ready provider. В E2E нет ретраев и `test.skip`, состояние ожидается по API/DOM; stream gate открывается после проверки промежуточного ответа.

## Артефакты и отдельные сценарии

- `frontend/playwright-report`: HTML-report.
- `frontend/test-results`: JUnit, шесть screenshots responsive-состояний, browser-log attachments; screenshot/video/trace каждого сбоя.
- `e2e-artifacts/compose.log`: логи только test stack.
- `e2e-artifacts/backend-coverage.json`: backend unit coverage.

Файлы генерируются локально, не коммитятся. После оставленного test stack:

```powershell
cd frontend
$env:PLAYWRIGHT_BASE_URL = 'http://127.0.0.1:5175'
$env:E2E_COMPOSE_PROJECT = 'document-checker-e2e-<8-hex-этого-прогона>'
$env:E2E_COMPOSE_FILE = (Join-Path (Resolve-Path ..) 'compose.e2e.yml')
npm run test:e2e -- -g "pdf: upload"
npx playwright show-report
# При сбое подставьте реальный путь из сообщения теста:
npx playwright show-trace test-results/<failed-test>/trace.zip
Remove-Item Env:PLAYWRIGHT_BASE_URL, Env:E2E_COMPOSE_PROJECT, Env:E2E_COMPOSE_FILE
```

В `compose.e2e.yml` фиксированы services, а имя проекта и loopback port задаёт `scripts/test-e2e.ps1`. Не перенаправляйте тесты на рабочую библиотеку. Скрипт сам генерирует уникальный project name и удаляет только созданные им тестовые volumes. Не запускайте cleanup ниже для существующего проекта: используйте только имя случайного проекта из конкретного текущего прогона, если тот завершился с `-KeepRunning`.

```powershell
docker compose -p document-checker-e2e-<8-hex-этого-прогона> -f compose.e2e.yml down -v --remove-orphans
```

## Явно включаемый live Codex

Проверка исключена из обычного E2E. В Codex передаются только сгенерированный `sample.txt` (вымышленный проект, Алексей/Мария Пример, дата 2026-11-30) и вопрос «Кто автор документа?». Не подключайте рабочую библиотеку или рабочий auth volume.

```powershell
docker compose -p document-checker-e2e-live -f compose.e2e.yml -f compose.e2e-live.yml up --build -d --wait
# Откройте http://localhost:5175 и подключите Codex через UI отдельного проекта.
$env:E2E_LIVE_CODEX_ACK = 'send-synthetic-document-to-cloud'
cd frontend
npm run test:e2e:live
cd ..
Remove-Item Env:E2E_LIVE_CODEX_ACK
docker compose -p document-checker-e2e-live -f compose.e2e.yml -f compose.e2e-live.yml down -v
```

Без ACK конфигурация отказывает в запуске. Без входа/подходящей модели тест падает, а не пропускается. Он проверяет отсутствие test-provider endpoint, семь ответов, имя автора и citations; синтетический документ удаляется в `finally`. Обычная локальная приёмка не требует live Codex и не заявляет, что он был проверен.
