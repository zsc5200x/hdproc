param(
  [string]$OutDir = "",
  [ValidateSet("chromium","firefox","webkit")]
  [string]$Browser = "chromium",
  [switch]$IncludeLogs
)

$ErrorActionPreference = "Stop"

function Write-Info([string]$msg) { Write-Host "[INFO] $msg" }
function Write-Warn([string]$msg) { Write-Host "[WARN] $msg" -ForegroundColor Yellow }
function Write-Err([string]$msg)  { Write-Host "[ERROR] $msg" -ForegroundColor Red }

function Ensure-Dir([string]$p) {
  if (-not (Test-Path $p)) { New-Item -ItemType Directory -Path $p | Out-Null }
}

function Get-PyVer() {
  $v = & python -c "import sys; print('.'.join(map(str, sys.version_info[:3])))"
  return ($v | Select-Object -First 1).Trim()
}

function Get-EmbedUrl([string]$ver) {
  # Windows x64 embeddable zip
  return "https://www.python.org/ftp/python/$ver/python-$ver-embed-amd64.zip"
}

function Download([string]$url, [string]$dest) {
  Write-Info "Downloading: $url"
  Invoke-WebRequest -Uri $url -OutFile $dest -UseBasicParsing
}

function Unzip([string]$zip, [string]$dest) {
  Ensure-Dir $dest
  Expand-Archive -Path $zip -DestinationPath $dest -Force
}

function Patch-Pth([string]$pythonDir) {
  $pth = Get-ChildItem -Path $pythonDir -Filter "python*._pth" | Select-Object -First 1
  if (-not $pth) { throw "Cannot find python*._pth under $pythonDir" }
  $lines = Get-Content $pth.FullName
  $out = @()
  $hasApp = $false
  $hasSitePackages = $false
  $hasImportSite = $false
  foreach ($ln in $lines) {
    if ($ln -match '^\s*\.\.\\app\s*$') { $hasApp = $true }
    if ($ln -match '^\s*Lib\\site-packages\s*$') { $hasSitePackages = $true }
    if ($ln -match '^\s*import\s+site\s*$') { $hasImportSite = $true }
    $out += $ln
  }
  # Make local app modules importable in the embeddable distro (important for `portable_env.py`, etc.).
  if (-not $hasApp) { $out += "..\\app" }
  if (-not $hasSitePackages) { $out += "Lib\\site-packages" }
  if (-not $hasImportSite) { $out += "import site" }
  Set-Content -Path $pth.FullName -Value $out -Encoding ASCII
}

function Copy-Tree([string]$src, [string]$dst) {
  Ensure-Dir $dst
  Copy-Item -Path (Join-Path $src "*") -Destination $dst -Recurse -Force
}

function Robocopy-Tree([string]$src, [string]$dst, [bool]$includeLogs) {
  Ensure-Dir $dst
  $exclude = @(
    # packaging artifacts / recursion traps (from previous runs)
    "dist",
    "hdydf",
    "__pycache__",
    "data\\_pw_member",
    "data\\captures",
    "data\\debug",
    "data\\_tmp"
  )
  if (-not $includeLogs) {
    $exclude += ".run"
  }
  $args = @($src, $dst, "/E", "/NFL", "/NDL", "/NJH", "/NJS", "/NP", "/R:1", "/W:1")
  foreach ($d in $exclude) {
    $args += "/XD"
    $args += $d
  }
  $p = Start-Process -FilePath robocopy -ArgumentList $args -NoNewWindow -Wait -PassThru
  $rc = [int]($p.ExitCode)
  # robocopy exit code: 0-7 success, 8+ failure
  if ($rc -ge 8) { throw "robocopy failed with exit code $rc" }
}

