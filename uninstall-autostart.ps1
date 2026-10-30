$startup = [Environment]::GetFolderPath("Startup")
$shortcutPath = Join-Path $startup "Silence Filler.lnk"

if (Test-Path $shortcutPath) {
    Remove-Item $shortcutPath -Force
    Write-Host "Autostart entfernt: $shortcutPath"
} else {
    Write-Host "Kein Autostart-Eintrag gefunden."
}
