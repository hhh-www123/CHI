$ErrorActionPreference = 'Stop'
& (Join-Path $PSScriptRoot 'run-phase1-tests.ps1')
exit $LASTEXITCODE
