param([int]$WaitTimeoutSeconds = 300)
$ErrorActionPreference = 'Stop'
$taskRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$taskPreviousBase = $env:AI_CHECKER_BASE_URL
$taskPreviousBuildId = $env:APP_BUILD_ID
$taskPreviousCommit = $env:APP_BUILD_COMMIT
$taskPreviousTime = $env:APP_BUILD_TIME
Push-Location $taskRoot
try {
    & docker info --format '{{.ServerVersion}}' | Out-Null
    if ($LASTEXITCODE -ne 0) { throw 'Docker daemon is not ready. Start Docker Desktop and wait for docker info to succeed before retrying.' }
    $dirty = @(& git status --porcelain)
    if ($LASTEXITCODE -ne 0) { throw 'Could not inspect Git working-tree status.' }
    if ($dirty.Count -gt 0) { throw 'Commit or save the current changes before building a version-stamped Compose deployment.' }

    $commit = (& git rev-parse HEAD).Trim()
    if ($LASTEXITCODE -ne 0 -or -not $commit) { throw 'Could not determine the Git commit for build metadata.' }
    $builtAt = [DateTime]::UtcNow.ToString('yyyy-MM-ddTHH:mm:ssZ')
    $env:APP_BUILD_ID = "$($commit.Substring(0, 12))-$([DateTime]::UtcNow.ToString('yyyyMMddHHmmss'))"
    $env:APP_BUILD_COMMIT = $commit
    $env:APP_BUILD_TIME = $builtAt

    & docker compose up --build -d --wait --wait-timeout $WaitTimeoutSeconds
    if ($LASTEXITCODE -ne 0) { throw 'Compose build or health checks failed. Check docker compose logs for api, worker and web.' }

    $webUrl = 'http://localhost:5173'
    $env:AI_CHECKER_BASE_URL = $webUrl
    $manifest = Invoke-RestMethod -Uri "$webUrl/build-info.json" -Headers @{ 'Cache-Control' = 'no-cache' }
    $api = Invoke-RestMethod -Uri "$webUrl/api/v1/version" -Headers @{ 'Cache-Control' = 'no-cache' }
    foreach ($field in @('build_id', 'commit', 'built_at')) {
        # APP_BUILD_TIME maps to built_at; APP_BUILD_COMMIT maps to commit.
        $expected = switch ($field) { 'build_id' { $env:APP_BUILD_ID } 'commit' { $env:APP_BUILD_COMMIT } 'built_at' { $env:APP_BUILD_TIME } }
        if ($manifest.$field -ne $expected) { throw "Frontend build metadata mismatch for $field." }
        if ($manifest.$field -ne $api.$field) { throw "Frontend and API builds do not match for $field." }
    }
    if (-not ($manifest.assets | Where-Object { $_ -match 'pdf\.worker.*\.mjs$' })) { throw 'Build manifest does not list the PDF.js module worker.' }

    Push-Location (Join-Path $taskRoot 'frontend')
    try {
        & npm run smoke:deployed
        if ($LASTEXITCODE -ne 0) { throw 'Browser deployment smoke check failed.' }
    } finally { Pop-Location }
    Write-Host "Compose deployment is healthy and verified as $($manifest.build_id). Existing named volumes were preserved."
} finally {
    $env:AI_CHECKER_BASE_URL = $taskPreviousBase
    $env:APP_BUILD_ID = $taskPreviousBuildId
    $env:APP_BUILD_COMMIT = $taskPreviousCommit
    $env:APP_BUILD_TIME = $taskPreviousTime
    Pop-Location
}
