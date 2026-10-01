# M03 — устойчивая обработка документов

M03 переносит обработку документов из процесса API в отдельный Compose worker. PostgreSQL хранит задания, lease, версии результата и контрольные точки; Redis для этого не используется.

## Жизненный цикл

Загрузка создаёт в одной транзакции `Document`, `Chat`, первую `DocumentVersion` и `ProcessingJob`. Для повторного запуска создаётся новая монотонная версия. Активная версия не меняется до успешной подготовки индекса и анализа.

Задание проходит этапы `queued`, `extracting`, `ocr`, `indexing`, `indexing_checkpoint`, `waiting_analysis`, `analysis_request` и `complete`. Его состояния: `queued`, `running`, `cancelling`, `cancelled`, `succeeded`, `failed`. В `progress` сохраняются счётчики индексации, а `heartbeat` и `lease_until` защищают задание от двух владельцев.

Worker атомарно захватывает одно задание через `FOR UPDATE SKIP LOCKED`; короткая транзакция admission использует PostgreSQL advisory lock для общего лимита `QUEUE_CONCURRENCY` (по умолчанию 2). Отдельный advisory lock ограничивает активные внешние запросы анализа `ANALYSIS_CONCURRENCY` (по умолчанию 1). Каждая запись результата повторно проверяет владельца, состояние и lease, поэтому просроченный worker не может активировать результат.

## Версии и восстановление

`DocumentVersion` хранит снимок полей результата и `chunk_version`. Chunks не удаляются при переобработке: активные API-запросы фильтруют текущую версию, а старые идентификаторы источников в истории чата продолжают разрешаться через общую таблицу. Insights также имеют версию и публикуются одной транзакцией вместе с новой активной версией.

Производные файлы получают имя с UUID документа, номером версии и UUID владельца попытки. Временная embedding-партия удаляется сразу после индексации. При отмене или восстановлении удаляются только файлы и chunks staging-версии. Оригинал и активные артефакты не затрагиваются.

После перезапуска worker восстанавливает истёкшие lease. Локальные этапы возвращаются в очередь и повторяются с новым владельцем. Если lease истёк на `analysis_request`, задание становится `failed` с кодом `analysis_interrupted`: внешний запрос нельзя безопасно повторить автоматически, пользователь должен нажать «Повторить анализ». После исчерпания `max_attempts` используется `attempts_exhausted`.

Удаление документа сначала переводит активное задание в `cancelling`, ждёт ограниченное время завершения дочерних процессов, затем удаляет документ каскадно. Все последующие fenced-записи видят отсутствие документа или потерю владельца. Файлы версий очищаются только после успешного удаления записи.

## Операции API

```http
GET  /api/v1/documents/{id}/jobs
POST /api/v1/documents/{id}/cancel
POST /api/v1/documents/{id}/retry?operation=analysis
POST /api/v1/documents/{id}/retry?operation=process
POST /api/v1/documents/{id}/markdown/rebuild
```

`analysis` повторно использует последнюю индексированную версию и не создаёт новые chunks. `process` заново выполняет native-парсер/MarkItDown, OCR и embeddings. Обычный `retry` выбирает `analysis`, если индекс уже пригоден, и `process` в противном случае.

Параметры модели, reasoning, OCR и версии конвертера копируются в `ProcessingJob.parameters` в момент постановки задания. Изменение глобальных настроек не меняет уже ожидающее задание.

## Compose

Сервис `worker` использует тот же образ и volumes, что и API, но отдельную команду `python -m app.worker`. Его healthcheck проверяет свежий heartbeat-файл в `/tmp`. Миграции выполняет API перед тем, как worker получает статус готовности. В E2E Compose используется отдельный `e2e_worker.py`, который заменяет только Codex мостом к синтетическому test-only endpoint; реальные парсеры, OCR, embeddings и PostgreSQL остаются настоящими.

Настройки:

- `QUEUE_CONCURRENCY` — число тяжёлых заданий одновременно;
- `ANALYSIS_CONCURRENCY` — число одновременных внешних анализов;
- `QUEUE_LEASE_SECONDS` — длительность lease;
- `QUEUE_HEARTBEAT_SECONDS` — интервал heartbeat;
- `QUEUE_POLL_SECONDS` — интервал опроса очереди.

## Проверка M03

```powershell
backend/.venv/Scripts/python.exe -m pytest -q -m "not integration"
$env:E2E_AUDIT_PROJECT = "document-checker-e2e"
backend/.venv/Scripts/python.exe backend/tests/e2e_support/queue_acceptance.py
```

Acceptance-набор проверяет конкурентную постановку, lease/fencing, восстановление после SIGKILL на OCR и индексации, сохранность старого чата и citations, явный retry после прерванного анализа, отмену дочернего OCR-процесса, очистку staging-файлов, удаление документа и повторную обработку без дубликатов.
