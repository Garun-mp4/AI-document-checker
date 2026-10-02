# M09/M10 API contract: bookmarks, supplementary analysis and export

All routes below are under `/api/v1`. UUIDs and source locators are issued by the backend; a model response cannot choose a document URL or locator.

## Bookmarks

- `GET /documents/{document_id}/bookmarks` returns saved source bookmarks in most-recent-first order.
- `POST /documents/{document_id}/bookmarks` accepts `{ "source_id": "<uuid>", "source_version": 1, "note": "optional, at most 500 characters" }` and returns `201` with the resolved source and locator.
- `PATCH /documents/{document_id}/bookmarks/{bookmark_id}` accepts `{ "note": "updated note or null" }`.
- `DELETE /documents/{document_id}/bookmarks/{bookmark_id}` returns `204`.

Writes validate that the source belongs to the document and declared source version. Re-saving the same source returns `409`; a missing document or bookmark returns `404`. Deleting a source cascades its bookmark. Source ID and original version stay fixed through later analysis rebuilds.

## Supplementary analysis

- `GET /documents/{document_id}/analysis/additional?version=1` returns saved results for that analysis version. Omit `version` to select the active version.
- `POST /documents/{document_id}/analysis/additional` accepts:

```json
{
  "mode": "tasks",
  "model": "gpt-6-luna",
  "reasoning_effort": "medium",
  "analysis_version": 1,
  "expected_source_version": 1
}
```

Supported modes are `brief`, `detailed`, `tasks`, and `risks`. The server checks model availability and the exact ready analysis/source version, runs bounded retrieval only after an explicit request, filters model citation labels against the supplied source set, then saves the answer with model, reasoning, analysis version and source IDs. A stale version returns `409`; unavailable Codex credentials/model return a distinct `409`/`422`/`503`. A no-evidence result contains no fabricated citation. Repeated requests create separate results and do not replace the seven standard insights or earlier supplementary results.

The context limits are configured by `ADDITIONAL_ANALYSIS_MAX_SOURCES` (1–20; default 8), `ADDITIONAL_ANALYSIS_MAX_SOURCE_CHARS` (100–5000; default 1200) and the shared `MODEL_CONTEXT_CHARS` limit. The full original file is not sent to Codex.

## Export

`POST /documents/{document_id}/export` accepts `scope` (`analysis`, `selected_answers`, or `conversation`), `format` (`markdown` or `pdf`), `selected_keys` and `selected_additional_analysis_ids`. Additional-analysis IDs are valid for analysis scopes only and are resolved against the requested document and active analysis version. Selected answer keys and additional-analysis IDs must be unique; conversation exports reject additional-analysis IDs. A version mismatch or foreign result is rejected instead of exporting a citation against another version.

Export reads stored snapshots and does not call Codex. It returns a sanitized filename and matching Markdown/PDF MIME type. Source notes contain stable locator labels and excerpts, not temporary application URLs. User-supplied HTML is escaped/sanitized and never executed.

## Migration and compatibility

Migration `0011_bookmarks_analysis` is additive. Existing document, chunk, chat, message and source rows are not rewritten. Existing documents have no bookmark or supplementary rows until a user creates them; legacy locators continue through the existing versioned-locator compatibility path. The new tables are included in local-data record counts and are deleted only with their owning source, document or analysis version.
