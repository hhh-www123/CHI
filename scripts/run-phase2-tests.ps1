# Reuse the isolated full backend suite, including Phase 1 and Phase 2.
$ErrorActionPreference = 'Stop'
& (Join-Path $PSScriptRoot 'run-phase1-tests.ps1')
exit $LASTEXITCODE
