@echo off
setlocal
chcp 65001 >nul
set "TRAVEL_LAUNCH_FILE=%~f0"
powershell.exe -NoProfile -ExecutionPolicy Bypass -Command "$raw=[System.IO.File]::ReadAllText($env:TRAVEL_LAUNCH_FILE,[System.Text.Encoding]::UTF8);$parts=$raw -split '(?m)^# POWERSHELL-BEGIN\r?$',2;if($parts.Count -ne 2){throw 'Launcher payload missing'};& ([scriptblock]::Create($parts[1]))"
set "launchExitCode=%errorlevel%"
if not "%launchExitCode%"=="0" if not "%TRAVEL_LAUNCH_NO_BROWSER%"=="1" pause
exit /b %launchExitCode%
# POWERSHELL-BEGIN
#requires -Version 5.1
<#
旅序 0.6.5-phase4：检查当前源码、构建网页、重启本项目的旧服务，再打开当前版本。
仅使用已安装的本地依赖。行程数据库不会因更新而重置。
#>
param([switch]$NoBrowser)
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$projectDirectory = (Resolve-Path -LiteralPath ([System.IO.Path]::GetDirectoryName($env:TRAVEL_LAUNCH_FILE))).Path
if ($env:TRAVEL_LAUNCH_NO_BROWSER -eq '1') { $NoBrowser = $true }
$pythonExecutable = Join-Path $projectDirectory '.venv\Scripts\python.exe'
$builtDirectory = Join-Path $projectDirectory 'dist'
$runtimeDirectory = Join-Path $projectDirectory '.runtime'
$webAddress = 'http://127.0.0.1:8011'
$healthAddress = "$webAddress/api/health"
$launchMutex = $null
$ownsMutex = $false

function Normalize-ProjectPath([string]$Value) {
    if ([string]::IsNullOrWhiteSpace($Value)) { return '' }
    try { return [System.IO.Path]::GetFullPath($Value).TrimEnd([char[]]'\/') }
    catch { return '' }
}

function Assert-WithinProject([string]$Value) {
    $normalized = Normalize-ProjectPath $Value
    $prefix = (Normalize-ProjectPath $projectDirectory) + [System.IO.Path]::DirectorySeparatorChar
    if (-not $normalized.StartsWith($prefix, [StringComparison]::OrdinalIgnoreCase)) {
        throw "更新目录超出当前项目，已停止：$Value"
    }
}

function Get-ProjectHealth {
    try {
        $response = Invoke-WebRequest -UseBasicParsing -Uri $healthAddress -Method Get -TimeoutSec 2 -ErrorAction Stop
        return ConvertFrom-Json -InputObject ([System.Text.Encoding]::UTF8.GetString($response.RawContentStream.ToArray())) -ErrorAction Stop
    }
    catch { return $null }
}

function Test-ThisProject($Health) {
    if ($null -eq $Health) { return $false }
    $fields = $Health.PSObject.Properties.Name
    if (-not ($fields -contains 'status') -or -not ($fields -contains 'app') -or -not ($fields -contains 'projectRoot')) { return $false }
    if ($Health.status -ne 'ok' -or $Health.app -ne 'travel-genui') { return $false }
    return [string]::Equals((Normalize-ProjectPath ([string]$Health.projectRoot)), (Normalize-ProjectPath $projectDirectory), [StringComparison]::OrdinalIgnoreCase)
}

function Test-CurrentServer($Health, $Expected) {
    if (-not (Test-ThisProject $Health)) { return $false }
    $fields = $Health.PSObject.Properties.Name
    return ($fields -contains 'serverRevision') -and ($fields -contains 'version') -and $Health.version -eq $Expected.version -and $Health.serverRevision -eq $Expected.serverRevision
}

function Test-LocalPort {
    $client = New-Object System.Net.Sockets.TcpClient
    try {
        $result = $client.BeginConnect('127.0.0.1', 8011, $null, $null)
        if (-not $result.AsyncWaitHandle.WaitOne(700, $false)) { return $false }
        $client.EndConnect($result)
        return $true
    }
    catch { return $false }
    finally { $client.Dispose() }
}

function Test-FrontendBuild([string]$Directory, $Expected) {
    try {
        $infoPath = Join-Path $Directory 'build-info.json'
        if (-not (Test-Path -LiteralPath $infoPath -PathType Leaf)) { return $false }
        $info = Get-Content -LiteralPath $infoPath -Raw -Encoding UTF8 | ConvertFrom-Json
        if ($info.version -ne $Expected.version -or $info.frontendRevision -ne $Expected.frontendRevision) { return $false }
        if (-not ($info.files -contains 'index.html')) { return $false }
        $prefix = (Normalize-ProjectPath $Directory) + [System.IO.Path]::DirectorySeparatorChar
        foreach ($file in $info.files) {
            $artifact = Normalize-ProjectPath (Join-Path $Directory $file)
            if (-not $artifact.StartsWith($prefix, [StringComparison]::OrdinalIgnoreCase) -or -not (Test-Path -LiteralPath $artifact -PathType Leaf)) { return $false }
        }
        return $true
    }
    catch { return $false }
}

