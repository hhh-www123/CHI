#requires -Version 5.1
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
try {
    $projectDirectory = (Resolve-Path -LiteralPath $PSScriptRoot).Path
    $response = Invoke-WebRequest -UseBasicParsing -Uri 'http://127.0.0.1:8011/api/health' -TimeoutSec 3
    $health = [System.Text.Encoding]::UTF8.GetString($response.RawContentStream.ToArray()) | ConvertFrom-Json
    $fields = $health.PSObject.Properties.Name
    if (-not ($fields -contains 'app') -or -not ($fields -contains 'projectRoot') -or $health.app -ne 'travel-genui' -or -not [string]::Equals([string]$health.projectRoot, $projectDirectory, [StringComparison]::OrdinalIgnoreCase)) {
        throw '无法确认8011端口属于当前项目，未停止任何程序。'
    }
    if ($fields -contains 'processId') { $serverProcessId = [int]$health.processId }
    else {
        $pidPath = Join-Path $projectDirectory '.runtime\server.pid'
        if (-not (Test-Path -LiteralPath $pidPath)) { throw '没有本项目启动记录，请在启动服务的终端按Ctrl+C停止。' }
        $serverProcessId = [int](Get-Content -LiteralPath $pidPath -Raw).Trim()
    }
    $serverProcess = Get-CimInstance Win32_Process -Filter "ProcessId = $serverProcessId"
    if ($null -eq $serverProcess -or [string]::IsNullOrWhiteSpace($serverProcess.CommandLine) -or $serverProcess.CommandLine -notmatch 'uvicorn' -or $serverProcess.CommandLine -notmatch 'backend\.app:app') {
        throw '运行记录与预期的网页进程不符，未停止任何程序。'
    }
    Stop-Process -Id $serverProcessId -ErrorAction Stop
    Write-Host '已停止当前项目的网页服务，行程数据保留。' -ForegroundColor Green
    exit 0
}
catch {
    Write-Host $_.Exception.Message -ForegroundColor Red
    exit 1
}
