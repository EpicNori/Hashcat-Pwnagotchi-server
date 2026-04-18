param(
    [ValidateSet("run", "status")]
    [string]$Action = "run",
    [string]$InstallRoot = "C:\ProgramData\HashcatWPAServer"
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$LogsRoot = Join-Path $InstallRoot "logs"
$PidFile = Join-Path $InstallRoot "updater.pid"
$LogFile = Join-Path $LogsRoot "updater.log"
$ErrorLogFile = Join-Path $LogsRoot "updater_stderr.log"
$UpdateScript = Join-Path $InstallRoot "current\update.ps1"

New-Item -ItemType Directory -Path $LogsRoot -Force | Out-Null

if ($Action -eq "status") {
    if (Test-Path $PidFile) {
        $pidValue = Get-Content -LiteralPath $PidFile -ErrorAction SilentlyContinue | Select-Object -First 1
        if (-not [string]::IsNullOrWhiteSpace($pidValue) -and (Get-Process -Id $pidValue.Trim() -ErrorAction SilentlyContinue)) {
            Write-Output "running"
            exit 0
        }
        Remove-Item -LiteralPath $PidFile -Force -ErrorAction SilentlyContinue
    }
    Write-Output "idle"
    exit 0
}

$process = Start-Process `
    -FilePath "powershell.exe" `
    -ArgumentList @("-NoProfile", "-ExecutionPolicy", "Bypass", "-File", $UpdateScript) `
    -WorkingDirectory (Split-Path $UpdateScript -Parent) `
    -WindowStyle Hidden `
    -RedirectStandardOutput $LogFile `
    -RedirectStandardError $ErrorLogFile `
    -PassThru

Set-Content -LiteralPath $PidFile -Value $process.Id
Write-Output "started"
