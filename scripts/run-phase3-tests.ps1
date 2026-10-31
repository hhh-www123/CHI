# Run the full backend suite in an isolated temporary SQLite store, without real keys.
$ErrorActionPreference = 'Stop'
& (Join-Path $PSScriptRoot 'run-phase1-tests.ps1')
exit $LASTEXITCODE
