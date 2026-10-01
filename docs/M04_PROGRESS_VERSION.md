# M04: processing progress and build verification

The workspace polls the persisted processing job while a document is selected. It restores the latest active job after reload, shows the real stage and queue position, displays page or indexed-fragment counts only when the worker reports them, and offers cancellation or retry when those actions are valid. Remaining time is a rough range and is hidden until the current stage has at least three measured units and 15 seconds of elapsed time.

OCR page progress travels from the isolated document worker through a bounded stderr control channel. Diagnostic output is drained but never returned to the browser. The original PDF processing remains isolated from API credentials and user configuration.

`APP_BUILD_ID`, `APP_BUILD_COMMIT`, and `APP_BUILD_TIME` are injected into both images. The frontend exposes `/build-info.json`, the API exposes `/api/v1/version`, and the browser compares both endpoints with the metadata compiled into its active JavaScript bundle. On mismatch, the workspace offers a refresh action and avoids interrupting an active upload or chat response.

Build and deploy the current committed version with:

```powershell
./scripts/deploy-compose.ps1
```

The script refuses to stamp uncommitted code, builds and waits for healthy Compose services, checks metadata and HTTP cache headers for HTML, hashed assets and the PDF.js worker, then runs a browser smoke check at desktop and mobile widths. It does not remove or recreate named data volumes.

The build timestamp comparison normalizes PowerShell's parsed JSON `DateTime` values to UTC instants before matching the manifest, API response, and build environment. The production deployment smoke passed for build `5a1edd8b942d-20261001140028`; the database, API, and worker were healthy, the frontend was reachable, and named volumes were preserved.
