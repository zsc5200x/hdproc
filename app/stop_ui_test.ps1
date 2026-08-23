param(
  [switch]$Force
)

$ErrorActionPreference = "Stop"

$root = $PSScriptRoot
$pidFile = Join-Path $root ".run/ui_test_pids.json"

if (-not (Test-Path $pidFile)) {
  Write-Host ".run/ui_test_pids.json not found (maybe not started by run_ui_test.ps1)."
  exit 0
}

$data = Get-Content -Raw -Encoding UTF8 $pidFile | ConvertFrom-Json
$pids = @($data.pids)
if (-not $pids -or $pids.Count -eq 0) {
  Write-Host "PID list is empty."
  exit 0
}

foreach ($procId in $pids) {
  try {
    $p = Get-Process -Id $procId -ErrorAction Stop
    Write-Host ("Stop PID={0} Name={1}" -f $procId, $p.ProcessName)
    Stop-Process -Id $procId -Force:$Force
  } catch {
    Write-Host ("PID={0} not found/already exited" -f $procId)
  }
}

Remove-Item -Force $pidFile -ErrorAction SilentlyContinue
Write-Host "Done."