try {
  $root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
  $pyVer = Get-PyVer
  if (-not $OutDir) {
    $stamp = (Get-Date -Format "yyyyMMdd_HHmmss")
    $OutDir = (Join-Path $root ("dist/AGMatrix-HDYDF_$stamp"))
  } else {
    if (-not [System.IO.Path]::IsPathRooted($OutDir)) {
      $OutDir = (Join-Path $root $OutDir)
    }
    if (Test-Path $OutDir) {
      # 避免覆盖/删除用户已有目录：自动落到子目录
      $stamp = (Get-Date -Format "yyyyMMdd_HHmmss")
      $OutDir = (Join-Path $OutDir ("AGMatrix-HDYDF_$stamp"))
    }
  }

  # 防止把输出目录放在项目根目录内导致“打包目录被再次打包”出现指数级膨胀
  $rootFull = [System.IO.Path]::GetFullPath($root)
  $outFull = [System.IO.Path]::GetFullPath($OutDir)
  if ($outFull.StartsWith($rootFull, [System.StringComparison]::OrdinalIgnoreCase)) {
    throw "OutDir must NOT be inside project root: choose e.g. E:\\AGMatrix_Packages (current OutDir=$OutDir)"
  }

  Ensure-Dir $OutDir
  Write-Info "Project root : $root"
  Write-Info "Output dir   : $OutDir"
  Write-Info "Python ver   : $pyVer"
  Write-Info "Browser      : $Browser"

  $work = Join-Path $OutDir "_work"
  Ensure-Dir $work

  # 1) Copy app files
  Write-Info "Copying app files..."
  $appDir = Join-Path $OutDir "app"
  Ensure-Dir $appDir
  Robocopy-Tree $root $appDir ([bool]$IncludeLogs)

  # 2) Download Python embeddable
  $pyZip = Join-Path $work ("python-$pyVer-embed-amd64.zip")
  if (-not (Test-Path $pyZip)) {
    Download (Get-EmbedUrl $pyVer) $pyZip
  } else {
    Write-Info "Using cached python zip: $pyZip"
  }
  $pyDir = Join-Path $OutDir "python"
  if (Test-Path $pyDir) {
    Write-Warn "python dir already exists, keeping it: $pyDir"
  } else {
    Unzip $pyZip $pyDir
  }
  Patch-Pth $pyDir
  Ensure-Dir (Join-Path $pyDir "Lib/site-packages")

  # 3) Install deps into embedded site-packages
  Write-Info "Installing deps into embedded Python (site-packages)..."
  $targetSite = (Join-Path $pyDir "Lib/site-packages")
  & python -m pip install -r (Join-Path $root "requirements.txt") --target $targetSite | Out-Null

  # 4) Install Playwright browser into package
  Write-Info "Installing Playwright browser into package..."
  $browsersDir = Join-Path $OutDir "ms-playwright"
  Ensure-Dir $browsersDir
  $env:PLAYWRIGHT_BROWSERS_PATH = $browsersDir
  & (Join-Path $pyDir "python.exe") -m playwright install $Browser | Out-Null

  # 5) Write launcher
  Write-Info "Writing launcher..."
  $launcherLines = @(
    '@echo off',
    'setlocal EnableExtensions',
    'chcp 65001 >nul',
    'cd /d %~dp0app',
    '',
    'set "PYTHONUTF8=1"',
    'set "PYTHONIOENCODING=utf-8"',
    'set "PLAYWRIGHT_BROWSERS_PATH=%~dp0ms-playwright"',
    'set "PYTHONPATH=%CD%"',
    'set "NO_PROXY=127.0.0.1,localhost"',
    'set "no_proxy=127.0.0.1,localhost"',
    '',
    'set "HOST=127.0.0.1"',
    'set "PORT=9999"',
    '',
    'rem Best-effort: stop existing listener on the port (avoid multiple old instances)',
    'for /f "tokens=5" %%a in (''netstat -ano ^| findstr /R /C:":%PORT% .*LISTENING"'') do (',
    '  taskkill /PID %%a /F >nul 2>nul',
    ')',
    '',
    'rem Start the full project stack with bundled Python.',
    '%~dp0python\\python.exe app.py start --host %HOST% --port %PORT%',
    'if errorlevel 1 (',
    '  echo.',
    '  echo [ERROR] Failed to start the full project stack.',
    '  echo.',
    '  pause',
    '  exit /b 1',
    ')',
    '',
    'start "" "http://%HOST%:%PORT%/"',
    '',
    '@echo.',
    '@pause'
  )
  $launcher = ($launcherLines -join "`r`n") + "`r`n"
  Set-Content -Path (Join-Path $OutDir "Start_AG_Matrix.bat") -Value $launcher -Encoding ASCII

  # 6) Readme
  # NOTE: keep this file ASCII-only for best compatibility with Windows PowerShell 5.1 encoding detection.
  $readmeLines = @(
    'AG Matrix (HDYDF) Portable',
    '',
    '1) Double-click Start_AG_Matrix.bat',
    '2) Open: http://127.0.0.1:9999/',
    '3) Auto-starts the full project stack',
    '',
    'Diagnostics:',
    '- http://127.0.0.1:9999/api/diagnostics',
    '- Logs: app\\.run\\logs\\*.err.log',
    '',
    'Notes:',
    ('- Bundled Python + deps + Playwright browser: ' + $Browser),
    '- Config: app\\config.json',
    '- Data  : app\\data\\',
    '- Working directory: app\\'
  )
  Set-Content -Path (Join-Path $OutDir "README_PORTABLE.txt") -Value (($readmeLines -join "`r`n") + "`r`n") -Encoding UTF8

  Write-Info "DONE. Package at: $OutDir"
  Write-Info "Launcher: $(Join-Path $OutDir 'Start_AG_Matrix.bat')"
} catch {
  Write-Err $_.Exception.Message
  exit 1
}
