$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
. "$PSScriptRoot/python-runtime.ps1"
$ports = @(8080, 8091, 5173)
$listeners = Get-NetTCPConnection -State Listen -ErrorAction SilentlyContinue |
    Where-Object { $_.LocalPort -in $ports }
if ($listeners) {
    throw "Required ports already in use: $($listeners.LocalPort -join ',')"
}

$nodeDirectory = Split-Path -Parent (Get-Command node).Source
if ((& "$nodeDirectory/node.exe" --version) -ne 'v22.23.2') {
    $wingetPackages = Join-Path $env:LOCALAPPDATA 'Microsoft/WinGet/Packages'
    $candidate = Get-ChildItem $wingetPackages -Recurse -Filter node.exe -ErrorAction SilentlyContinue |
        Where-Object { $_.FullName -like '*OpenJS.NodeJS.22*node-v22.23.2-*' } |
        Select-Object -First 1
    if (-not $candidate) { throw 'Node.js 22.23.2 is required' }
    $nodeDirectory = $candidate.DirectoryName
}
$env:PATH = "$nodeDirectory;$env:PATH"
$env:PYTHONPATH = "$projectRoot/python/src"

New-Item -ItemType Directory -Force "$projectRoot/bin" | Out-Null
go build -o "$projectRoot/bin/chartd.exe" ./cmd/chartd

function Stop-ExactProcessTree([int]$TargetProcessId) {
    $children = Get-CimInstance Win32_Process -Filter "ParentProcessId=$TargetProcessId" -ErrorAction SilentlyContinue
    foreach ($child in $children) {
        Stop-ExactProcessTree -TargetProcessId $child.ProcessId
    }
    Stop-Process -Id $TargetProcessId -Force -ErrorAction SilentlyContinue
}

function Wait-Json([string]$Uri, [datetime]$Deadline) {
    do {
        try {
            return Invoke-RestMethod -Uri $Uri -TimeoutSec 1
        } catch {
            Start-Sleep -Milliseconds 300
        }
    } while ((Get-Date) -lt $Deadline)
    throw "Timed out waiting for $Uri"
}

$processes = @()
try {
    $processes += Start-Process $PythonExecutable -ArgumentList @(
        '-m', 'tvbt', '--config', "$projectRoot/config/app.yaml"
    ) -WorkingDirectory $projectRoot -WindowStyle Hidden -PassThru
    $processes += Start-Process "$projectRoot/bin/chartd.exe" -ArgumentList @(
        '-config', "$projectRoot/config/app.yaml"
    ) -WorkingDirectory $projectRoot -WindowStyle Hidden -PassThru
    $processes += Start-Process "$nodeDirectory/node.exe" -ArgumentList @(
        "$projectRoot/web/node_modules/vite/bin/vite.js", '--host', '127.0.0.1'
    ) -WorkingDirectory "$projectRoot/web" -WindowStyle Hidden -PassThru

    $deadline = (Get-Date).AddSeconds(30)
    $python = Wait-Json -Uri 'http://127.0.0.1:8091/internal/v1/health' -Deadline $deadline
    $health = Wait-Json -Uri 'http://127.0.0.1:8080/api/v1/health' -Deadline $deadline
    $web = Invoke-WebRequest -Uri 'http://127.0.0.1:5173/' -TimeoutSec 5 -UseBasicParsing
    $event = @{
        events = @(@{
            timestamp = (Get-Date).ToUniversalTime().ToString('o')
            level = 'INFO'
            service = 'spoofed'
            event = 'app.started'
            message = 'runtime smoke'
            source_file = 'src/main.ts'
            source_line = 12
            source_function = 'bootstrap'
        })
    } | ConvertTo-Json -Depth 5
    $ingest = Invoke-RestMethod -Uri 'http://127.0.0.1:8080/api/v1/client-logs' `
        -Method Post -ContentType 'application/json' -Body $event

    [pscustomobject]@{
        python_status = $python.status
        go_status = $health.status
        python_via_go = $health.services.'python-engine'.status
        python_version = $health.services.'python-engine'.version
        vue_http = $web.StatusCode
        client_logs_accepted = $ingest.accepted
    } | ConvertTo-Json -Compress
} finally {
    foreach ($process in $processes) {
        Stop-ExactProcessTree -TargetProcessId $process.Id
    }
}
