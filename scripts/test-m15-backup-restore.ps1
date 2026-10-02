$ErrorActionPreference = 'Stop'
$taskRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$composeFile = Join-Path $taskRoot 'compose.m15-restore-test.yml'
$guid = [Guid]::NewGuid().ToString('N')
$project = "document-checker-m15-restore-$guid"
$tempRoot = [System.IO.Path]::GetFullPath($env:TEMP)
$tempPath = Join-Path $tempRoot $project
$started = $false

function Invoke-DockerChecked([string[]]$Arguments) {
    & docker @Arguments
    if ($LASTEXITCODE -ne 0) { throw "Docker command failed ($LASTEXITCODE): docker $($Arguments -join ' ')" }
}

function Invoke-DockerOutput([string[]]$Arguments) {
    $result = & docker @Arguments 2>&1 | Out-String
    if ($LASTEXITCODE -ne 0) { throw "Docker command failed ($LASTEXITCODE): docker $($Arguments -join ' ')`n$result" }
    return $result.Trim()
}

try {
    if (-not (Test-Path -LiteralPath $composeFile -PathType Leaf)) { throw 'M15 isolated restore Compose file is missing.' }
    Invoke-DockerChecked @('info', '--format', '{{.ServerVersion}}')
    if (Test-Path -LiteralPath $tempPath) { throw "The unique temporary test path already exists: $tempPath" }
    New-Item -ItemType Directory -Path $tempPath | Out-Null
    Push-Location $taskRoot
    try {
        $started = $true
        Invoke-DockerChecked @('compose', '-p', $project, '-f', $composeFile, 'up', '-d', '--wait', '--wait-timeout', '120')

        $marker = "M15_BACKUP_RESTORE_$guid"
        Invoke-DockerChecked @('compose', '-p', $project, '-f', $composeFile, 'exec', '-T', 'source-db', 'psql', '-U', 'm15test', '-d', 'm15source', '-v', 'ON_ERROR_STOP=1', '-c', "CREATE TABLE m15_restore_probe (id integer PRIMARY KEY, payload text NOT NULL); INSERT INTO m15_restore_probe VALUES (1, '$marker');")
        Invoke-DockerChecked @('compose', '-p', $project, '-f', $composeFile, 'exec', '-T', 'source-files', 'sh', '-c', "printf '%s' '$marker' > /data/restore-probe.txt")

        Invoke-DockerChecked @('compose', '-p', $project, '-f', $composeFile, 'exec', '-T', 'source-db', 'pg_dump', '-Fc', '-U', 'm15test', '-d', 'm15source', '-f', '/tmp/m15-source.dump')
        Invoke-DockerChecked @('compose', '-p', $project, '-f', $composeFile, 'exec', '-T', 'source-files', 'tar', '-czf', '/tmp/m15-documents.tar.gz', '-C', '/data', '.')
        $dumpPath = Join-Path $tempPath 'database.dump'
        $documentsPath = Join-Path $tempPath 'documents.tar.gz'
        Invoke-DockerChecked @('compose', '-p', $project, '-f', $composeFile, 'cp', 'source-db:/tmp/m15-source.dump', $dumpPath)
        Invoke-DockerChecked @('compose', '-p', $project, '-f', $composeFile, 'cp', 'source-files:/tmp/m15-documents.tar.gz', $documentsPath)
        if ((Get-Item -LiteralPath $dumpPath).Length -le 0 -or (Get-Item -LiteralPath $documentsPath).Length -le 0) {
            throw 'The synthetic backup archive was empty.'
        }

        Invoke-DockerChecked @('compose', '-p', $project, '-f', $composeFile, 'cp', $dumpPath, 'restore-db:/tmp/m15-source.dump')
        Invoke-DockerChecked @('compose', '-p', $project, '-f', $composeFile, 'cp', $documentsPath, 'restore-files:/tmp/m15-documents.tar.gz')
        Invoke-DockerChecked @('compose', '-p', $project, '-f', $composeFile, 'exec', '-T', 'restore-db', 'pg_restore', '--no-owner', '-U', 'm15test', '-d', 'm15restore', '/tmp/m15-source.dump')
        Invoke-DockerChecked @('compose', '-p', $project, '-f', $composeFile, 'exec', '-T', 'restore-files', 'tar', '-xzf', '/tmp/m15-documents.tar.gz', '-C', '/data')
        $databaseValue = Invoke-DockerOutput @('compose', '-p', $project, '-f', $composeFile, 'exec', '-T', 'restore-db', 'psql', '-U', 'm15test', '-d', 'm15restore', '-Atc', 'SELECT payload FROM m15_restore_probe WHERE id = 1')
        $fileValue = Invoke-DockerOutput @('compose', '-p', $project, '-f', $composeFile, 'exec', '-T', 'restore-files', 'cat', '/data/restore-probe.txt')
        if ($databaseValue -ne $marker -or $fileValue -ne $marker) { throw 'The isolated restore did not reproduce both the database row and document-volume file.' }
        Write-Host 'M15 isolated PostgreSQL + document-volume backup/restore: PASS'
    } finally {
        Pop-Location
    }
} finally {
    try {
        if ($started) {
            Push-Location $taskRoot
            try { Invoke-DockerChecked @('compose', '-p', $project, '-f', $composeFile, 'down', '--volumes', '--remove-orphans') }
            finally { Pop-Location }
        }
    } finally {
        $resolvedTempRoot = (Resolve-Path -LiteralPath $tempRoot).Path.TrimEnd('\')
        if ((Test-Path -LiteralPath $tempPath) -and
            [System.IO.Path]::GetFullPath($tempPath).StartsWith($resolvedTempRoot + '\', [System.StringComparison]::OrdinalIgnoreCase) -and
            [System.IO.Path]::GetFileName($tempPath) -match '^document-checker-m15-restore-[0-9a-f]{32}$') {
            Remove-Item -LiteralPath $tempPath -Recurse -Force
        }
    }
}
