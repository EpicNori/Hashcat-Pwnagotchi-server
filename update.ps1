Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$RepoZipUrl = "https://github.com/EpicNori/Hashcat-Pwnagotchi-server/archive/refs/heads/main.zip"
$InstallRoot = "C:\ProgramData\HashcatWPAServer"
$CurrentRoot = Join-Path $InstallRoot "current"
$PreviousRoot = Join-Path $InstallRoot "previous"
$VenvRoot = Join-Path $InstallRoot "venv"
$LogsRoot = Join-Path $InstallRoot "logs"
$ToolsRoot = Join-Path $InstallRoot "tools"

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

function Get-LatestGitHubRelease {
    param(
        [string]$Repository
    )

    $releaseUrl = "https://api.github.com/repos/$Repository/releases/latest"
    return Invoke-RestMethod -Uri $releaseUrl -Headers @{ "User-Agent" = "HashcatWPAServerUpdater" }
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

function Install-HashcatToolchain([string]$ToolsRootPath) {
    $hashcatBinRoot = Join-Path $ToolsRootPath "hashcat"
    $hcxBinRoot = Join-Path $ToolsRootPath "hcxtools"
    New-Item -ItemType Directory -Path $ToolsRootPath, $hashcatBinRoot, $hcxBinRoot -Force | Out-Null

    $hashcatRelease = Get-LatestGitHubRelease -Repository "hashcat/hashcat"
    $hashcatAsset = $hashcatRelease.assets | Where-Object {
        $_.name -match '\.(zip|7z)$'
    } | Select-Object -First 1

    if (-not $hashcatAsset) {
        throw "Could not find a Hashcat release archive to install hashcat.exe."
    }

    $hashcatArchive = Join-Path ([IO.Path]::GetTempPath()) $hashcatAsset.name
    Write-Step "Downloading Hashcat release asset $($hashcatAsset.name)"
    Invoke-WebRequest -Uri $hashcatAsset.browser_download_url -OutFile $hashcatArchive -UseBasicParsing
    Expand-ArchiveCrossFormat -ArchivePath $hashcatArchive -DestinationPath $hashcatBinRoot
    Remove-Item -LiteralPath $hashcatArchive -Force -ErrorAction SilentlyContinue

    $hashcatExe = Get-ChildItem -Path $hashcatBinRoot -Recurse -Filter "hashcat.exe" -ErrorAction SilentlyContinue | Select-Object -First 1
    if (-not $hashcatExe) {
        throw "The downloaded Hashcat package did not contain hashcat.exe."
    }
    Ensure-MachinePathEntry -PathEntry $hashcatExe.Directory.FullName

    if (-not (Get-Command hashcat.exe -ErrorAction SilentlyContinue)) {
        throw "hashcat.exe could not be installed automatically during update."
    }

    $hcxtoolsRelease = Get-LatestGitHubRelease -Repository "ZerBea/hcxtools"
    $hcxtoolsAsset = $hcxtoolsRelease.assets | Where-Object {
        $_.name -match 'win|windows|mingw' -and $_.name -match '\.(zip|7z)$'
    } | Select-Object -First 1

    if (-not $hcxtoolsAsset) {
        throw "Could not find a Windows hcxtools release asset to install hcxpcapngtool.exe and hcxhashtool.exe."
    }

    $tempArchive = Join-Path ([IO.Path]::GetTempPath()) $hcxtoolsAsset.name
    Write-Step "Downloading hcxtools release asset $($hcxtoolsAsset.name)"
    Invoke-WebRequest -Uri $hcxtoolsAsset.browser_download_url -OutFile $tempArchive -UseBasicParsing
    Expand-ArchiveCrossFormat -ArchivePath $tempArchive -DestinationPath $hcxBinRoot
    Remove-Item -LiteralPath $tempArchive -Force -ErrorAction SilentlyContinue

    foreach ($toolName in @("hcxpcapngtool.exe", "hcxhashtool.exe")) {
        $toolPath = Get-ChildItem -Path $hcxBinRoot -Recurse -Filter $toolName -ErrorAction SilentlyContinue | Select-Object -First 1
        if (-not $toolPath) {
            throw "The downloaded hcxtools package did not contain $toolName."
        }
        Ensure-MachinePathEntry -PathEntry $toolPath.Directory.FullName
    }
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
    Install-HashcatToolchain -ToolsRootPath $ToolsRoot

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
