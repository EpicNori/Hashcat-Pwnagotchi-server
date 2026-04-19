Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

function Test-NvidiaGpuPresent {
    try {
        $controllers = Get-CimInstance Win32_VideoController -ErrorAction Stop
        return @($controllers | Where-Object {
            ($_.Name -match "NVIDIA") -or ($_.AdapterCompatibility -match "NVIDIA")
        }).Count -gt 0
    } catch {
        return $false
    }
}

function Test-NvidiaDriverReady {
    if (Get-Command nvidia-smi.exe -ErrorAction SilentlyContinue) {
        return $true
    }

    return Test-Path "C:\Program Files\NVIDIA Corporation\NVSMI\nvidia-smi.exe"
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

if (-not (Test-NvidiaGpuPresent)) {
    Write-Output "No NVIDIA GPU was detected on this system."
    exit 0
}

if (Test-NvidiaDriverReady) {
    Write-Output "NVIDIA drivers already appear to be installed."
    exit 0
}

$wingetCmd = Get-WingetCommand
if (-not $wingetCmd) {
    throw "winget is unavailable, so automatic NVIDIA helper installation cannot continue."
}

& $wingetCmd install -e --id Nvidia.GeForceExperience --scope machine --accept-package-agreements --accept-source-agreements --silent --disable-interactivity
if ($LASTEXITCODE -ne 0) {
    throw "Automatic NVIDIA helper installation failed."
}

Write-Output "NVIDIA helper installation completed. A reboot or first-time NVIDIA setup may still be required before Hashcat can use the GPU."
