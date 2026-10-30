$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$pythonw = Join-Path $root ".venv\Scripts\pythonw.exe"
$app = Join-Path $root "app.py"

if (-not (Test-Path $pythonw)) {
    throw "Die virtuelle Python-Umgebung fehlt. Bitte zuerst setup.bat ausfuehren."
}

$startup = [Environment]::GetFolderPath("Startup")
$shortcutPath = Join-Path $startup "Silence Filler.lnk"

$shell = New-Object -ComObject WScript.Shell
$shortcut = $shell.CreateShortcut($shortcutPath)
$shortcut.TargetPath = $pythonw
$shortcut.Arguments = "`"$app`""
$shortcut.WorkingDirectory = $root
$shortcut.WindowStyle = 7
$shortcut.Description = "Spotify bei Stille automatisch starten"
$shortcut.Save()

Write-Host "Autostart eingerichtet: $shortcutPath"
