# Сборка: MeetingRecorder.exe (PyInstaller, папка dist\MeetingRecorder) и установщик (Inno Setup 6).
#   powershell -ExecutionPolicy Bypass -File build.ps1            exe + установщик (если найден iscc)
#   powershell -ExecutionPolicy Bypass -File build.ps1 -NoInstaller
# Переменные: MR_PYTHON — python с зависимостями; MR_BUILD_DIR — куда собирать (быстрее вне синхронизируемой папки).
param([switch]$NoInstaller)
$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot

$py = if ($env:MR_PYTHON) { $env:MR_PYTHON } elseif (Test-Path .venv\Scripts\python.exe) { '.venv\Scripts\python.exe' } else { 'python' }
$out = if ($env:MR_BUILD_DIR) { $env:MR_BUILD_DIR } else { $PSScriptRoot }
& $py -m pip install -q -r requirements.txt pyinstaller
& $py assets\make_icon.py

& $py -m PyInstaller --noconfirm --clean --windowed --name MeetingRecorder --icon "$PSScriptRoot\assets\icon.ico" `
    --distpath "$out\dist" --workpath "$out\build" --specpath "$out" `
    --collect-all faster_whisper --collect-all ctranslate2 --collect-all av --collect-all tzdata `
    --collect-submodules recurring_ical_events --hidden-import pyaudiowpatch --hidden-import pystray._win32 `
    "$PSScriptRoot\run.py"
if ($LASTEXITCODE) { throw 'PyInstaller failed' }
"Собрано: $out\dist\MeetingRecorder\MeetingRecorder.exe"

if ($NoInstaller) { exit 0 }
$iscc = (Get-Command iscc -ErrorAction SilentlyContinue).Source
if (-not $iscc) { $iscc = @("$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe", "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe", "$env:ProgramFiles\Inno Setup 6\ISCC.exe") | Where-Object { Test-Path $_ } | Select-Object -First 1 }
if (-not $iscc) { 'Inno Setup 6 не найден — установщик пропущен (winget install JRSoftware.InnoSetup).'; exit 0 }
& $iscc "/DDistDir=$out\dist" "/DOutDir=$out\installer_out" installer.iss
"Установщик: $out\installer_out\MeetingRecorder-Setup.exe"