function Get-NodeExecutable {
    $command = Get-Command node.exe -ErrorAction SilentlyContinue
    if ($command) { return $command.Source }
    foreach ($candidate in @(
        (Join-Path $env:USERPROFILE '.cache\codex-runtimes\codex-primary-runtime\dependencies\node\bin\node.exe'),
        (Join-Path $env:ProgramFiles 'nodejs\node.exe')
    )) {
        if (Test-Path -LiteralPath $candidate -PathType Leaf) { return $candidate }
    }
    throw '网页源码已更新，但未找到 Node.js。请安装 Node.js 22 或更高版本，再双击启动。'
}

function Update-Frontend($Expected) {
    if (Test-FrontendBuild $builtDirectory $Expected) { return }
    Write-Host '检测到网页更新，正在构建当前源码…' -ForegroundColor Cyan
    $nodeExecutable = Get-NodeExecutable
    $tsc = Join-Path $projectDirectory 'node_modules\typescript\bin\tsc'
    $vite = Join-Path $projectDirectory 'node_modules\vite\bin\vite.js'
    if (-not (Test-Path -LiteralPath $tsc -PathType Leaf) -or -not (Test-Path -LiteralPath $vite -PathType Leaf)) {
        throw '缺少前端依赖，请先在项目目录运行 pnpm install --frozen-lockfile，再启动。'
    }
    $buildToken = [Guid]::NewGuid().ToString('N')
    $stagingDirectory = Join-Path $runtimeDirectory "frontend-build-$buildToken"
    $previousDirectory = Join-Path $runtimeDirectory "frontend-previous-$buildToken"
    Assert-WithinProject $stagingDirectory
    Assert-WithinProject $previousDirectory
    Assert-WithinProject $builtDirectory
    Push-Location -LiteralPath $projectDirectory
    try {
        & $nodeExecutable $tsc --noEmit
        if ($LASTEXITCODE -ne 0) { throw '网页类型检查失败，已保留原构建；请查看上方错误。' }
        & $nodeExecutable $vite build --outDir $stagingDirectory --emptyOutDir
        if ($LASTEXITCODE -ne 0) { throw '网页构建失败，已保留原构建；请查看上方错误。' }
        if (-not (Test-FrontendBuild $stagingDirectory $Expected)) { throw '网页构建版本校验失败，已保留原构建。' }
        if (Test-Path -LiteralPath $builtDirectory) { Move-Item -LiteralPath $builtDirectory -Destination $previousDirectory }
        try { Move-Item -LiteralPath $stagingDirectory -Destination $builtDirectory }
        catch {
            if (Test-Path -LiteralPath $previousDirectory) { Move-Item -LiteralPath $previousDirectory -Destination $builtDirectory }
            throw
        }
        if (Test-Path -LiteralPath $previousDirectory) {
            try { Remove-Item -LiteralPath $previousDirectory -Recurse -Force }
            catch { Write-Warning "当前网页已更新，但临时旧构建被占用，暂未清除：$previousDirectory" }
        }
        Write-Host '网页已构建并更新。' -ForegroundColor Green
    }
    finally {
        Pop-Location
        if (Test-Path -LiteralPath $stagingDirectory) { Remove-Item -LiteralPath $stagingDirectory -Recurse -Force }
    }
}

function Stop-PreviousServer($PreviousHealth) {
    $confirmed = Get-ProjectHealth
    if (-not (Test-ThisProject $confirmed) -or -not ($confirmed.PSObject.Properties.Name -contains 'processId') -or $confirmed.processId -ne $PreviousHealth.processId) {
        throw '服务进程已变化，未结束任何程序。请重新启动旅序。'
    }
    $serverProcessId = [int]$confirmed.processId
    $process = Get-CimInstance Win32_Process -Filter "ProcessId = $serverProcessId"
    $listeners = @(Get-NetTCPConnection -LocalPort 8011 -State Listen -ErrorAction SilentlyContinue)
    if ($null -eq $process -or [string]::IsNullOrWhiteSpace($process.CommandLine) -or $process.CommandLine -notmatch '(?i)(?:^|\s)-m\s+uvicorn(?:\s|$)' -or $process.CommandLine -notmatch 'backend\.app:app' -or -not ($listeners.OwningProcess -contains $serverProcessId)) {
        throw '无法确认当前项目的后台进程，未结束任何程序。'
    }
    Write-Host '检测到旧版后台，正在重启当前项目服务…' -ForegroundColor Cyan
    Stop-Process -Id $serverProcessId -ErrorAction Stop
    for ($attempt = 0; $attempt -lt 40; $attempt++) {
        if (-not (Test-LocalPort)) { return }
        Start-Sleep -Milliseconds 250
    }
    throw '旧服务停止后8011端口仍未释放，请稍后重新启动。'
}

