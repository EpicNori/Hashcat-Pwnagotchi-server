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
$script:NvidiaDriverStatus = "not-needed"

function Test-IsAdministrator {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = New-Object Security.Principal.WindowsPrincipal($identity)
    return $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

function Write-Step([string]$Message) {
    Write-Output "[*] $Message"
}

function Test-NvidiaGpuPresent {
    try {
        $controllers = Get-CimInstance Win32_VideoController -ErrorAction SilentlyContinue
        if (-not $controllers) {
            Write-Step "No video controllers detected via WMI."
            return $false
        }
        $nvidiaGpus = @($controllers | Where-Object {
            ($_.Name -match "NVIDIA|GeForce") -or ($_.AdapterCompatibility -match "NVIDIA")
        })
        $nvidiaCount = $nvidiaGpus.Count
        if ($nvidiaCount -gt 0) {
            Write-Step "Detected $nvidiaCount NVIDIA GPU(s): $($nvidiaGpus.Name -join ', ')"
            return $true
        }
        Write-Step "No NVIDIA GPU detected."
        $otherGpus = @($controllers | Where-Object { $_.Name -notmatch "NVIDIA|GeForce" })
        if ($otherGpus.Count -gt 0) {
            Write-Step "Other GPU(s) found: $($otherGpus.Name -join ', ')"
            Write-Step "Note: Hashcat on Windows requires NVIDIA GPU with CUDA support for optimal performance."
        }
        return $false
    } catch {
        Write-Step "Warning: Could not query video controllers - $_"
        return $false
    }
}

function Test-NvidiaDriverReady {
    if (Get-Command nvidia-smi.exe -ErrorAction SilentlyContinue) {
        return $true
    }

    $defaultNvsmPath = "C:\Program Files\NVIDIA Corporation\NVSMI\nvidia-smi.exe"
    return Test-Path $defaultNvsmPath
}

function Get-WingetCommand {
    $winget = Get-Command winget.exe -ErrorAction SilentlyContinue
    if ($winget) {
        return $winget.Source
    }

    try {
        Add-AppxPackage -RegisterByFamilyName -MainPackage Microsoft.DesktopAppInstaller_8wekyb3d8bbwe -ErrorAction Stop
    } catch {
    }

    $winget = Get-Command winget.exe -ErrorAction SilentlyContinue
    if ($winget) {
        return $winget.Source
    }

    return $null
}

function Ensure-NvidiaDriverSupport {
    if (-not (Test-NvidiaGpuPresent)) {
        Write-Step "Skipping NVIDIA driver installation (no NVIDIA GPU detected)."
        return
    }

    if (Test-NvidiaDriverReady) {
        $script:NvidiaDriverStatus = "already-installed"
        Write-Step "NVIDIA GPU runtime already appears to be available."
        return
    }

    $wingetCmd = Get-WingetCommand
    if (-not $wingetCmd) {
        $script:NvidiaDriverStatus = "manual-required"
        Write-Step "NVIDIA GPU detected, but winget is unavailable for automatic driver helper installation."
        return
    }

    Write-Step "NVIDIA GPU detected. Attempting to install NVIDIA GeForce Experience so drivers can be installed automatically"
    try {
        & $wingetCmd install -e --id Nvidia.GeForceExperience --scope machine --accept-package-agreements --accept-source-agreements --silent --disable-interactivity
        if ($LASTEXITCODE -eq 0) {
            $script:NvidiaDriverStatus = "installed"
            return
        }
    } catch {
    }

    $script:NvidiaDriverStatus = "manual-required"
    Write-Step "Automatic NVIDIA helper installation did not complete successfully."
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

function Try-InstallWSL {
    if (-not (Get-Command wsl.exe -ErrorAction SilentlyContinue)) {
        return $false
    }

    try {
        & wsl.exe -l -v | Out-Null
        return $true
    } catch {
    }

    try {
        Write-Step "Attempting to install WSL Ubuntu for Linux hcxtools support"
        & wsl.exe --install -d Ubuntu --no-launch
        return $true
    } catch {
        return $false
    }
}

function Install-HashcatToolchain() {
    Ensure-NvidiaDriverSupport
    Require-ToolInPath -ToolName "hashcat.exe" -BundledSubdir "hashcat" -MissingMessage "hashcat.exe is required. Bundle it under windows\\tools\\hashcat or install it system-wide before running the updater."
    $hcxBundled = Copy-BundledToolDirectory -SourceDir (Join-Path $BundledToolsRoot "hcxtools") -DestinationDir (Join-Path $ToolsRoot "hcxtools")
    if ($hcxBundled) {
        Ensure-MachinePathEntry -PathEntry (Join-Path $ToolsRoot "hcxtools")
    }
    if (-not (Get-Command hcxpcapngtool.exe -ErrorAction SilentlyContinue)) {
        Try-InstallWSL | Out-Null
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
switch ($script:NvidiaDriverStatus) {
    "installed" {
        Write-Step "NVIDIA GPU detected. GeForce Experience was installed automatically so NVIDIA drivers can be provisioned. A reboot or first-time NVIDIA setup may still be required before Hashcat can use the GPU."
    }
    "already-installed" {
        Write-Step "NVIDIA GPU and drivers detected. Hashcat should be able to use GPU acceleration."
    }
    "manual-required" {
        Write-Step "NVIDIA GPU detected, but automatic NVIDIA driver/helper installation did not complete. Install the NVIDIA driver manually before expecting GPU cracking to work."
    }
    "not-needed" {
        Write-Step "No NVIDIA GPU detected. Hashcat will run in CPU-only mode. For GPU acceleration, install an NVIDIA GPU with CUDA-capable drivers."
    }
}
