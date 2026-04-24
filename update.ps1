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
        "-File", $PSCommandPath
    )
    $process = Start-Process -FilePath "powershell.exe" -Verb RunAs -ArgumentList $launchArgs -Wait -PassThru
    if ($process.ExitCode -ne 0) {
        try {
            $probe = Invoke-WebRequest -Uri "http://127.0.0.1:9111" -UseBasicParsing -TimeoutSec 5
            if ($probe.StatusCode -eq 200) {
                return $false
            }
        } catch {
        }
    }
    if ($process.ExitCode -ne 0) {
        throw "Elevated updater failed with exit code $($process.ExitCode)."
    }
    return $false
}

if (-not (Ensure-Administrator)) {
    return
}

$RepoZipUrl = "https://github.com/EpicNori/Hashcat-Pwnagotchi-server/archive/refs/heads/main.zip"
$InstallRoot = "C:\ProgramData\HashcatWPAServer"
$CurrentRoot = Join-Path $InstallRoot "current"
$PreviousRoot = Join-Path $InstallRoot "previous"
$VenvRoot = Join-Path $InstallRoot "venv"
$LogsRoot = Join-Path $InstallRoot "logs"
$ToolsRoot = Join-Path $InstallRoot "tools"
$BundledToolsRoot = Join-Path $CurrentRoot "windows\tools"
$ProgressFile = Join-Path $LogsRoot "app_update.progress"
$script:NvidiaDriverStatus = "not-needed"

