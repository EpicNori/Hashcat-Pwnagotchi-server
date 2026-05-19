[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$PublicHostname,
    [string]$InstallRoot = "C:\ProgramData\HashcatWPAServer"
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$token = [Console]::In.ReadToEnd().Trim()
if (-not $token) {
    throw "Cloudflare Tunnel token is required."
}

$ToolsRoot = Join-Path $InstallRoot "tools"
$CloudflaredRoot = Join-Path $ToolsRoot "cloudflared"
$CloudflaredExe = Join-Path $CloudflaredRoot "cloudflared.exe"

New-Item -ItemType Directory -Path $CloudflaredRoot -Force | Out-Null

if (-not (Test-Path -LiteralPath $CloudflaredExe)) {
    $url = "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-windows-amd64.exe"
    Invoke-WebRequest -Uri $url -OutFile $CloudflaredExe -UseBasicParsing
}

$existing = Get-Service -Name cloudflared -ErrorAction SilentlyContinue
if ($existing) {
    if ($existing.Status -ne "Stopped") {
        Stop-Service -Name cloudflared -Force -ErrorAction SilentlyContinue
    }
    & $CloudflaredExe service uninstall *> $null
}

& $CloudflaredExe service install $token
Start-Service -Name cloudflared

Write-Output "Cloudflare Tunnel connector is installed for https://$PublicHostname"
