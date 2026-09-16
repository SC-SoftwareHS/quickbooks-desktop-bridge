#Requires -Version 5.1
<#
.SYNOPSIS
  Inspect this Windows machine for QuickBooks Desktop SDK / COM / Python readiness.

  Run in the Windows VM (elevated optional, not required for most checks):

      powershell -NoProfile -ExecutionPolicy Bypass -File diagnose.ps1

  Also try the 32-bit host, which is what QBXMLRP2 usually needs:

      & "$env:SystemRoot\SysWOW64\WindowsPowerShell\v1.0\powershell.exe" `
          -NoProfile -ExecutionPolicy Bypass -File diagnose.ps1
#>
[CmdletBinding()]
param()

$ErrorActionPreference = "Continue"
$results = New-Object System.Collections.Generic.List[object]

function Add-Check {
    param(
        [string]$Name,
        [ValidateSet("ok", "warn", "fail", "info")]
        [string]$Status,
        [string]$Detail
    )
    $results.Add([pscustomobject]@{
        Name   = $Name
        Status = $Status
        Detail = $Detail
    }) | Out-Null
    $color = switch ($Status) {
        "ok" { "Green" }
        "warn" { "Yellow" }
        "fail" { "Red" }
        default { "Gray" }
    }
    Write-Host ("[{0,-4}] {1}: {2}" -f $Status.ToUpper(), $Name, $Detail) -ForegroundColor $color
}

function Get-ProcessBitness {
    if ([Environment]::Is64BitProcess) { "64-bit" } else { "32-bit" }
}

function Test-ComProgId {
    param([string]$ProgId)
    try {
        $obj = New-Object -ComObject $ProgId
        if ($obj) {
            [System.Runtime.InteropServices.Marshal]::ReleaseComObject($obj) | Out-Null
        }
        return @{ Ok = $true; Error = $null }
    } catch {
        return @{ Ok = $false; Error = $_.Exception.Message }
    }
}

Write-Host ""
Write-Host "=== qbctl Windows environment diagnose ===" -ForegroundColor Cyan
Write-Host ("Time: {0:o}" -f (Get-Date))
Write-Host ""

# --- OS / process ---
$os = Get-CimInstance Win32_OperatingSystem
$arch = $env:PROCESSOR_ARCHITECTURE
$archWow = $env:PROCESSOR_ARCHITEW6432
$osArch = (Get-CimInstance Win32_Processor | Select-Object -First 1).Architecture
$osArchName = switch ($osArch) {
    0 { "x86" }
    9 { "x64" }
    12 { "ARM64" }
    default { "unknown($osArch)" }
}

Add-Check "OS" "info" ("{0} {1} (Build {2})" -f $os.Caption, $os.Version, $os.BuildNumber)
Add-Check "CPU architecture" "info" $osArchName
Add-Check "Process architecture" "info" ("{0} (PROCESSOR_ARCHITECTURE={1}; WOW6432={2})" -f (Get-ProcessBitness), $arch, $archWow)
Add-Check "User" "info" ("{0}\{1}  elevated={2}" -f $env:USERDOMAIN, $env:USERNAME, ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator))
Add-Check "Computer" "info" $env:COMPUTERNAME

if ($osArchName -eq "ARM64") {
    Add-Check "ARM Windows" "warn" "QuickBooks Desktop 2021 is an x86 app. COM must be called from a 32-bit x86 process (SysWOW64), not ARM64 Python."
}

# --- QuickBooks process ---
$qbProcs = Get-Process -ErrorAction SilentlyContinue | Where-Object {
    $_.ProcessName -match '^(QBW|QBW32|QBW64|QuickBooks)$' -or
    $_.MainWindowTitle -match 'QuickBooks'
}
if ($qbProcs) {
    $desc = ($qbProcs | ForEach-Object {
        "{0} pid={1} path={2}" -f $_.ProcessName, $_.Id, $_.Path
    }) -join "; "
    Add-Check "QuickBooks process" "ok" $desc
} else {
    Add-Check "QuickBooks process" "warn" "No QBW/QBW32 process. Open the company file in QuickBooks Desktop before connecting."
}

# --- Files on disk ---
$dllCandidates = @(
    "${env:ProgramFiles}\Common Files\Intuit\QuickBooks\QBXMLRP2.dll",
    "${env:ProgramFiles(x86)}\Common Files\Intuit\QuickBooks\QBXMLRP2.dll",
    "${env:CommonProgramFiles}\Intuit\QuickBooks\QBXMLRP2.dll",
    "${env:CommonProgramFiles(x86)}\Intuit\QuickBooks\QBXMLRP2.dll"
) | Select-Object -Unique

$foundDlls = @()
foreach ($p in $dllCandidates) {
    if ($p -and (Test-Path $p)) { $foundDlls += $p }
}
Get-ChildItem -Path "${env:ProgramFiles}\Common Files\Intuit","${env:ProgramFiles(x86)}\Common Files\Intuit" -Filter "QBXMLRP2.dll" -Recurse -ErrorAction SilentlyContinue |
    ForEach-Object { $foundDlls += $_.FullName }
$foundDlls = $foundDlls | Select-Object -Unique

if ($foundDlls) {
    Add-Check "QBXMLRP2.dll" "ok" ($foundDlls -join "; ")
} else {
    Add-Check "QBXMLRP2.dll" "fail" "Not found under Common Files\Intuit\QuickBooks. Install QuickBooks Desktop and/or the Desktop SDK (QBXMLRP2Installer)."
}

$sdkRoots = Get-ChildItem -Path "${env:ProgramFiles(x86)}\Intuit\IDN","${env:ProgramFiles}\Intuit\IDN" -Directory -ErrorAction SilentlyContinue
if ($sdkRoots) {
    Add-Check "QB SDK" "ok" (($sdkRoots | ForEach-Object { $_.FullName }) -join "; ")
} else {
    Add-Check "QB SDK" "warn" "No IDN\QBSDK* folder. The SDK installer is optional if QBXMLRP2.dll is already registered by QuickBooks itself."
}

$qbExe = Get-ChildItem -Path "${env:ProgramFiles(x86)}\Intuit","${env:ProgramFiles}\Intuit" -Include "QBW.exe","QBW32.exe" -Recurse -ErrorAction SilentlyContinue |
    Select-Object -First 5
if ($qbExe) {
    Add-Check "QuickBooks install" "ok" (($qbExe | ForEach-Object { $_.FullName }) -join "; ")
} else {
    Add-Check "QuickBooks install" "warn" "QBW.exe not found under Program Files\Intuit."
}

# --- COM registration ---
$progIds = @("QBXMLRP2.RequestProcessor", "QBXMLRP2.RequestProcessor.1", "QBXMLRP.RequestProcessor")
foreach ($id in $progIds) {
    $hkcr = "Registry::HKEY_CLASSES_ROOT\$id"
    if (Test-Path $hkcr) {
        Add-Check "ProgID $id" "ok" "Registered at HKCR\$id"
    } else {
        Add-Check "ProgID $id" "warn" "Not in HKCR"
    }
}

$com = Test-ComProgId "QBXMLRP2.RequestProcessor"
if ($com.Ok) {
    Add-Check "COM instantiate (this process)" "ok" "New-Object QBXMLRP2.RequestProcessor succeeded in this $(Get-ProcessBitness) PowerShell"
} else {
    Add-Check "COM instantiate (this process)" "fail" $com.Error
}

$syswow = Join-Path $env:SystemRoot "SysWOW64\WindowsPowerShell\v1.0\powershell.exe"
if (Test-Path $syswow) {
    if ([Environment]::Is64BitProcess) {
        $out = & $syswow -NoProfile -Command "try { `$o = New-Object -ComObject QBXMLRP2.RequestProcessor; 'OK' } catch { 'FAIL: ' + `$_.Exception.Message }"
        if ($out -match '^OK') {
            Add-Check "COM instantiate (32-bit PS)" "ok" "SysWOW64 PowerShell can create QBXMLRP2.RequestProcessor"
        } else {
            Add-Check "COM instantiate (32-bit PS)" "fail" ([string]$out)
        }
    } else {
        Add-Check "COM instantiate (32-bit PS)" "info" "Already running inside 32-bit PowerShell"
    }
} else {
    Add-Check "32-bit PowerShell" "warn" "SysWOW64 powershell.exe not found"
}

# --- Python ---
$pyCmds = @("py", "python", "python3")
$foundPy = $false
foreach ($cmd in $pyCmds) {
    $exe = Get-Command $cmd -ErrorAction SilentlyContinue
    if (-not $exe) { continue }
    try {
        $ver = & $cmd -c "import struct,platform,sys; print(sys.version.split()[0], platform.machine(), struct.calcsize('P')*8)"
        Add-Check "Python ($cmd)" "info" ("{0} -> {1}" -f $exe.Source, $ver)
        $foundPy = $true
        $win32 = & $cmd -c "import importlib.util,sys; print('yes' if importlib.util.find_spec('win32com') else 'no')"
        if ($win32 -match 'yes') {
            Add-Check "pywin32 ($cmd)" "ok" "win32com importable"
        } else {
            Add-Check "pywin32 ($cmd)" "fail" "Not installed. In this interpreter: pip install pywin32"
        }
    } catch {
        Add-Check "Python ($cmd)" "warn" $_.Exception.Message
    }
}
if (-not $foundPy) {
    Add-Check "Python" "fail" "No python/py on PATH. Install 32-bit x86 Python 3 from python.org (not ARM64) for QB 2021 COM."
}

try {
    $pyList = & py -0p 2>$null
    if ($pyList) {
        Add-Check "py launcher" "info" (($pyList | Out-String).Trim())
    }
} catch { }

# --- Tailscale ---
$ts = Get-Command tailscale -ErrorAction SilentlyContinue
if ($ts) {
    $tsStatus = & tailscale status --self 2>$null
    $tsIp = & tailscale ip -4 2>$null
    Add-Check "Tailscale" "ok" ("ip={0} {1}" -f $tsIp, (($tsStatus | Select-Object -First 1)))
} else {
    Add-Check "Tailscale" "warn" "tailscale.exe not on PATH"
}

# --- OpenSSH ---
$sshd = Get-Service sshd -ErrorAction SilentlyContinue
if ($sshd) {
    Add-Check "OpenSSH Server" "info" ("Status={0} StartType={1}" -f $sshd.Status, $sshd.StartType)
} else {
    Add-Check "OpenSSH Server" "info" "Not installed. Enable later with scripts/enable-openssh.ps1 if you want remote shells from macOS."
}

Write-Host ""
Write-Host "=== Next steps ===" -ForegroundColor Cyan
Write-Host "1. Open QuickBooks Desktop 2021 and the company file (leave it open)."
Write-Host "2. If COM instantiate failed, install QBXMLRP2 (SDK) and/or re-register:"
Write-Host '     regsvr32 "C:\Program Files (x86)\Common Files\Intuit\QuickBooks\QBXMLRP2.dll"'
Write-Host "3. Use 32-bit x86 Python + pywin32, or let qbctl fall back to 32-bit PowerShell COM."
Write-Host "4. First live connection will pop a QuickBooks 'Application Certificate' dialog."
Write-Host "   Approve qbctl. Prefer: Yes, whenever QuickBooks is running."
Write-Host ""
