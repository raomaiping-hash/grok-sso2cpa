$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

$separator = if ($env:OS -eq "Windows_NT") { ";" } else { ":" }
$staticData = "static${separator}static"

python -m PyInstaller `
  --noconfirm `
  --clean `
  --onedir `
  --name sso-bridge-backend `
  --distpath backend-dist `
  --workpath build/backend `
  --add-data $staticData `
  --collect-all curl_cffi `
  backend_runner.py
