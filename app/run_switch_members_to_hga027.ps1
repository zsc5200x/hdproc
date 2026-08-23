param(
  [string]$BaseUrl = "https://hga027.com",
  [string]$Labels = "summer001,summer002,summer003,summer004,summer005",
  [switch]$ClearSession = $true
)

$ErrorActionPreference = "Stop"

$env:PYTHONUTF8 = "1"
$env:PYTHONIOENCODING = "utf-8"

$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $root

$argsList = @("tools/switch_member_base_url.py", "--data-dir", "data", "--base-url", $BaseUrl)
if ($Labels) { $argsList += @("--labels", $Labels) }
if ($ClearSession) { $argsList += @("--clear-session") }

Write-Host ("[{0}] Patch member base_url -> {1} (labels={2}, clear_session={3})" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $BaseUrl, $Labels, [bool]$ClearSession)
python @argsList

Write-Host ""
Write-Host "接下来：等待 10~30 秒让 member_arsenal_watch 自动重登并刷新 Cookie/UID。"
Write-Host "验证："
Write-Host "  python app.py status"
Write-Host "  Get-Content .run/logs/member_arsenal_watch.out.log -Tail 60"
Write-Host "  Get-Content .run/logs/follow_engine.out.log -Tail 60"

