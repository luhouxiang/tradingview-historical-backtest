$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
. "$PSScriptRoot/python-runtime.ps1"

$goVersion = (go version)
if ($goVersion -notmatch '^go version go1\.25\.7\b') {
    throw "Go 1.25.7 is required; found: $goVersion"
}

$pythonVersion = & $PythonExecutable -c "import sys; print('.'.join(map(str, sys.version_info[:3])))" 2>$null
if ($LASTEXITCODE -ne 0 -or $pythonVersion -notmatch '^3\.14\.') {
    throw "Python 3.14 is required; configured executable: $PythonExecutable"
}

$nodeVersion = (node --version).TrimStart('v')
if ($nodeVersion -ne '22.23.2') {
    $wingetPackages = Join-Path $env:LOCALAPPDATA 'Microsoft/WinGet/Packages'
    $candidate = Get-ChildItem $wingetPackages -Recurse -Filter node.exe -ErrorAction SilentlyContinue |
        Where-Object { $_.FullName -like '*OpenJS.NodeJS.22*node-v22.23.2-*' } |
        Select-Object -First 1
    if ($candidate) {
        $env:PATH = "$($candidate.DirectoryName);$env:PATH"
        $nodeVersion = (node --version).TrimStart('v')
    }
}
if ($nodeVersion -ne '22.23.2') {
    throw "Node.js 22.23.2 is required; found: $nodeVersion"
}

Write-Output "Versions OK: Go 1.25.7, Python $pythonVersion ($PythonExecutable), Node $nodeVersion"
