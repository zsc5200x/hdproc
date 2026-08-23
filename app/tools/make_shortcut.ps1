param(
  [Parameter(Mandatory=$true)][string]$TargetBat,
  [string]$ShortcutPath = ""
)

$ErrorActionPreference = "Stop"

if (-not (Test-Path $TargetBat)) {
  throw "Target not found: $TargetBat"
}

$targetFull = (Resolve-Path $TargetBat).Path
if (-not $ShortcutPath) {
  $dir = Split-Path -Parent $targetFull
  $ShortcutPath = Join-Path $dir "AG Matrix (HDYDF).lnk"
}

$ws = New-Object -ComObject WScript.Shell
$sc = $ws.CreateShortcut($ShortcutPath)
$sc.TargetPath = $targetFull
$sc.WorkingDirectory = (Split-Path -Parent $targetFull)
$sc.WindowStyle = 1
$sc.Description = "AG Matrix (HDYDF) Control Center"
$sc.Save()

Write-Host "[OK] Shortcut created: $ShortcutPath"

