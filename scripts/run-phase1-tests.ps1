# Run from any directory; never imports the app against the real trip database.
$ErrorActionPreference = 'Stop'
$taskRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$taskNames = @('TRAVEL_ENV_FILE','TRAVEL_GENUI_DATA_DIR','AGENT_MODE','LLM_API_KEY','DEEPSEEK_API_KEY','CANDIDATE_AGENT_MODE','CANDIDATE_TTL_SECONDS','AMAP_KEY','AGENT_REQUESTS_PER_MINUTE','PYTHONUTF8')
$taskPrevious = @{}
foreach ($taskName in $taskNames) { $taskPrevious[$taskName] = [Environment]::GetEnvironmentVariable($taskName,'Process') }
$taskTemporary = Join-Path ([IO.Path]::GetTempPath()) ('travel-genui-phase1-' + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $taskTemporary | Out-Null
$taskExit = 1
Push-Location -LiteralPath $taskRoot
try {
    $env:TRAVEL_ENV_FILE = Join-Path $taskTemporary 'absent.env'
    $env:TRAVEL_GENUI_DATA_DIR = Join-Path $taskTemporary 'data'
    $env:AGENT_MODE = 'demo'
    $env:LLM_API_KEY = ''
    $env:DEEPSEEK_API_KEY = ''
    $env:CANDIDATE_AGENT_MODE = 'mock'
    $env:CANDIDATE_TTL_SECONDS = '600'
    $env:AMAP_KEY = ''
    $env:AGENT_REQUESTS_PER_MINUTE = '120'
    $env:PYTHONUTF8 = '1'
    & (Join-Path $taskRoot '.venv/Scripts/python.exe') -B -m unittest discover -s backend -p 'test_*.py' -v
    $taskExit = $LASTEXITCODE
}
finally {
    Pop-Location
    foreach ($taskName in $taskNames) { [Environment]::SetEnvironmentVariable($taskName,$taskPrevious[$taskName],'Process') }
    $taskResolved = [IO.Path]::GetFullPath($taskTemporary)
    $taskTempRoot = [IO.Path]::GetFullPath([IO.Path]::GetTempPath()).TrimEnd([char[]]'\/') + [IO.Path]::DirectorySeparatorChar
    if ($taskResolved.StartsWith($taskTempRoot,[StringComparison]::OrdinalIgnoreCase) -and [IO.Path]::GetFileName($taskResolved).StartsWith('travel-genui-phase1-')) {
        if (-not ((Get-Item -LiteralPath $taskResolved -Force).Attributes -band [IO.FileAttributes]::ReparsePoint)) {
            Remove-Item -LiteralPath $taskResolved -Recurse -Force
        }
    }
}
exit $taskExit
