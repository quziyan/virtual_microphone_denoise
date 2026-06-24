param(
  [switch]$Clean
)

$ErrorActionPreference = "Stop"

$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
Set-Location $RepoRoot

if ($Clean) {
  Remove-Item -LiteralPath ".venv-win" -Recurse -Force -ErrorAction SilentlyContinue
  Remove-Item -LiteralPath "build" -Recurse -Force -ErrorAction SilentlyContinue
}

if (-not (Test-Path ".venv-win\Scripts\python.exe")) {
  python -m venv .venv-win
}

.\.venv-win\Scripts\python.exe -m pip install --upgrade pip
.\.venv-win\Scripts\python.exe -m pip install -r requirements-windows.txt

.\.venv-win\Scripts\python.exe -m PyInstaller packaging\VibeCodingVirMicWindows.spec --noconfirm

$Version = (& .\.venv-win\Scripts\python.exe -c "import sys; sys.path.insert(0, 'src'); from version import __version__; print(__version__)").Trim()
$Exe = "dist\VibeCodingVirMic-Windows-$Version.exe"
if (-not (Test-Path $Exe)) {
  throw "Expected exe not found: $Exe"
}

& $Exe --selftest
Write-Host "Built $Exe"
