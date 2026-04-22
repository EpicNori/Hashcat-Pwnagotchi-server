param(
    [string]$InstallRoot = "C:\ProgramData\HashcatWPAServer"
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$CurrentRoot = Join-Path $InstallRoot "current"
$RepoWindowsRoot = $PSScriptRoot
$TaskScript = Join-Path $CurrentRoot "windows\autostart_service.ps1"
$CliScript = Join-Path $CurrentRoot "windows\crackserver.ps1"
$FallbackTaskScript = Join-Path $RepoWindowsRoot "autostart_service.ps1"
$FallbackCliScript = Join-Path $RepoWindowsRoot "crackserver.ps1"

function Invoke-PreferredPowerShellScript([string]$Primary, [string]$Fallback, [string[]]$Arguments = @()) {
    foreach ($scriptPath in @($Primary, $Fallback)) {
        if (-not (Test-Path $scriptPath)) {
            continue
        }

        try {
            & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $scriptPath @Arguments | Out-Null
            if ($LASTEXITCODE -eq 0) {
                return $true
            }
        } catch {
        }
    }

    return $false
}

function Stop-InstallProcess([string]$PidFilePath) {
    if (-not (Test-Path $PidFilePath)) {
        return
    }

    $pidValue = Get-Content -LiteralPath $PidFilePath -ErrorAction SilentlyContinue | Select-Object -First 1
    if (-not [string]::IsNullOrWhiteSpace($pidValue)) {
        Stop-Process -Id $pidValue.Trim() -Force -ErrorAction SilentlyContinue
    }

    Get-Process -Name hashcat -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath $PidFilePath -Force -ErrorAction SilentlyContinue
}

Invoke-PreferredPowerShellScript -Primary $TaskScript -Fallback $FallbackTaskScript -Arguments @("disable", "-InstallRoot", $InstallRoot) | Out-Null
if (-not (Invoke-PreferredPowerShellScript -Primary $CliScript -Fallback $FallbackCliScript -Arguments @("stop", "-InstallRoot", $InstallRoot))) {
    Stop-InstallProcess -PidFilePath (Join-Path $InstallRoot "server.pid")
}

$cleanupDeadline = (Get-Date).AddSeconds(20)
do {
    try {
        if (Test-Path $InstallRoot) {
            Remove-Item -LiteralPath $InstallRoot -Recurse -Force
        }
    } catch [System.IO.IOException] {
        Start-Sleep -Milliseconds 500
        continue
    }
    break
} while ((Get-Date) -lt $cleanupDeadline)

if (Test-Path $InstallRoot) {
    $cmd = "ping 127.0.0.1 -n 6 > nul && rmdir /s /q `"$InstallRoot`""
    Start-Process -FilePath "cmd.exe" -ArgumentList @("/c", $cmd) -WindowStyle Hidden | Out-Null
}

Write-Output "uninstall-started"
