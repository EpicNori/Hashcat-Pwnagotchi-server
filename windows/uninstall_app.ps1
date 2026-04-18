param(
    [string]$InstallRoot = "C:\ProgramData\HashcatWPAServer"
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$CurrentRoot = Join-Path $InstallRoot "current"
$TaskScript = Join-Path $CurrentRoot "windows\autostart_service.ps1"
$CliScript = Join-Path $CurrentRoot "windows\crackserver.ps1"

if (Test-Path $TaskScript) {
    & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $TaskScript disable -InstallRoot $InstallRoot | Out-Null
}
if (Test-Path $CliScript) {
    & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $CliScript stop -InstallRoot $InstallRoot | Out-Null
}

$cmd = "ping 127.0.0.1 -n 6 > nul && rmdir /s /q `"$InstallRoot`""
Start-Process -FilePath "cmd.exe" -ArgumentList @("/c", $cmd) -WindowStyle Hidden | Out-Null
Write-Output "started"
