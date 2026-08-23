param(
  [string]$HostAddr = "127.0.0.1",
  [int]$Port = 8080,
  [int]$BackendInterval = 3,
  [int]$MemberInterval = 15
)

$ErrorActionPreference = "Stop"

# 让重定向日志稳定输出 UTF-8（避免中文在 .run/logs 里乱码）
$env:PYTHONUTF8 = "1"
$env:PYTHONIOENCODING = "utf-8"

function Resolve-Python {
  $py = Get-Command "python" -ErrorAction SilentlyContinue
  if ($py) { return @($py.Source, @()) }
  $py = Get-Command "py" -ErrorAction SilentlyContinue
  if ($py) { return @($py.Source, @("-3")) }
  throw "python/py not found. Please install Python and ensure it is in PATH."
}

function Start-Worker {
  param(
    [string]$Name,
    [string]$Exe,
    [string[]]$ArgList,
    [string]$WorkDir
  )
  Write-Host ("[{0}] Start: {1} {2}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $Name, ($ArgList -join " "))
  $outFile = Join-Path $logDir ("{0}.out.log" -f $Name)
  $errFile = Join-Path $logDir ("{0}.err.log" -f $Name)
  Write-Host ("  logs: {0} | {1}" -f $outFile, $errFile)
  $p = Start-Process -FilePath $Exe -ArgumentList $ArgList -WorkingDirectory $WorkDir -RedirectStandardOutput $outFile -RedirectStandardError $errFile -PassThru
  return $p
}

$root = $PSScriptRoot
Set-Location $root

$runDir = Join-Path $root ".run"
New-Item -ItemType Directory -Force -Path $runDir | Out-Null
$logDir = Join-Path $runDir "logs"
New-Item -ItemType Directory -Force -Path $logDir | Out-Null

$resolved = Resolve-Python
$pyExe = $resolved[0]
$pyPrefix = $resolved[1]

$procs = @()

# 1) Backend watcher
$procs += Start-Worker -Name "ag_wmc_watch" -Exe $pyExe -ArgList (@($pyPrefix) + @(
  "-u", "ag_wmc_watch.py",
  "--config", "config.json",
  "--timeout", "30",
  "--interval", "$BackendInterval",
  "--only-orders"
)) -WorkDir $root

# 1.5) OOK 信号转发（orders -> webhook）
$procs += Start-Worker -Name "ook_signal_forwarder" -Exe $pyExe -ArgList (@($pyPrefix) + @(
  "-u", "..\\ook\\signal_forwarder.py",
  "--orders-dir", "orders",
  "--config", "config.json",
  "--state", ".run/ook_signal_forwarder_state.json"
)) -WorkDir $root

# 1.6) OOK 信号拉取（remote signals -> local orders）
$procs += Start-Worker -Name "ook_signal_puller" -Exe $pyExe -ArgList (@($pyPrefix) + @(
  "-u", "..\\ook\\signal_puller.py",
  "--orders-dir", "orders",
  "--config", "config.json",
  "--state", ".run/ook_signal_puller_state.json"
)) -WorkDir $root

# 2) WebUI
$procs += Start-Worker -Name "webui_server" -Exe $pyExe -ArgList (@($pyPrefix) + @(
  "-u", "webui_server.py",
  "--host", "$HostAddr",
  "--port", "$Port",
  "--orders-dir", "orders",
  "--webui-dir", "webui",
  "--state-dir", "state",
  "--config", "config.json",
  "--data-dir", "data"
)) -WorkDir $root

# 3) Member keepalive
$procs += Start-Worker -Name "member_arsenal_watch" -Exe $pyExe -ArgList (@($pyPrefix) + @(
  "-u", "member_arsenal_watch.py",
  "--data-dir", "data",
  "--interval", "$MemberInterval"
)) -WorkDir $root

# 4) Follow engine (LIVE)
$followArgs = @(
  "-u", "follow_engine.py",
  "--orders-dir", "orders",
  "--data-dir", "data"
)
$procs += Start-Worker -Name "follow_engine" -Exe $pyExe -ArgList (@($pyPrefix) + $followArgs) -WorkDir $root

# 5) Telegram bot（接收指令 /today 输出今日跟单报表）
$procs += Start-Worker -Name "telegram_bot" -Exe $pyExe -ArgList (@($pyPrefix) + @(
  "-u", "telegram_bot.py",
  "--config", "config.json",
  "--data-dir", "data",
  "--orders-dir", "orders",
  "--state", ".run/tg_bot_state.json"
)) -WorkDir $root

$pidFile = Join-Path $runDir "pids.json"
$payload = @{
  started_at = (Get-Date).ToString("s")
  host = $HostAddr
  port = $Port
  live = $true
  pids = @($procs | ForEach-Object { $_.Id })
}
$payload | ConvertTo-Json -Depth 6 | Set-Content -Encoding UTF8 $pidFile

Write-Host ""
Write-Host ("WebUI: http://{0}:{1}/" -f $HostAddr, $Port)
Write-Host ("PID file: {0}" -f $pidFile)
Write-Host "Stop all: .\\stop_all.ps1"
