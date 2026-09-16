#Requires -Version 5.1
#Requires -RunAsAdministrator
<#
  One-time setup on the Windows VM. Run elevated:

    powershell -NoProfile -ExecutionPolicy Bypass -File scripts\install-server-task.ps1 -User QB-VM\<windows-user>

  - Creates C:\ProgramData\qbctl\token (readable only by -User and administrators) if it is missing.
  - Allows inbound TCP -Port from Tailscale addresses only.
  - Registers the "qbctl API" scheduled task: start-server.ps1 at -User's logon, in their session.
#>
param(
    [Parameter(Mandatory = $true)]
    [string]$User,
    [int]$Port = 8765,
    [ValidateSet("read-only", "add-only", "read-write")]
    [string]$Mode = "add-only",
    [string]$CompanyFile = "",
    [ValidateSet("single-user", "multi-user", "do-not-care")]
    [string]$FileMode = "do-not-care"
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$taskName = "qbctl API"

# Bearer token, pinned so clients survive restarts.
$tokenDir = "C:\ProgramData\qbctl"
$tokenFile = Join-Path $tokenDir "token"
New-Item -ItemType Directory -Force -Path $tokenDir | Out-Null
if (-not (Test-Path $tokenFile)) {
    $bytes = New-Object byte[] 32
    [Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($bytes)
    $token = [Convert]::ToBase64String($bytes).TrimEnd("=").Replace("+", "-").Replace("/", "_")
    [IO.File]::WriteAllText($tokenFile, $token)
}
# SYSTEM, Administrators: full; the QuickBooks user: read. No inherited access.
& icacls $tokenDir /inheritance:r /grant:r "*S-1-5-18:(OI)(CI)F" "*S-1-5-32-544:(OI)(CI)F" "${User}:(OI)(CI)RX" | Out-Null

# Unattended opens (-CompanyFile) make QuickBooks start its database service, which only administrators may
# start. Starting it at boot (as SYSTEM) means qbctl never waits on a UAC prompt.
if ($CompanyFile) {
    foreach ($svc in Get-Service -Name 'QuickBooksDB*' -ErrorAction SilentlyContinue) {
        Set-Service -Name $svc.Name -StartupType Automatic
        if ($svc.Status -ne 'Running') { Start-Service -Name $svc.Name }
        Write-Host "$($svc.Name): starts automatically"
    }
}

# Tailscale-only firewall opening.
$ruleName = "qbctl API (Tailscale)"
Get-NetFirewallRule -DisplayName $ruleName -ErrorAction SilentlyContinue | Remove-NetFirewallRule
New-NetFirewallRule -DisplayName $ruleName -Direction Inbound -Protocol TCP -LocalPort $Port `
    -RemoteAddress "100.64.0.0/10", "fd7a:115c:a1e0::/48" -Action Allow -Profile Any | Out-Null

# Logon task in the user's interactive session (no stored password; not elevated, like QuickBooks).
# Launched through "conhost --headless" so there is no console window at all: on Windows 11 a "hidden"
# PowerShell can still get a visible Windows Terminal window, and closing that window would stop the API.
$conhost = Join-Path $env:SystemRoot "System32\conhost.exe"
$powershell = Join-Path $env:SystemRoot "System32\WindowsPowerShell\v1.0\powershell.exe"
$startScript = Join-Path $PSScriptRoot "start-server.ps1"
$taskArgs = "--headless `"$powershell`" -NoProfile -ExecutionPolicy Bypass -File `"$startScript`" -Port $Port -Mode $Mode"
if ($CompanyFile) { $taskArgs += " -CompanyFile `"$CompanyFile`"" }
$taskArgs += " -FileMode $FileMode"
$action = New-ScheduledTaskAction -Execute $conhost -WorkingDirectory $root -Argument $taskArgs
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $User
$principal = New-ScheduledTaskPrincipal -UserId $User -LogonType Interactive -RunLevel Limited
$settings = New-ScheduledTaskSettingsSet -ExecutionTimeLimit ([TimeSpan]::Zero) -MultipleInstances IgnoreNew `
    -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable
Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger -Principal $principal `
    -Settings $settings -Force | Out-Null

Write-Host "Registered '$taskName' for $User ($Mode, port $Port)."
Write-Host "It starts at $User's next logon. To start it now in their session: Start-ScheduledTask -TaskName '$taskName'"
Write-Host "Token: $tokenFile"
