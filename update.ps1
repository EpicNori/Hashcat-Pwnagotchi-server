Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$RepoZipUrl = "https://github.com/EpicNori/Hashcat-Pwnagotchi-server/archive/refs/heads/main.zip"
$InstallRoot = "C:\ProgramData\HashcatWPAServer"
$CurrentRoot = Join-Path $InstallRoot "current"
$PreviousRoot = Join-Path $InstallRoot "previous"
$VenvRoot = Join-Path $InstallRoot "venv"
$LogsRoot = Join-Path $InstallRoot "logs"
$ToolsRoot = Join-Path $InstallRoot "tools"
$BundledToolsRoot = Join-Path $CurrentRoot "windows\tools"

function Test-IsAdministrator {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = New-Object Security.Principal.WindowsPrincipal($identity)
    return $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

function Write-Step([string]$Message) {
    Write-Output "[*] $Message"
}

function Invoke-CheckedPowerShellFile([string]$ScriptPath, [string[]]$Arguments = @()) {
    & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $ScriptPath @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "PowerShell helper failed: $ScriptPath"
    }
}

function Ensure-MachinePathEntry([string]$PathEntry) {
    $machinePath = [Environment]::GetEnvironmentVariable("Path", "Machine")
    $parts = @()
    if ($machinePath) {
        $parts = $machinePath.Split(';', [System.StringSplitOptions]::RemoveEmptyEntries)
    }
    if ($parts -contains $PathEntry) {
        return
    }
    $newPath = if ($machinePath) { "$machinePath;$PathEntry" } else { $PathEntry }
    [Environment]::SetEnvironmentVariable("Path", $newPath, "Machine")
    $env:Path = "$env:Path;$PathEntry"
}

function Expand-ArchiveCrossFormat {
    param(
        [string]$ArchivePath,
        [string]$DestinationPath
    )

    if (Test-Path $DestinationPath) {
        Remove-Item -LiteralPath $DestinationPath -Recurse -Force
    }
    New-Item -ItemType Directory -Path $DestinationPath -Force | Out-Null

    try {
        & tar.exe -xf $ArchivePath -C $DestinationPath
        if ($LASTEXITCODE -eq 0) {
            return
        }
    } catch {
    }

    Expand-Archive -Path $ArchivePath -DestinationPath $DestinationPath -Force
}

function Copy-BundledToolDirectory([string]$SourceDir, [string]$DestinationDir) {
    if (-not (Test-Path $SourceDir)) {
        return $false
    }
    New-Item -ItemType Directory -Path $DestinationDir -Force | Out-Null
    Copy-Item -LiteralPath (Join-Path $SourceDir "*") -Destination $DestinationDir -Recurse -Force
    return $true
}

function Require-ToolInPath([string]$ToolName, [string]$BundledSubdir, [string]$MissingMessage) {
    if (Get-Command $ToolName -ErrorAction SilentlyContinue) {
        return
    }

    $bundledDir = Join-Path $BundledToolsRoot $BundledSubdir
    $installedDir = Join-Path $ToolsRoot $BundledSubdir
    $copied = Copy-BundledToolDirectory -SourceDir $bundledDir -DestinationDir $installedDir
    if ($copied) {
        Ensure-MachinePathEntry -PathEntry $installedDir
    }

    if (-not (Get-Command $ToolName -ErrorAction SilentlyContinue)) {
        throw $MissingMessage
    }
}

function Install-HashcatToolchain() {
    Require-ToolInPath -ToolName "hashcat.exe" -BundledSubdir "hashcat" -MissingMessage "hashcat.exe is required. Bundle it under windows\\tools\\hashcat or install it system-wide before running the updater."
    Require-ToolInPath -ToolName "hcxpcapngtool.exe" -BundledSubdir "hcxtools" -MissingMessage "hcxpcapngtool.exe is required. Bundle it under windows\\tools\\hcxtools or install it system-wide before running the updater."
    Require-ToolInPath -ToolName "hcxhashtool.exe" -BundledSubdir "hcxtools" -MissingMessage "hcxhashtool.exe is required. Bundle it under windows\\tools\\hcxtools or install it system-wide before running the updater."
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
New-Item -ItemType Directory -Path $ToolsRoot -Force | Out-Null
Write-Step "--- CRACKSERVER SAFE UPDATE INITIATED ---"
Write-Step "Data preservation: ACTIVE"
Write-Step "Stopping current Windows service process"
Invoke-CheckedPowerShellFile -ScriptPath (Join-Path $CurrentRoot "windows\crackserver.ps1") -Arguments @("stop")

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

    Write-Step "Refreshing Windows cracking toolchain"
    Install-HashcatToolchain

    Write-Step "Refreshing autostart task"
    Invoke-CheckedPowerShellFile -ScriptPath (Join-Path $CurrentRoot "windows\autostart_service.ps1") -Arguments @("enable")

    Write-Step "Restarting dashboard service"
    Invoke-CheckedPowerShellFile -ScriptPath (Join-Path $CurrentRoot "windows\run_server.ps1") -Arguments @("-InstallRoot", $InstallRoot)

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
        Invoke-CheckedPowerShellFile -ScriptPath (Join-Path $CurrentRoot "windows\run_server.ps1") -Arguments @("-InstallRoot", $InstallRoot)
    }
    throw
}
finally {
    if ($source.Temp -and (Test-Path $source.Temp)) {
        Remove-Item -LiteralPath $source.Temp -Recurse -Force
    }
}

Write-Step "Update complete. All user data and settings have been preserved."
