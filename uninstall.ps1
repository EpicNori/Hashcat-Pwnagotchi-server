param(
    [string]$InstallRoot = "C:\ProgramData\HashcatWPAServer"
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$UninstallScript = Join-Path $PSScriptRoot "windows\uninstall_app.ps1"

if (-not (Test-Path $UninstallScript)) {
    throw "Uninstall helper not found: $UninstallScript"
}

& powershell.exe -NoProfile -ExecutionPolicy Bypass -File $UninstallScript -InstallRoot $InstallRoot
if ($LASTEXITCODE -ne 0) {
    throw "Uninstall failed."
}
