param(
    [string]$InstallRoot = "C:\ProgramData\HashcatWPAServer"
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$CurrentRoot = Join-Path $InstallRoot "current"
$VenvPython = Join-Path $InstallRoot "venv\Scripts\python.exe"
$LogsRoot = Join-Path $InstallRoot "logs"
$PidFile = Join-Path $InstallRoot "server.pid"
$StdoutLog = Join-Path $LogsRoot "server_stdout.log"
$StderrLog = Join-Path $LogsRoot "server_stderr.log"

New-Item -ItemType Directory -Path $LogsRoot, (Join-Path $InstallRoot "data") -Force | Out-Null

if (-not (Test-Path $CurrentRoot)) {
    throw "Installed application root not found: $CurrentRoot"
}
if (-not (Test-Path $VenvPython)) {
    throw "Virtual environment Python not found: $VenvPython"
}

if (Test-Path $PidFile) {
    $existingPid = Get-Content -LiteralPath $PidFile -ErrorAction SilentlyContinue | Select-Object -First 1
    if (-not [string]::IsNullOrWhiteSpace($existingPid) -and (Get-Process -Id $existingPid.Trim() -ErrorAction SilentlyContinue)) {
        Write-Output "already-running"
        exit 0
    }
    Remove-Item -LiteralPath $PidFile -Force -ErrorAction SilentlyContinue
}

$env:HASHCAT_WPA_SERVER_HOME = Join-Path $InstallRoot "data"
$env:HASHCAT_WPA_INSTALL_ROOT = $InstallRoot
$env:PYTHONUNBUFFERED = "1"
if (-not $env:HASHCAT_ADMIN_USER) {
    $env:HASHCAT_ADMIN_USER = "admin"
}
if (-not $env:HASHCAT_ADMIN_PASSWORD) {
    $env:HASHCAT_ADMIN_PASSWORD = "changeme"
}

$process = Start-Process `
    -FilePath $VenvPython `
    -ArgumentList @("-m", "waitress", "--host=0.0.0.0", "--port=9111", "app:app") `
    -WorkingDirectory $CurrentRoot `
    -WindowStyle Hidden `
    -RedirectStandardOutput $StdoutLog `
    -RedirectStandardError $StderrLog `
    -PassThru

Set-Content -LiteralPath $PidFile -Value $process.Id
Start-Sleep -Seconds 3

if ($process.HasExited) {
    $stderr = if (Test-Path $StderrLog) { Get-Content -LiteralPath $StderrLog -Tail 20 | Out-String } else { "" }
    throw "The dashboard process exited immediately. $stderr"
}

Write-Output "started"
