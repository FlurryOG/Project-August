$shortcutPath = "$env:APPDATA\Microsoft\Windows\Start Menu\Programs\Startup\August Voice AI.lnk"
$projectDir   = 'f:\Documents\Project---AUGEST'
$batFile      = "$projectDir\Start August.bat"

$ws = New-Object -ComObject WScript.Shell
$s  = $ws.CreateShortcut($shortcutPath)
$s.TargetPath       = $batFile
$s.WorkingDirectory = $projectDir
$s.Description      = 'August Voice AI — starts automatically on login'
$s.WindowStyle      = 1
$s.Save()

Write-Host " [OK] Startup shortcut created at: $shortcutPath"
