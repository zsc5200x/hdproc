param(
  [string]$HostAddr = "127.0.0.1",
  [int]$Port = 9999,
  [int]$BackendInterval = 3
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
    [string]$WorkDir,
    [string]$LogDir
  )
  Write-Host ("[{0}] Start: {1} {2}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $Name, ($ArgList -join " "))
  $outFile = Join-Path $LogDir ("{0}.out.log" -f $Name)
  $errFile = Join-Path $LogDir ("{0}.err.log" -f $Name)
  Write-Host ("  logs: {0} | {1}" -f $outFile, $errFile)
  return Start-Process -FilePath $Exe -ArgumentList $ArgList -WorkingDirectory $WorkDir -RedirectStandardOutput $outFile -RedirectStandardError $errFile -PassThru
}

$root = $PSScriptRoot
Set-Location $root

$runDir = Join-Path $root ".run"
$logDir = Join-Path $runDir "logs"
New-Item -ItemType Directory -Force -Path $runDir | Out-Null
New-Item -ItemType Directory -Force -Path $logDir | Out-Null

$resolved = Resolve-Python
$pyExe = $resolved[0]
$pyPrefix = $resolved[1]

$procs = @()

# 1) 监听登3（只抓 orders，不跟单）
$procs += Start-Worker -Name "ag_wmc_watch" -Exe $pyExe -ArgList (@($pyPrefix) + @(
  "-u", "ag_wmc_watch.py",
  "--config", "config.json",
  "--timeout", "30",
  "--interval", "$BackendInterval",
  "--only-orders"
)) -WorkDir $root -LogDir $logDir

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
)) -WorkDir $root -LogDir $logDir

$pidFile = Join-Path $runDir "ui_test_pids.json"
$payload = @{
  started_at = (Get-Date).ToString("s")
  host = $HostAddr
  port = $Port
  pids = @($procs | ForEach-Object { $_.Id })
}
$payload | ConvertTo-Json -Depth 6 | Set-Content -Encoding UTF8 $pidFile

Write-Host ""
Write-Host ("WebUI: http://{0}:{1}/" -f $HostAddr, $Port)
Write-Host ("PID file: {0}" -f $pidFile)
Write-Host "Stop: .\\stop_ui_test.ps1"

