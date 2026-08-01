param([switch]$SkipVersionCheck)
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
. "$PSScriptRoot/python-runtime.ps1"
if (-not $SkipVersionCheck) { & "$PSScriptRoot/check-versions.ps1" }

function Stop-ProcessTree([int]$ProcessId) {
    $children = Get-CimInstance Win32_Process -Filter "ParentProcessId=$ProcessId" -ErrorAction SilentlyContinue
    foreach ($child in $children) { Stop-ProcessTree -ProcessId $child.ProcessId }
    Stop-Process -Id $ProcessId -Force -ErrorAction SilentlyContinue
}

$processes = @()
try {
    $processes += Start-Process go -ArgumentList @('run', './cmd/chartd', '-config', 'config/app.yaml') -WorkingDirectory $projectRoot -NoNewWindow -PassThru
    $previousPythonPath = $env:PYTHONPATH
    $env:PYTHONPATH = "$projectRoot/python/src"
    $processes += Start-Process $PythonExecutable -ArgumentList @('-m', 'tvbt', '--config', "$projectRoot/config/app.yaml") -WorkingDirectory $projectRoot -NoNewWindow -PassThru
    $env:PYTHONPATH = $previousPythonPath
    $processes += Start-Process npm.cmd -ArgumentList @('run', 'dev') -WorkingDirectory "$projectRoot/web" -NoNewWindow -PassThru
    Write-Output 'Go, Python, and Vue development services started. Press Ctrl+C to stop their exact process trees.'
    while ($true) {
        foreach ($process in $processes) {
            if ($process.HasExited) { throw "Child process $($process.Id) exited with $($process.ExitCode)" }
        }
        Start-Sleep -Milliseconds 500
    }
} finally {
    foreach ($process in $processes) { Stop-ProcessTree -ProcessId $process.Id }
}
