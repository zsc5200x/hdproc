param(
  [switch]$Force
)

$ErrorActionPreference = "Stop"

$root = $PSScriptRoot
Set-Location $root

# Delegate to app.py stop to avoid PID reuse killing unrelated processes.
$py = Get-Command "python" -ErrorAction SilentlyContinue
if (-not $py) {
  $py = Get-Command "py" -ErrorAction SilentlyContinue
  if ($py) {
    if ($Force) { & $py.Source -3 "app.py" stop --force } else { & $py.Source -3 "app.py" stop }
    exit $LASTEXITCODE
  }
  Write-Host "python/py not found. Please install Python and ensure it is in PATH."
  exit 2
}

if ($Force) { & $py.Source "app.py" stop --force } else { & $py.Source "app.py" stop }
exit $LASTEXITCODE
