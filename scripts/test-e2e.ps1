param([switch]$KeepRunning)
$ErrorActionPreference = 'Stop'
$taskRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$taskPython = Join-Path $taskRoot 'backend/.venv/Scripts/python.exe'
$taskPreviousBase = $env:AI_CHECKER_BASE_URL
$taskPreviousAudit = $env:E2E_AUDIT_PROJECT
$taskExit = 1
function Run-Checked([string]$Command, [string[]]$Arguments) {
    & $Command @Arguments
    if ($LASTEXITCODE -ne 0) { throw "Command failed ($LASTEXITCODE): $Command $Arguments" }
}
Push-Location $taskRoot
try {
    # Never start/recover Desktop here. Its host-specific procedure is external.
    Run-Checked docker @('info', '--format', '{{.ServerVersion}}')
    if (-not (Test-Path -LiteralPath $taskPython)) {
        throw 'Create backend/.venv and install requirements.txt plus requirements-test.txt first (see README).'
    }
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
    # These volumes belong only to the explicitly named test project.
    Run-Checked docker @('compose', '-p', 'document-checker-e2e', '-f', 'compose.e2e.yml', 'down', '-v', '--remove-orphans')
    Run-Checked docker @('compose', '-p', 'document-checker-e2e', '-f', 'compose.e2e.yml', 'up', '--build', '-d', '--wait', '--wait-timeout', '300')
    Run-Checked docker @('compose', '-p', 'document-checker-e2e', '-f', 'compose.e2e.yml', 'exec', '-T', '-e', 'E2E_AUDIT_PROJECT=document-checker-e2e', 'api', 'python', '/test_support/security_audit.py')
    Push-Location frontend
    try { Run-Checked npm @('run', 'test:e2e') }
    finally { Pop-Location }
    $env:E2E_AUDIT_PROJECT = 'document-checker-e2e'
    Run-Checked $taskPython @('backend/tests/e2e_support/queue_acceptance.py')
    Invoke-RestMethod -Method Post -Uri 'http://localhost:5174/api/v1/__e2e/provider' -ContentType 'application/json' -Body '{"mode":"disconnected"}' | Out-Null
    $env:AI_CHECKER_BASE_URL = 'http://localhost:5174'
    Push-Location backend
    try { Run-Checked $taskPython @('-m', 'pytest', '-q', '-m', 'integration') }
    finally { Pop-Location }
    $taskExit = 0
} catch {
    Write-Error $_ -ErrorAction Continue
} finally {
    & docker compose -p document-checker-e2e -f compose.e2e.yml logs --no-color 2>&1 | Out-File -Encoding utf8 'e2e-artifacts/compose.log'
    if (-not $KeepRunning) { & docker compose -p document-checker-e2e -f compose.e2e.yml down -v --remove-orphans }
    $env:AI_CHECKER_BASE_URL = $taskPreviousBase
    $env:E2E_AUDIT_PROJECT = $taskPreviousAudit
    Pop-Location
}
exit $taskExit
