param([switch]$KeepRunning, [switch]$M15Only)
$ErrorActionPreference = 'Stop'
$taskRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$taskPython = Join-Path $taskRoot 'backend/.venv/Scripts/python.exe'
$taskComposeFile = Join-Path $taskRoot 'compose.e2e.yml'
$taskPort = 5175
$taskProject = "document-checker-e2e-$([Guid]::NewGuid().ToString('N').Substring(0, 8))"
$taskPreviousBase = $env:AI_CHECKER_BASE_URL
$taskPreviousAudit = $env:E2E_AUDIT_PROJECT
$taskPreviousProject = $env:E2E_COMPOSE_PROJECT
$taskPreviousComposeFile = $env:E2E_COMPOSE_FILE
$taskPreviousPort = $env:E2E_WEB_PORT
$taskPreviousOrigins = $env:E2E_LOCAL_UI_ORIGINS
$taskPreviousBrowserUrl = $env:PLAYWRIGHT_BASE_URL
$taskExit = 1
function Run-Checked([string]$Command, [string[]]$Arguments) {
    & $Command @Arguments
    if ($LASTEXITCODE -ne 0) { throw "Command failed ($LASTEXITCODE): $Command $Arguments" }
}
Push-Location $taskRoot
try {
    # Never start/recover Desktop here. Its host-specific procedure is external.
    Run-Checked docker @('info', '--format', '{{.ServerVersion}}')
    if (Get-NetTCPConnection -LocalPort $taskPort -State Listen -ErrorAction SilentlyContinue) {
        throw "The isolated E2E port $taskPort is already in use; no service was stopped or changed."
    }
    if (-not (Test-Path -LiteralPath $taskPython)) {
        throw 'Create backend/.venv and install requirements.txt plus requirements-test.txt first (see README).'
    }
    $env:E2E_COMPOSE_PROJECT = $taskProject
    $env:E2E_COMPOSE_FILE = $taskComposeFile
    $env:E2E_WEB_PORT = [string]$taskPort
    $env:E2E_LOCAL_UI_ORIGINS = '["http://localhost:5175","http://127.0.0.1:5175"]'
    $env:E2E_AUDIT_PROJECT = $taskProject
    $env:AI_CHECKER_BASE_URL = "http://127.0.0.1:$taskPort"
    $env:PLAYWRIGHT_BASE_URL = "http://127.0.0.1:$taskPort"
    New-Item -ItemType Directory -Force 'e2e-artifacts' | Out-Null
    Run-Checked $taskPython @('backend/tests/e2e_support/generate.py')
    Push-Location frontend
    try {
        Run-Checked npm @('ci')
        Run-Checked npx @('playwright', 'install', 'chromium')
        Run-Checked npm @('test')
        Run-Checked npm @('run', 'build')
    } finally { Pop-Location }
    Push-Location backend
    try {
        Run-Checked $taskPython @('-m', 'coverage', 'run', '--source=app', '-m', 'pytest', '-q', '-m', 'not integration')
        Run-Checked $taskPython @('-m', 'coverage', 'report', '-m')
        Run-Checked $taskPython @('-m', 'coverage', 'json', '-o', '../e2e-artifacts/backend-coverage.json')
    }
    finally { Pop-Location }
    # A fresh GUID project isolates these disposable volumes from any running local library.
    Run-Checked docker @('compose', '-p', $taskProject, '-f', $taskComposeFile, 'up', '--build', '-d', '--wait', '--wait-timeout', '300')
    Run-Checked docker @('compose', '-p', $taskProject, '-f', $taskComposeFile, 'exec', '-T', '-e', "E2E_AUDIT_PROJECT=$taskProject", 'api', 'python', '/test_support/security_audit.py')
    if ($M15Only) {
        Push-Location frontend
        try { Run-Checked npm @('run', 'test:e2e', '--', '--grep', 'local data dialog previews deletion') }
        finally { Pop-Location }
    } else {
        Push-Location frontend
        try { Run-Checked npm @('run', 'test:e2e') }
        finally { Pop-Location }
        Run-Checked $taskPython @('backend/tests/e2e_support/queue_acceptance.py')
        Invoke-RestMethod -Method Post -Uri "http://127.0.0.1:$taskPort/api/v1/__e2e/provider" -ContentType 'application/json' -Body '{"mode":"disconnected"}' | Out-Null
        Push-Location backend
        try { Run-Checked $taskPython @('-m', 'pytest', '-q', '-m', 'integration') }
        finally { Pop-Location }
    }
    Run-Checked $taskPython @('backend/tests/e2e_support/maintenance_acceptance.py')
    Run-Checked powershell.exe @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', (Join-Path $PSScriptRoot 'test-m15-backup-restore.ps1'))
    $taskExit = 0
} catch {
    Write-Error $_ -ErrorAction Continue
} finally {
    & docker compose -p $taskProject -f $taskComposeFile logs --no-color 2>&1 | Out-File -Encoding utf8 'e2e-artifacts/compose.log'
    if (-not $KeepRunning) { & docker compose -p $taskProject -f $taskComposeFile down -v --remove-orphans }
    $env:AI_CHECKER_BASE_URL = $taskPreviousBase
    $env:E2E_AUDIT_PROJECT = $taskPreviousAudit
    $env:E2E_COMPOSE_PROJECT = $taskPreviousProject
    $env:E2E_COMPOSE_FILE = $taskPreviousComposeFile
    $env:E2E_WEB_PORT = $taskPreviousPort
    $env:E2E_LOCAL_UI_ORIGINS = $taskPreviousOrigins
    $env:PLAYWRIGHT_BASE_URL = $taskPreviousBrowserUrl
    Pop-Location
}
if ($KeepRunning -and $taskExit -eq 0) { Write-Host "Kept isolated E2E project $taskProject at http://127.0.0.1:$taskPort" }
exit $taskExit
