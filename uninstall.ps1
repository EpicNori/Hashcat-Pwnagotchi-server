param(
    [string]$InstallRoot = "C:\ProgramData\HashcatWPAServer"
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

function Ensure-Administrator {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = New-Object Security.Principal.WindowsPrincipal($identity)
    if ($principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
        return $true
    }

    $launchArgs = @(
        "-NoProfile",
        "-ExecutionPolicy", "Bypass",
        "-File", $PSCommandPath,
        "-InstallRoot", $InstallRoot
    )
    $process = Start-Process -FilePath "powershell.exe" -Verb RunAs -ArgumentList $launchArgs -Wait -PassThru
    if ($process.ExitCode -ne 0 -and (-not (Test-Path $InstallRoot))) {
        return $false
    }
    if ($process.ExitCode -ne 0) {
        throw "Elevated uninstall failed with exit code $($process.ExitCode)."
    }
    return $false
}

if (-not (Ensure-Administrator)) {
    return
}

$UninstallScript = Join-Path $PSScriptRoot "windows\uninstall_app.ps1"

if (-not (Test-Path $UninstallScript)) {
    throw "Uninstall helper not found: $UninstallScript"
}

& powershell.exe -NoProfile -ExecutionPolicy Bypass -File $UninstallScript -InstallRoot $InstallRoot
if ($LASTEXITCODE -ne 0) {
    throw "Uninstall failed."
}
