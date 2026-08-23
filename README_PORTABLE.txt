AG Matrix (HDYDF) Portable

1) Double-click Start_AG_Matrix.bat
2) Open: http://127.0.0.1:9999/
3) Auto-starts the full project stack

macOS:
1) Double-click Start_AG_Matrix_ALL.command (or SAFE)
2) First run auto-heals .venv and installs deps/playwright if needed
3) Open: http://127.0.0.1:9999/

macOS app bundle:
- Double-click Build_Mac_App.command
- Output: _dist/AG Matrix.app
- Drag AG Matrix.app into Applications, then double-click it like a normal app

macOS standalone app bundle:
- Double-click Build_Mac_Standalone_App.command
- Output: _dist/AG Matrix Standalone.app
- Friend's Mac does not need Python installed
- Launches the current WebUI inside the app's own desktop window (no external browser)
- First launch copies runtime files to ~/Library/Application Support/AG Matrix Standalone/
- If app/config.json exists during build, it is bundled into the standalone app as the initial config

macOS package build:
- Double-click Build_Mac_Portable_ZIP.command
- Copy _dist/*.zip to client Mac, unzip, then run Start_AG_Matrix_ALL.command

Diagnostics:
- http://127.0.0.1:9999/api/diagnostics
- Logs: app\\.run\\logs\\*.err.log（默认）

Notes:
- Bundled Python + deps + Playwright browser: chromium
- Config: app\\config.json
- Data  : app\\data\\
- Working directory: app\\

