param([ValidateRange(0, 120)][int]$HoldSeconds = 0)

$ErrorActionPreference = 'Stop'
& "$PSScriptRoot/smoke-milestone1.ps1" -VerifyBars -VerifyIndicators -VerifyWorkspace -VerifyChan -VerifyReplay -VerifyBacktest -HoldSeconds $HoldSeconds
