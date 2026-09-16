#Requires -RunAsAdministrator
<#
  Enable OpenSSH Server so macOS can shell into this VM over Tailscale.

      powershell -NoProfile -ExecutionPolicy Bypass -File scripts\enable-openssh.ps1
#>
$ErrorActionPreference = "Stop"

Write-Host "Installing OpenSSH Server..."
Add-WindowsCapability -Online -Name OpenSSH.Server~~~~0.0.1.0 | Out-Null
Start-Service sshd
Set-Service -Name sshd -StartupType Automatic

$rule = Get-NetFirewallRule -Name sshd -ErrorAction SilentlyContinue
if (-not $rule) {
    New-NetFirewallRule -Name sshd -DisplayName "OpenSSH Server (sshd)" `
        -Enabled True -Direction Inbound -Protocol TCP -Action Allow -LocalPort 22 | Out-Null
}

Write-Host "sshd is running. From macOS:"
Write-Host "  ssh $env:USERNAME@desktop-douh1fp"
Write-Host "Tailscale IP:"
try { & tailscale ip -4 } catch { Write-Host "(tailscale not on PATH)" }