function Test-IsAdministrator {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = New-Object Security.Principal.WindowsPrincipal($identity)
    return $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

function Write-Step([string]$Message) {
    Write-Output "[*] $Message"
}

function Write-ProgressState([string]$State, [int]$Percent, [string]$Message) {
    New-Item -ItemType Directory -Path $LogsRoot -Force | Out-Null
    Set-Content -LiteralPath $ProgressFile -Value "$State|$Percent|$Message"
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
    $nvidiaSmi = Get-Command nvidia-smi.exe -ErrorAction SilentlyContinue
    $nvidiaSmiPath = if ($nvidiaSmi) { $nvidiaSmi.Source } else { "C:\Program Files\NVIDIA Corporation\NVSMI\nvidia-smi.exe" }
    if (-not (Test-Path $nvidiaSmiPath)) {
        return $false
    }

    try {
        & $nvidiaSmiPath -L *> $null
        return $LASTEXITCODE -eq 0
    } catch {
        return $false
    }
}

function Ensure-NvidiaDriverSupport {
    if (-not (Test-NvidiaGpuPresent)) {
        Write-Step "Skipping NVIDIA driver installation (no NVIDIA GPU detected)."
        Write-ProgressState "not-applicable" 100 "No NVIDIA GPU detected"
        return
    }

    if (Test-NvidiaDriverReady) {
        $script:NvidiaDriverStatus = "already-installed"
        Write-Step "NVIDIA GPU runtime already appears to be available."
        Write-ProgressState "success" 100 "NVIDIA drivers are already installed"
        return
    }

    $helperScript = Join-Path $CurrentRoot "windows\install_nvidia_drivers.ps1"
    if (-not (Test-Path $helperScript)) {
        $script:NvidiaDriverStatus = "manual-required"
        Write-Step "NVIDIA GPU detected, but the NVIDIA driver helper script is missing."
        return
    }

    Write-Step "NVIDIA GPU detected. Attempting automatic driver installation and validation"
    Write-ProgressState "running" 15 "Checking NVIDIA driver support"
    try {
        & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $helperScript check
        if (Test-NvidiaDriverReady) {
            $script:NvidiaDriverStatus = "installed"
            Write-ProgressState "success" 100 "NVIDIA drivers are ready"
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
    Copy-Item -Path (Join-Path $SourceDir "*") -Destination $DestinationDir -Recurse -Force
    return $true
}

function Ensure-7ZipExe {
    $candidate = Get-Command 7z.exe -ErrorAction SilentlyContinue
    if ($candidate) {
        return $candidate.Source
    }

    $sevenZipDir = Join-Path $ToolsRoot "7zip"
    $sevenZipExe = Join-Path $sevenZipDir "7zr.exe"
    if (Test-Path $sevenZipExe) {
        return $sevenZipExe
    }

    New-Item -ItemType Directory -Path $sevenZipDir -Force | Out-Null
    $sevenZipUrl = "https://www.7-zip.org/a/7zr.exe"
    Write-Step "Downloading portable 7-Zip extractor for the official Hashcat archive"
    Write-ProgressState "running" 72 "Downloading the Hashcat extractor"
    Invoke-WebRequest -Uri $sevenZipUrl -OutFile $sevenZipExe -UseBasicParsing
    if (-not (Test-Path $sevenZipExe)) {
        throw "Could not download 7zr.exe."
    }
    return $sevenZipExe
}

function Install-HashcatRelease {
    $hashcatVersion = "7.1.2"
    $archiveName = "hashcat-$hashcatVersion.7z"
    $downloadUris = @(
        "https://hashcat.net/files/$archiveName",
        "https://github.com/hashcat/hashcat/releases/download/v$hashcatVersion/$archiveName"
    )
    $archivePath = Join-Path ([IO.Path]::GetTempPath()) ("hashcat-" + [guid]::NewGuid().ToString("N") + ".7z")
    $extractRoot = Join-Path ([IO.Path]::GetTempPath()) ("hashcat-extract-" + [guid]::NewGuid().ToString("N"))
    New-Item -ItemType Directory -Path $extractRoot -Force | Out-Null
    $sevenZipExe = Ensure-7ZipExe

    try {
        $downloaded = $false
        foreach ($uri in $downloadUris) {
            try {
                Write-Step "Downloading official Hashcat release from $uri"
                Write-ProgressState "running" 78 "Downloading the official Hashcat release"
                Invoke-WebRequest -Uri $uri -OutFile $archivePath -UseBasicParsing
                $downloaded = $true
                break
            } catch {
                Write-Step "Warning: download attempt failed for $uri"
            }
        }

        if (-not $downloaded) {
            throw "Could not download the Hashcat release archive."
        }

        & $sevenZipExe x "-o$extractRoot" -y $archivePath | Out-Null
        if ($LASTEXITCODE -ne 0) {
            throw "Failed to extract the Hashcat release archive."
        }
        Write-ProgressState "running" 86 "Extracting the Hashcat release"

        $sourceDir = Get-ChildItem -LiteralPath $extractRoot -Directory -ErrorAction SilentlyContinue |
            Where-Object { $_.Name -like "hashcat-*" } |
            Select-Object -First 1
        if (-not $sourceDir) {
            throw "Could not locate extracted Hashcat files."
        }

        $destinationRoot = Join-Path $ToolsRoot "hashcat"
        if (Test-Path $destinationRoot) {
            Remove-Item -LiteralPath $destinationRoot -Recurse -Force
        }
        New-Item -ItemType Directory -Path $destinationRoot -Force | Out-Null
        Copy-Item -Path (Join-Path $sourceDir.FullName "*") -Destination $destinationRoot -Recurse -Force
        Ensure-MachinePathEntry -PathEntry $destinationRoot
        Write-ProgressState "running" 92 "Installing the Hashcat binaries"
    } finally {
        if (Test-Path $archivePath) {
            Remove-Item -LiteralPath $archivePath -Force -ErrorAction SilentlyContinue
        }
        if (Test-Path $extractRoot) {
            Remove-Item -LiteralPath $extractRoot -Recurse -Force -ErrorAction SilentlyContinue
        }
    }
}

function Ensure-HashcatTool([string]$PythonExe) {
    if (Get-Command hashcat.exe -ErrorAction SilentlyContinue) {
        return $true
    }

    $bundledDir = Join-Path $BundledToolsRoot "hashcat"
    $installedDir = Join-Path $ToolsRoot "hashcat"
    $copied = Copy-BundledToolDirectory -SourceDir $bundledDir -DestinationDir $installedDir
    if ($copied) {
        Ensure-MachinePathEntry -PathEntry $installedDir
    } elseif (-not (Get-Command hashcat.exe -ErrorAction SilentlyContinue)) {
        try {
            Install-HashcatRelease
        } catch {
            Write-Step "Warning: automatic Hashcat download failed - $($_.Exception.Message)"
        }
    }

    if (-not (Get-Command hashcat.exe -ErrorAction SilentlyContinue)) {
        Write-Step "Warning: hashcat.exe was not found. Bundle it under windows\\tools\\hashcat or let the updater download the official release."
        return $false
    }

    return $true
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
    $venvPython = Join-Path $VenvRoot "Scripts\python.exe"
    $null = Ensure-HashcatTool -PythonExe $venvPython
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
    Write-ProgressState "running" 20 "Downloading the latest application source"
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
        Write-Step "Preserving existing runtime logs while refreshing application files"
    }
    Write-ProgressState "running" 35 "Refreshing application files"
    New-Item -ItemType Directory -Path $DestinationRoot -Force | Out-Null
    & robocopy $SourceRoot $DestinationRoot /MIR /XD .git .github __pycache__ .venv venv logs | Out-Null
    if ($LASTEXITCODE -gt 7) {
        throw "robocopy failed with exit code $LASTEXITCODE"
    }
}

function Test-PathUnlocked([string]$Path) {
    if (-not (Test-Path -LiteralPath $Path)) {
        return $true
    }

    try {
        $stream = [System.IO.File]::Open($Path, [System.IO.FileMode]::Open, [System.IO.FileAccess]::ReadWrite, [System.IO.FileShare]::None)
        $stream.Close()
        return $true
    } catch [System.IO.IOException] {
        return $false
    }
}

function Wait-DirectoryUnlocked([string]$Path, [int]$TimeoutSeconds = 20) {
    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    do {
        $blocked = $false
        if (Test-Path -LiteralPath $Path) {
            foreach ($file in Get-ChildItem -LiteralPath $Path -Recurse -File -Force -ErrorAction SilentlyContinue) {
                if (-not (Test-PathUnlocked -Path $file.FullName)) {
                    $blocked = $true
                    break
                }
            }
        }

        if (-not $blocked) {
            return $true
        }

        Start-Sleep -Milliseconds 250
    } while ((Get-Date) -lt $deadline)

    return $false
}

function Invoke-WithRetry([scriptblock]$Action, [string]$Description, [int]$Attempts = 12, [int]$DelayMilliseconds = 500) {
    $lastError = $null
    for ($attempt = 1; $attempt -le $Attempts; $attempt++) {
        try {
            & $Action
            return
        } catch [System.IO.IOException] {
            $lastError = $_
            if ($attempt -lt $Attempts) {
                Start-Sleep -Milliseconds $DelayMilliseconds
                continue
            }
            break
        }
    }

    if ($lastError) {
        throw "Timed out while trying to $Description : $($lastError.Exception.Message)"
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
Write-ProgressState "running" 10 "Stopping the current server"
Invoke-CheckedPowerShellFile -ScriptPath (Join-Path $CurrentRoot "windows\crackserver.ps1") -Arguments @("stop")

$source = Get-SourceRoot
try {
    if (Test-Path $CurrentRoot) {
        $currentLogs = Join-Path $CurrentRoot "logs"
        if (Test-Path $currentLogs) {
            if (-not (Wait-DirectoryUnlocked -Path $currentLogs -TimeoutSeconds 20)) {
                Write-Step "Warning: log files still appeared busy after waiting; continuing with retries."
            }
        }
    }

    if (Test-Path $PreviousRoot) {
        Invoke-WithRetry -Description "remove the previous installation snapshot" -Action {
            Remove-Item -LiteralPath $PreviousRoot -Recurse -Force
        }
    }
    if (Test-Path $CurrentRoot) {
        Invoke-WithRetry -Description "move the current installation aside" -Action {
            Move-Item -LiteralPath $CurrentRoot -Destination $PreviousRoot
        }
    }

    Write-Step "Installing latest application files"
    Write-ProgressState "running" 30 "Installing the updated application files"
    Copy-RepoTree -SourceRoot $source.Root -DestinationRoot $CurrentRoot

    $venvPython = Join-Path $VenvRoot "Scripts\python.exe"
    Write-Step "Refreshing Python dependencies"
    Write-ProgressState "running" 45 "Refreshing Python dependencies"
    & $venvPython -m pip install --upgrade pip wheel
    & $venvPython -m pip install -r (Join-Path $CurrentRoot "requirements.txt")

    Write-Step "Refreshing Windows cracking toolchain"
    Write-ProgressState "running" 65 "Downloading and installing Hashcat"
    Install-HashcatToolchain

    Write-Step "Refreshing autostart task"
    Write-ProgressState "running" 85 "Updating the autostart task"
    Invoke-CheckedPowerShellFile -ScriptPath (Join-Path $CurrentRoot "windows\autostart_service.ps1") -Arguments @("enable")

    Write-Step "Restarting dashboard service"
    Write-ProgressState "running" 95 "Restarting the server"
    Invoke-CheckedPowerShellFile -ScriptPath (Join-Path $CurrentRoot "windows\run_server.ps1") -Arguments @("-InstallRoot", $InstallRoot)

    if (Test-Path $PreviousRoot) {
        Invoke-WithRetry -Description "remove the previous installation snapshot" -Action {
            Remove-Item -LiteralPath $PreviousRoot -Recurse -Force
        }
    }
}
catch {
    Write-Output "[!] Update failed: $($_.Exception.Message)"
    if (Test-Path $CurrentRoot) {
        try {
            Invoke-WithRetry -Description "remove the failed current installation" -Action {
                Remove-Item -LiteralPath $CurrentRoot -Recurse -Force
            }
        } catch {
            Write-Step "Warning: could not fully remove the failed current installation."
        }
    }
    if (Test-Path $PreviousRoot) {
        Invoke-WithRetry -Description "restore the previous installation" -Action {
            Move-Item -LiteralPath $PreviousRoot -Destination $CurrentRoot
        }
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
Write-ProgressState "success" 100 "Application update completed successfully"
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
