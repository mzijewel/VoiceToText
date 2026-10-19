# Windows installer: app + venv under %LOCALAPPDATA%\voice-to-text, a Startup-folder shortcut that starts
# the daemon at login, and a vtt.cmd helper. The daemon itself registers the global hotkey.
#
# Usage: powershell -ExecutionPolicy Bypass -File install-windows.ps1 [-Key '<ctrl>+<alt>+<space>']
#            [-Model small] [-Language auto] [-NoAutostart]
#        powershell -ExecutionPolicy Bypass -File install-windows.ps1 -Uninstall [-Purge]
param(
    [string]$Key = "",
    [string]$Model = "",
    [string]$Language = "",
    [switch]$NoAutostart,
    [switch]$Uninstall,
    [switch]$Purge
)
$ErrorActionPreference = "Stop"

$InstallDir = Join-Path $env:LOCALAPPDATA "voice-to-text"
$ConfigDir = Join-Path $env:APPDATA "voice-to-text"
$Venv = Join-Path $InstallDir "venv"
$Python = Join-Path $Venv "Scripts\python.exe"
$PythonW = Join-Path $Venv "Scripts\pythonw.exe"
$App = Join-Path $InstallDir "app.py"
$Shortcut = Join-Path ([Environment]::GetFolderPath("Startup")) "Voice to Text.lnk"

function Step($msg) { Write-Host "`n==> $msg" }

function Stop-Daemon {
    Get-CimInstance Win32_Process -Filter "Name LIKE 'python%'" |
        Where-Object { $_.CommandLine -like "*app.py*daemon*" } |
        ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
}

if ($Uninstall) {
    Step "Stopping the daemon and removing autostart"
    Stop-Daemon
    Remove-Item -Force -ErrorAction SilentlyContinue $Shortcut
    Step "Removing the app from $InstallDir"
    Remove-Item -Recurse -Force -ErrorAction SilentlyContinue $Venv
    Get-ChildItem $InstallDir -ErrorAction SilentlyContinue |
        Where-Object { $_.Name -notlike "recording-*.wav" } |
        Remove-Item -Force -ErrorAction SilentlyContinue
    if ($Purge) {
        Step "Purging recordings, settings and downloaded Whisper models"
        Remove-Item -Recurse -Force -ErrorAction SilentlyContinue $InstallDir, $ConfigDir
        Get-ChildItem (Join-Path $HOME ".cache\huggingface\hub") -Directory -ErrorAction SilentlyContinue |
            Where-Object { $_.Name -like "models--*faster-whisper-*" } |
            Remove-Item -Recurse -Force -ErrorAction SilentlyContinue
    } else {
        Write-Host "Kept recordings in $InstallDir and settings in $ConfigDir (use -Purge to delete them)."
    }
    exit 0
}

# Prefer the py launcher; the Microsoft Store "python" alias only opens the Store.
$Base = $null
if (Get-Command py -ErrorAction SilentlyContinue) { $Base = @("py", "-3") }
elseif (Get-Command python -ErrorAction SilentlyContinue) { $Base = @("python") }
if (-not $Base) { throw "Python 3 not found; install it from python.org (tick 'Add python.exe to PATH')." }

Step "Installing app to $InstallDir"
New-Item -ItemType Directory -Force $InstallDir | Out-Null
Stop-Daemon
Copy-Item -Force (Join-Path $PSScriptRoot "app.py") $App

Step "Setting up Python venv and dependencies"
if (-not (Test-Path $Python)) {
    $BaseArgs = @($Base | Select-Object -Skip 1)
    & $Base[0] @BaseArgs -m venv $Venv
    if ($LASTEXITCODE) { throw "creating the venv failed" }
}
& $Python -m pip install --quiet --upgrade pip
& $Python -m pip install --quiet faster-whisper sounddevice numpy
if ($LASTEXITCODE) { throw "pip install failed" }

if ($Key) { & $Python $App set hotkey $Key }
if ($Model) { & $Python $App set model $Model }
if ($Language) { & $Python $App set language $Language }

Step "Creating vtt.cmd"
Set-Content -Encoding ASCII (Join-Path $InstallDir "vtt.cmd") "@`"$Python`" `"$App`" %*"

if (-not $NoAutostart) {
    Step "Adding a Startup shortcut (starts the daemon at login)"
    $shell = New-Object -ComObject WScript.Shell
    $lnk = $shell.CreateShortcut($Shortcut)
    $lnk.TargetPath = $PythonW
    $lnk.Arguments = "`"$App`" daemon"
    $lnk.WorkingDirectory = $InstallDir
    $lnk.Description = "Voice to Text daemon"
    $lnk.Save()
    Step "Starting the daemon"
    Start-Process -FilePath $PythonW -ArgumentList "`"$App`"", "daemon" -WorkingDirectory $InstallDir
}

$Hotkey = (& $Python $App settings | Select-String '^hotkey\s+(\S+)').Matches.Groups[1].Value
Step "Done"
Write-Host "Press $Hotkey to start recording, press again to transcribe and paste."
Write-Host "The first transcription downloads the Whisper model, so it will be slow."
Write-Host "Settings: `"$InstallDir\vtt.cmd`" settings | set language bn | devices"
Write-Host "  (add $InstallDir to your PATH to just type: vtt settings)"
Write-Host "Logs: Get-Content -Wait `"$InstallDir\voice-to-text.log`""
