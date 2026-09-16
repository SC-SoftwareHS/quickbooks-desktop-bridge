#Requires -Version 5.1
<#
  Keep the qbctl API running for the logged-in QuickBooks user.

  Started at logon by the "qbctl API" scheduled task (scripts\install-server-task.ps1).
  It must run as the same Windows user, in the same session, as QuickBooks:
  the SDK attaches to that user's running QuickBooks, which SYSTEM cannot see.

  Reads the bearer token from C:\ProgramData\qbctl\token, logs to logs\, and
  restarts the server whenever it exits.
#>
param(
    [int]$Port = 8765,
    [ValidateSet("read-only", "add-only", "read-write")]
    [string]$Mode = "add-only",
    # Optional .QBW path. With it, qbctl can open the file itself when QuickBooks isn't open,
    # provided qbctl is allowed to "login automatically" (QuickBooks: Edit > Preferences >
    # Integrated Applications > Company Preferences > qbctl > Properties).
    [string]$CompanyFile = "",
    # single-user keeps an unattended open from starting QuickBooks' multi-user database server.
    [ValidateSet("single-user", "multi-user", "do-not-care")]
    [string]$FileMode = "do-not-care"
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$python = Join-Path $root ".venv\Scripts\python.exe"
$tokenFile = "C:\ProgramData\qbctl\token"
$logDir = Join-Path $root "logs"

New-Item -ItemType Directory -Force -Path $logDir | Out-Null
$env:QBCTL_TOKEN = (Get-Content -Raw -Path $tokenFile).Trim()
$env:PYTHONUNBUFFERED = "1"
if ($CompanyFile) { $env:QBCTL_COMPANY_FILE = $CompanyFile }
$env:QBCTL_FILE_MODE = $FileMode

$serveArgs = @("-m", "quickbooks", "serve", "--tailscale", "--port", "$Port")
if ($Mode -ne "read-only") { $serveArgs += "--allow-writes" }
if ($Mode -eq "read-write") { $serveArgs += "--allow-destructive" }

while ($true) {
    Get-ChildItem $logDir -Filter "serve-*.log" | Where-Object LastWriteTime -lt (Get-Date).AddDays(-14) | Remove-Item
    $stamp = Get-Date -Format "yyyyMMdd-HHmmss"
    $out = Join-Path $logDir "serve-$stamp.log"
    $err = Join-Path $logDir "serve-$stamp.err.log"
    $proc = Start-Process -FilePath $python -ArgumentList $serveArgs -WorkingDirectory $root `
        -RedirectStandardOutput $out -RedirectStandardError $err -NoNewWindow -Wait -PassThru
    Add-Content -Path $err -Value "$(Get-Date -Format s) qbctl exited with code $($proc.ExitCode); restarting in 15s"
    Start-Sleep -Seconds 15
}
