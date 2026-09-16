"""Inspect the local machine for QuickBooks Desktop SDK readiness."""

from __future__ import annotations

import os
import platform
import shutil
import struct
import subprocess
import sys
from pathlib import Path
from typing import Any

from .connection import (
    find_powershell_com_host,
    python_process_info,
    pywin32_available,
    try_create_request_processor,
)


def run_diagnose() -> dict[str, Any]:
    checks: list[dict[str, str]] = []

    def add(name: str, status: str, detail: str) -> None:
        checks.append({"name": name, "status": status, "detail": detail})

    info = python_process_info()
    add("python", "info", "{executable} {version} {machine} {pointer_bits}-bit".format(**info))
    add("platform", "info", f"{platform.platform()} ({platform.machine()})")

    if os.name != "nt":
        add("os", "warn", "Not Windows. COM inspection is skipped. Use --url against the Windows API.")
        return {"checks": checks, "windows": False}

    add("os", "info", f"Windows, process pointer_bits={info['pointer_bits']}")
    if platform.machine().lower() in ("arm64", "aarch64"):
        add(
            "arm",
            "warn",
            "This is ARM64 Windows. QuickBooks Desktop 2021 is x86. "
            "Use 32-bit x86 Python + pywin32, or the 32-bit PowerShell COM fallback.",
        )

    if pywin32_available():
        add("pywin32", "ok", "win32com importable")
    else:
        add("pywin32", "fail", "win32com is not importable. pip install pywin32")

    ok, msg = try_create_request_processor()
    add("com.pywin32", "ok" if ok else "fail", msg)

    ps = find_powershell_com_host()
    if ps:
        add("powershell", "info", ps)
        ps_ok, ps_msg = _powershell_com(ps)
        add("com.powershell", "ok" if ps_ok else "fail", ps_msg)
    else:
        add("powershell", "warn", "No powershell.exe found")

    dlls = _find_qbxmlrp2_dlls()
    if dlls:
        add("qbxmlrp2.dll", "ok", "; ".join(dlls))
    else:
        add(
            "qbxmlrp2.dll",
            "fail",
            "QBXMLRP2.dll not found. Install QuickBooks Desktop and/or QBXMLRP2Installer from the SDK.",
        )

    sdks = _find_sdk_roots()
    if sdks:
        add("sdk", "ok", "; ".join(sdks))
    else:
        add("sdk", "warn", "No Intuit\\IDN\\QBSDK* folder. Optional if QBXMLRP2.dll is already registered.")

    procs = _quickbooks_processes()
    if procs:
        add("quickbooks.process", "ok", "; ".join(procs))
    else:
        add(
            "quickbooks.process",
            "warn",
            "QuickBooks does not appear to be running. Open the company file before BeginSession.",
        )

    ts = shutil.which("tailscale")
    if ts:
        ip = _run([ts, "ip", "-4"])
        add("tailscale", "ok", f"{ts} ip={ip}")
    else:
        add("tailscale", "warn", "tailscale.exe not on PATH")

    return {
        "checks": checks,
        "windows": True,
        "python": info,
        "ready_for_session": any(
            c["name"] in ("com.pywin32", "com.powershell") and c["status"] == "ok" for c in checks
        ),
    }


def _powershell_com(powershell: str) -> tuple[bool, str]:
    script = (
        "try { $o = New-Object -ComObject QBXMLRP2.RequestProcessor; "
        "'OK' } catch { 'FAIL: ' + $_.Exception.Message }"
    )
    try:
        completed = subprocess.run(
            [powershell, "-NoProfile", "-NonInteractive", "-Command", script],
            capture_output=True,
            text=True,
            timeout=30,
        )
    except Exception as exc:
        return False, str(exc)
    out = (completed.stdout or "").strip() or (completed.stderr or "").strip()
    if out.startswith("OK"):
        return True, f"{powershell}: {out}"
    return False, out or f"exit {completed.returncode}"


def _find_qbxmlrp2_dlls() -> list[str]:
    roots = [
        os.environ.get("CommonProgramFiles(x86)"),
        os.environ.get("CommonProgramFiles"),
        os.environ.get("ProgramFiles(x86)"),
        os.environ.get("ProgramFiles"),
    ]
    found: list[str] = []
    for root in roots:
        if not root:
            continue
        candidate = Path(root) / "Intuit" / "QuickBooks" / "QBXMLRP2.dll"
        if candidate.is_file():
            found.append(str(candidate))
        nested = Path(root) / "Common Files" / "Intuit" / "QuickBooks" / "QBXMLRP2.dll"
        if nested.is_file():
            found.append(str(nested))
    return sorted(set(found))


def _find_sdk_roots() -> list[str]:
    found: list[str] = []
    for key in ("ProgramFiles(x86)", "ProgramFiles"):
        root = os.environ.get(key)
        if not root:
            continue
        idn = Path(root) / "Intuit" / "IDN"
        if not idn.is_dir():
            continue
        for child in idn.iterdir():
            if child.is_dir() and child.name.upper().startswith("QBSDK"):
                found.append(str(child))
    return found


def _quickbooks_processes() -> list[str]:
    if os.name != "nt":
        return []
    try:
        import subprocess as sp

        completed = sp.run(
            ["tasklist", "/FO", "CSV", "/NH"],
            capture_output=True,
            text=True,
            timeout=15,
        )
    except Exception:
        return []
    names = ("QBW.EXE", "QBW32.EXE", "QBW64.EXE", "QUICKBOOKS.EXE")
    hits = []
    for line in completed.stdout.splitlines():
        upper = line.upper()
        if any(name in upper for name in names):
            hits.append(line.strip().strip('"'))
    return hits[:8]


def _run(cmd: list[str]) -> str:
    try:
        completed = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        return (completed.stdout or completed.stderr or "").strip()
    except Exception as exc:
        return str(exc)


def format_diagnose(result: dict[str, Any]) -> str:
    lines = ["qbctl diagnose", f"python {sys.executable}", ""]
    for check in result.get("checks", []):
        lines.append(f"[{check['status'].upper():<4}] {check['name']}: {check['detail']}")
    if result.get("ready_for_session"):
        lines += ["", "COM looks instantiable. Next: open QuickBooks and run: qbctl host"]
    else:
        lines += [
            "",
            "COM is not ready. See scripts/diagnose.ps1 and README.md.",
        ]
    return "\n".join(lines)
