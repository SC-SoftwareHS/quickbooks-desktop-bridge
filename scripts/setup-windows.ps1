#Requires -Version 5.1
<#
  Bootstrap the Windows VM for qbctl.

  powershell -NoProfile -ExecutionPolicy Bypass -File scripts\setup-windows.ps1
#>
[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
Set-Location $root

Write-Host "=== qbctl Windows setup ===" -ForegroundColor Cyan
Write-Host "Repo: $root"
Write-Host ""

& "$PSScriptRoot\diagnose.ps1"

Write-Host ""
Write-Host "Install 64-bit Python 3.13 (all users) from https://www.python.org/downloads/windows/, then:"
Write-Host '    & "C:\Program Files\Python313\python.exe" -m venv .venv'
Write-Host '    .venv\Scripts\python -m pip install -e ".[api]"'
Write-Host ""
Write-Host "QBXMLRP2 is a 32-bit COM class; 64-bit Python reaches it through the 32-bit PowerShell bridge."
Write-Host "Open QuickBooks Desktop with the company file, then:"
Write-Host "    .venv\Scripts\python -m quickbooks diagnose"
Write-Host "    .venv\Scripts\python -m quickbooks company"
Write-Host "Approve the QuickBooks Application Certificate dialog for app name 'qbctl'."
Write-Host ""
Write-Host "To run the API at logon, in an elevated PowerShell:"
Write-Host "    powershell -NoProfile -ExecutionPolicy Bypass -File scripts\install-server-task.ps1 -User $env:COMPUTERNAME\<user>"
