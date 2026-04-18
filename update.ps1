Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$RepoZipUrl = "https://github.com/EpicNori/Hashcat-Pwnagotchi-server/archive/refs/heads/main.zip"
$InstallRoot = "C:\ProgramData\HashcatWPAServer"
$CurrentRoot = Join-Path $InstallRoot "current"
$PreviousRoot = Join-Path $InstallRoot "previous"
$VenvRoot = Join-Path $InstallRoot "venv"
$LogsRoot = Join-Path $InstallRoot "logs"

function Test-IsAdministrator {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = New-Object Security.Principal.WindowsPrincipal($identity)
    return $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

function Write-Step([string]$Message) {
    Write-Output "[*] $Message"
}

function Get-SourceRoot {
    $tempRoot = Join-Path ([IO.Path]::GetTempPath()) ("hashcat-wpa-win-update-" + [guid]::NewGuid().ToString("N"))
    $zipPath = Join-Path $tempRoot "repo.zip"
    New-Item -ItemType Directory -Path $tempRoot -Force | Out-Null
    Invoke-WebRequest -Uri $RepoZipUrl -OutFile $zipPath -UseBasicParsing
    Expand-Archive -Path $zipPath -DestinationPath $tempRoot -Force
    $sourceRoot = Get-ChildItem -Path $tempRoot -Directory | Where-Object { $_.Name -like "Hashcat-Pwnagotchi-server-*" } | Select-Object -First 1
    if (-not $sourceRoot) {
        throw "Could not locate extracted repository contents."
    }
    return @{ Root = $sourceRoot.FullName; Temp = $tempRoot }
}

function Copy-RepoTree([string]$SourceRoot, [string]$DestinationRoot) {
    if (Test-Path $DestinationRoot) {
        Remove-Item -LiteralPath $DestinationRoot -Recurse -Force
    }
    New-Item -ItemType Directory -Path $DestinationRoot -Force | Out-Null
    & robocopy $SourceRoot $DestinationRoot /MIR /XD .git .github __pycache__ .venv venv | Out-Null
    if ($LASTEXITCODE -gt 7) {
        throw "robocopy failed with exit code $LASTEXITCODE"
    }
}

if (-not (Test-Path $CurrentRoot)) {
    throw "Windows installation not found at $CurrentRoot. Run install.ps1 first."
}
if (-not (Test-IsAdministrator)) {
    throw "Please run this update script from an elevated PowerShell session."
}

New-Item -ItemType Directory -Path $LogsRoot -Force | Out-Null
Write-Step "--- CRACKSERVER SAFE UPDATE INITIATED ---"
Write-Step "Data preservation: ACTIVE"
Write-Step "Stopping current Windows service process"
& powershell.exe -NoProfile -ExecutionPolicy Bypass -File (Join-Path $CurrentRoot "windows\crackserver.ps1") stop

$source = Get-SourceRoot
try {
    if (Test-Path $PreviousRoot) {
        Remove-Item -LiteralPath $PreviousRoot -Recurse -Force
    }
    if (Test-Path $CurrentRoot) {
        Move-Item -LiteralPath $CurrentRoot -Destination $PreviousRoot
    }

    Write-Step "Installing latest application files"
    Copy-RepoTree -SourceRoot $source.Root -DestinationRoot $CurrentRoot

    $venvPython = Join-Path $VenvRoot "Scripts\python.exe"
    Write-Step "Refreshing Python dependencies"
    & $venvPython -m pip install --upgrade pip wheel
    & $venvPython -m pip install -r (Join-Path $CurrentRoot "requirements.txt")

    Write-Step "Refreshing autostart task"
    & powershell.exe -NoProfile -ExecutionPolicy Bypass -File (Join-Path $CurrentRoot "windows\autostart_service.ps1") enable

    Write-Step "Restarting dashboard service"
    & powershell.exe -NoProfile -ExecutionPolicy Bypass -File (Join-Path $CurrentRoot "windows\run_server.ps1") -InstallRoot $InstallRoot

    if (Test-Path $PreviousRoot) {
        Remove-Item -LiteralPath $PreviousRoot -Recurse -Force
    }
}
catch {
    Write-Output "[!] Update failed: $($_.Exception.Message)"
    if (Test-Path $CurrentRoot) {
        Remove-Item -LiteralPath $CurrentRoot -Recurse -Force
    }
    if (Test-Path $PreviousRoot) {
        Move-Item -LiteralPath $PreviousRoot -Destination $CurrentRoot
        & powershell.exe -NoProfile -ExecutionPolicy Bypass -File (Join-Path $CurrentRoot "windows\run_server.ps1") -InstallRoot $InstallRoot
    }
    throw
}
finally {
    if ($source.Temp -and (Test-Path $source.Temp)) {
        Remove-Item -LiteralPath $source.Temp -Recurse -Force
    }
}

Write-Step "Update complete. All user data and settings have been preserved."
