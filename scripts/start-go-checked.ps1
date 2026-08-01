$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
& "$PSScriptRoot/check-versions.ps1"
New-Item -ItemType Directory -Force "$projectRoot/bin" | Out-Null
go build -o "$projectRoot/bin/chartd.exe" ./cmd/chartd
& "$projectRoot/bin/chartd.exe" -config "$projectRoot/config/app.yaml"

