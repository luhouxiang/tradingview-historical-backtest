$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$requiredPorts = @{
    5173 = 'Vue/Vite'
    8080 = 'Go API'
    8091 = 'Python engine'
}

$listeners = @(Get-NetTCPConnection -State Listen -ErrorAction SilentlyContinue |
    Where-Object { $requiredPorts.ContainsKey([int]$_.LocalPort) } |
    Sort-Object LocalPort)
if ($listeners.Count -gt 0) {
    $details = foreach ($listener in $listeners) {
        $process = Get-CimInstance Win32_Process -Filter "ProcessId=$($listener.OwningProcess)" -ErrorAction SilentlyContinue
        $name = if ($process) { $process.Name } else { 'unknown' }
        $service = $requiredPorts[[int]$listener.LocalPort]
        "  $($listener.LocalPort) ($service): PID $($listener.OwningProcess), $name"
    }
    throw "TVBT cannot start because required ports are already in use.`n$($details -join "`n")`nStop the previous TVBT/debug session, then run the compound again."
}

& "$PSScriptRoot/check-versions.ps1"
Write-Output 'TVBT debug preflight passed: versions match and ports 5173/8080/8091 are free.'