try {
    $mutexHash = [System.Security.Cryptography.SHA256]::Create()
    try { $mutexKey = [BitConverter]::ToString($mutexHash.ComputeHash([System.Text.Encoding]::UTF8.GetBytes($projectDirectory.ToLowerInvariant()))).Replace('-', '').Substring(0, 16) }
    finally { $mutexHash.Dispose() }
    $launchMutex = New-Object System.Threading.Mutex($false, "Local\TravelGenUI-start-$mutexKey")
    try { $ownsMutex = $launchMutex.WaitOne(0) } catch [System.Threading.AbandonedMutexException] { $ownsMutex = $true }
    if (-not $ownsMutex) { Write-Host '旅序正在另一启动窗口中更新，请稍候。'; exit 0 }
    if (-not (Test-Path -LiteralPath $pythonExecutable -PathType Leaf)) { throw "没有找到项目 Python 环境：$pythonExecutable`n请按 README.md 准备 .venv。" }
    Push-Location -LiteralPath $projectDirectory
    try {
        $revisionJson = & $pythonExecutable -m backend.revision
        if ($LASTEXITCODE -ne 0) { throw '无法读取当前源码版本，请检查项目文件与 Python 环境。' }
        $expected = ($revisionJson -join "`n") | ConvertFrom-Json
    }
    finally { Pop-Location }
    Write-Host "旅序 v$($expected.version) · 检查并启动当前版本" -ForegroundColor Cyan
    Write-Host '本版功能：全部日期与时段编辑 · 真实景点替换 · 候选预览与持久历史 · 副本8011端口' -ForegroundColor Cyan
    Write-Host "项目目录：$projectDirectory"
    $health = Get-ProjectHealth
    if (-not (Test-ThisProject $health) -and (Test-LocalPort)) { throw '8011 端口已有服务，但无法确认属于此项目。请检查现有服务；本脚本不会关闭它。' }
    New-Item -ItemType Directory -Path $runtimeDirectory -Force | Out-Null
    Update-Frontend $expected
    $health = Get-ProjectHealth
    $browserAddress = "$webAddress/?v=$($expected.frontendRevision.Substring(0, 12))-$($expected.serverRevision.Substring(0, 12))"
    if (Test-CurrentServer $health $expected) {
        Write-Host "当前版本已在运行：$browserAddress" -ForegroundColor Green
        if (-not $NoBrowser) { Start-Process -FilePath $browserAddress }
        exit 0
    }
    if (Test-ThisProject $health) {
        if (-not ($health.PSObject.Properties.Name -contains 'processId')) { throw '旧服务未提供进程编号，请先用停止网页.cmd停止后再启动。' }
        Stop-PreviousServer $health
    }
    if (Test-LocalPort) { throw '8011端口被其他进程占用，已停止启动。' }
    $standardLog = Join-Path $runtimeDirectory 'server.stdout.log'
    $errorLog = Join-Path $runtimeDirectory 'server.stderr.log'
    Write-Host '正在启动旅序本地服务…' -ForegroundColor Cyan
    $server = Start-Process -FilePath $pythonExecutable `
        -ArgumentList @('-m', 'uvicorn', 'backend.app:app', '--host', '127.0.0.1', '--port', '8011') `
        -WorkingDirectory $projectDirectory -WindowStyle Hidden `
        -RedirectStandardOutput $standardLog -RedirectStandardError $errorLog -PassThru
    Set-Content -LiteralPath (Join-Path $runtimeDirectory 'server.pid') -Value $server.Id -Encoding ascii
    for ($attempt = 0; $attempt -lt 40; $attempt++) {
        Start-Sleep -Milliseconds 500
        $health = Get-ProjectHealth
        if (Test-CurrentServer $health $expected) {
            Set-Content -LiteralPath (Join-Path $runtimeDirectory 'server.pid') -Value $health.processId -Encoding ascii
            Write-Host "旅序 v$($expected.version) 已启动：$browserAddress" -ForegroundColor Green
            Write-Host "日志保存在：$runtimeDirectory"
            if (-not $NoBrowser) { Start-Process -FilePath $browserAddress }
            exit 0
        }
        $server.Refresh()
        if ($server.HasExited) { throw "服务启动失败（退出码 $($server.ExitCode)）。请查看 $errorLog" }
    }
    throw "服务尚未就绪。请查看 $errorLog 与 $standardLog。"
}
catch { Write-Host $_.Exception.Message -ForegroundColor Red; exit 1 }
finally {
    if ($ownsMutex) { $launchMutex.ReleaseMutex() }
    if ($null -ne $launchMutex) { $launchMutex.Dispose() }
}
