$ErrorActionPreference = "Stop"

$appDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$appPath = Join-Path $appDir "taskbar_text_clipboard.pyw"
$shortcutName = "Taskbar Text Clipboard.lnk"
$programsDir = [Environment]::GetFolderPath("Programs")
$shortcutPath = Join-Path $programsDir $shortcutName

function Find-Pythonw {
    $cmd = Get-Command pythonw.exe -ErrorAction SilentlyContinue
    if ($cmd) { return $cmd.Source }

    $candidates = @(
        "$env:LocalAppData\Programs\Python\Python312\pythonw.exe",
        "$env:LocalAppData\Programs\Python\Python311\pythonw.exe",
        "$env:LocalAppData\Programs\Python\Python310\pythonw.exe",
        "$env:ProgramFiles\Python312\pythonw.exe",
        "$env:ProgramFiles\Python311\pythonw.exe",
        "$env:ProgramFiles\Python310\pythonw.exe"
    )

    foreach ($candidate in $candidates) {
        if (Test-Path $candidate) { return $candidate }
    }

    return $null
}

$pythonw = Find-Pythonw
if (-not $pythonw) {
    Write-Host "Pythonw.exe was not found."
    Write-Host "Install Python from python.org, then run this installer again."
    pause
    exit 1
}

$wsh = New-Object -ComObject WScript.Shell
$link = $wsh.CreateShortcut($shortcutPath)
$link.TargetPath = $pythonw
$link.Arguments = "`"$appPath`""
$link.WorkingDirectory = $appDir
$link.IconLocation = "$env:SystemRoot\System32\shell32.dll,167"
$link.Description = "Open saved text clipboard popup"
$link.Save()

Write-Host ""
Write-Host "Shortcut created:"
Write-Host $shortcutPath
Write-Host ""
Write-Host "To pin it:"
Write-Host "1. Press the Windows key."
Write-Host "2. Search: Taskbar Text Clipboard"
Write-Host "3. Right-click it."
Write-Host "4. Click Pin to taskbar."
Write-Host ""
pause
