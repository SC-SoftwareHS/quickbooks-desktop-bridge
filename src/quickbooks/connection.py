"""QuickBooks Desktop COM transport.

Lifecycle:

    OpenConnection2 -> BeginSession -> ProcessRequest* -> EndSession -> CloseConnection

Sessions and connections are closed in ``finally`` even if a request fails.

Two transports:

1. pywin32 ``QBXMLRP2.RequestProcessor`` (preferred when the Python process
   can instantiate the COM class — typically 32-bit x86 for QB 2021).
2. A 32-bit PowerShell helper that hosts the same COM object. QBXMLRP2 is a
   32-bit class, so this is the path for any 64-bit Python (x64 or ARM64).
"""

from __future__ import annotations

import os
import queue
import subprocess
import sys
import tempfile
import threading
from pathlib import Path
from typing import Protocol

from .errors import (
    QBConnectionError,
    QBNotOnWindows,
    QBRefusedError,
    QBTransportError,
    hint_for_hresult,
)
from .qbxml import is_additive_qbxml, is_read_only_qbxml

APP_NAME_DEFAULT = "qbctl"
CONNECTION_TYPE_LOCAL_QBD = 1
FILE_MODE_SINGLE_USER = 0
FILE_MODE_MULTI_USER = 1
FILE_MODE_DO_NOT_CARE = 2
# BeginSession file modes by CLI name (--file-mode / QBCTL_FILE_MODE).
FILE_MODES = {
    "single-user": FILE_MODE_SINGLE_USER,
    "multi-user": FILE_MODE_MULTI_USER,
    "do-not-care": FILE_MODE_DO_NOT_CARE,
}

_BRIDGE_PS1 = Path(__file__).resolve().parent / "com_bridge.ps1"


class Transport(Protocol):
    name: str

    def open(self, app_name: str, company_file: str, file_mode: int, connection_type: int) -> None: ...
    def process_request(self, qbxml: str) -> str: ...
    def close(self) -> None: ...


def _com_error_parts(exc: BaseException) -> tuple[int | None, str]:
    args = getattr(exc, "args", ())
    hresult = None
    if args:
        try:
            hresult = int(args[0])
        except (TypeError, ValueError):
            hresult = None
    message = str(exc)
    hint = hint_for_hresult(hresult)
    if hint:
        message = f"{message}\n{hint}"
    return hresult, message


class PyWin32Transport:
    """In-process COM via pywin32."""

    name = "pywin32"

    def __init__(self) -> None:
        self._rp = None
        self._ticket = None
        self._opened = False
        self._session = False

    def open(self, app_name: str, company_file: str, file_mode: int, connection_type: int) -> None:
        try:
            import pythoncom  # type: ignore
            import win32com.client  # type: ignore
        except ImportError as exc:
            raise QBTransportError("pywin32 is not installed. pip install pywin32") from exc

        pythoncom.CoInitialize()
        try:
            self._rp = win32com.client.Dispatch("QBXMLRP2.RequestProcessor")
        except Exception as exc:
            _, message = _com_error_parts(exc)
            try:
                pythoncom.CoUninitialize()
            except Exception:
                pass
            raise QBTransportError(f"Could not create QBXMLRP2.RequestProcessor: {message}") from exc

        try:
            try:
                self._rp.OpenConnection2("", app_name, connection_type)
            except Exception:
                self._rp.OpenConnection("", app_name)
            self._opened = True
            self._ticket = self._rp.BeginSession(company_file, file_mode)
            self._session = True
        except Exception as exc:
            _, message = _com_error_parts(exc)
            self.close()
            raise QBConnectionError(message) from exc

    def process_request(self, qbxml: str) -> str:
        if not self._session or self._rp is None or self._ticket is None:
            raise QBConnectionError("No open QuickBooks session")
        try:
            return self._rp.ProcessRequest(self._ticket, qbxml)
        except Exception as exc:
            _, message = _com_error_parts(exc)
            raise QBConnectionError(f"ProcessRequest failed: {message}") from exc

    def close(self) -> None:
        try:
            if self._session and self._rp is not None and self._ticket is not None:
                try:
                    self._rp.EndSession(self._ticket)
                except Exception:
                    pass
        finally:
            self._session = False
            self._ticket = None
            try:
                if self._opened and self._rp is not None:
                    try:
                        self._rp.CloseConnection()
                    except Exception:
                        pass
            finally:
                self._opened = False
                self._rp = None
                try:
                    import pythoncom  # type: ignore

                    pythoncom.CoUninitialize()
                except Exception:
                    pass


class PowerShellTransport:
    """Out-of-process COM via 32-bit PowerShell (works on ARM64 Windows)."""

    name = "powershell-com"

    def __init__(self, powershell: str) -> None:
        self._powershell = powershell
        self._proc: subprocess.Popen[str] | None = None
        self._lock = threading.Lock()

    def open(self, app_name: str, company_file: str, file_mode: int, connection_type: int) -> None:
        if not _BRIDGE_PS1.is_file():
            raise QBTransportError(f"Missing COM bridge script: {_BRIDGE_PS1}")
        creationflags = 0
        if os.name == "nt":
            creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        self._proc = subprocess.Popen(
            [
                self._powershell,
                "-NoProfile",
                "-NonInteractive",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(_BRIDGE_PS1),
            ],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            creationflags=creationflags,
        )
        payload = "|".join(
            [
                _escape(app_name),
                _escape(company_file),
                str(file_mode),
                str(connection_type),
            ]
        )
        self._send(f"OPEN {payload}")

    def process_request(self, qbxml: str) -> str:
        with tempfile.TemporaryDirectory(prefix="qbctl-") as tmp:
            req = Path(tmp) / "request.xml"
            res = Path(tmp) / "response.xml"
            req.write_text(qbxml, encoding="utf-8")
            # |-separated and escaped like OPEN, so temp paths with spaces survive.
            self._send(f"PROCESS {_escape(str(req))}|{_escape(str(res))}")
            if not res.is_file():
                raise QBConnectionError("PowerShell COM bridge did not write a response file")
            return res.read_text(encoding="utf-8")

    def close(self) -> None:
        proc = self._proc
        if proc is None:
            return
        try:
            if proc.poll() is None and proc.stdin:
                try:
                    proc.stdin.write("CLOSE\n")
                    proc.stdin.flush()
                except Exception:
                    pass
            try:
                proc.communicate(timeout=15)
            except Exception:
                proc.kill()
        finally:
            self._proc = None

    def _send(self, line: str) -> None:
        if self._proc is None or self._proc.stdin is None or self._proc.stdout is None:
            raise QBConnectionError("PowerShell COM bridge is not running")
        with self._lock:
            if self._proc.poll() is not None:
                err = ""
                if self._proc.stderr:
                    err = self._proc.stderr.read()
                raise QBConnectionError(f"PowerShell COM bridge exited: {err.strip()}")
            self._proc.stdin.write(line + "\n")
            self._proc.stdin.flush()
            reply = self._proc.stdout.readline()
            if not reply:
                err = ""
                if self._proc.stderr:
                    err = self._proc.stderr.read()
                raise QBConnectionError(f"PowerShell COM bridge closed stdout: {err.strip()}")
            reply = reply.rstrip("\n")
            if reply.startswith("ERR "):
                raise QBConnectionError(reply[4:])
            if not reply.startswith("OK"):
                raise QBConnectionError(f"Unexpected COM bridge reply: {reply}")


def _escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace("|", "\\|")


def find_powershell_com_host() -> str | None:
    """Prefer 32-bit PowerShell so x86 QBXMLRP2 can be instantiated on ARM64."""
    windir = os.environ.get("SystemRoot") or os.environ.get("WINDIR") or r"C:\Windows"
    candidates = [
        str(Path(windir) / "SysWOW64" / "WindowsPowerShell" / "v1.0" / "powershell.exe"),
        str(Path(windir) / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe"),
    ]
    for path in candidates:
        if Path(path).is_file():
            return path
    return None


def pywin32_available() -> bool:
    try:
        import win32com.client  # noqa: F401
        return True
    except Exception:
        return False


def try_create_request_processor() -> tuple[bool, str]:
    """Attempt in-process COM instantiation. Does not open a QB session."""
    if os.name != "nt":
        return False, "Not Windows"
    try:
        import pythoncom  # type: ignore
        import win32com.client  # type: ignore
    except ImportError as exc:
        return False, f"pywin32 not importable: {exc}"
    pythoncom.CoInitialize()
    try:
        win32com.client.Dispatch("QBXMLRP2.RequestProcessor")
        return True, "QBXMLRP2.RequestProcessor created via pywin32"
    except Exception as exc:
        _, message = _com_error_parts(exc)
        return False, message
    finally:
        try:
            pythoncom.CoUninitialize()
        except Exception:
            pass


def create_transport() -> Transport:
    if os.name != "nt":
        raise QBNotOnWindows(
            "QuickBooks Desktop SDK COM is only available on Windows. "
            "Run qbctl on the Windows VM, or point this CLI at the Windows API with --url."
        )
    py_ok, py_msg = try_create_request_processor()
    if py_ok:
        return PyWin32Transport()
    ps = find_powershell_com_host()
    if ps:
        return PowerShellTransport(ps)
    raise QBTransportError(
        "Cannot talk to QBXMLRP2.RequestProcessor.\n"
        f"pywin32: {py_msg}\n"
        "No usable PowerShell COM host found. Install 32-bit x86 Python + pywin32, "
        "or register QBXMLRP2.dll."
    )


class QuickBooksConnection:
    """Context manager around one QuickBooks SDK session."""

    def __init__(
        self,
        app_name: str = APP_NAME_DEFAULT,
        company_file: str = "",
        file_mode: int = FILE_MODE_DO_NOT_CARE,
        connection_type: int = CONNECTION_TYPE_LOCAL_QBD,
        transport: Transport | None = None,
        read_only: bool = True,
        allow_destructive: bool = False,
    ) -> None:
        self.app_name = app_name
        self.company_file = company_file
        self.file_mode = file_mode
        self.connection_type = connection_type
        self.read_only = read_only
        self.allow_destructive = allow_destructive
        self._transport = transport
        self._entered = False

    @property
    def transport_name(self) -> str:
        if self._transport is None:
            return "unopened"
        return self._transport.name

    def __enter__(self) -> "QuickBooksConnection":
        if self._transport is None:
            self._transport = create_transport()
        self._transport.open(self.app_name, self.company_file, self.file_mode, self.connection_type)
        self._entered = True
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    def process(self, qbxml: str) -> str:
        if not self._entered or self._transport is None:
            raise QBConnectionError("Connection is not open")
        if self.read_only and not is_read_only_qbxml(qbxml):
            raise QBRefusedError("Refusing to send a non-query qbXML document (read-only mode)")
        if not self.read_only and not self.allow_destructive and not is_additive_qbxml(qbxml):
            raise QBRefusedError(
                "Refusing to send mod/del/void qbXML (add-only mode; use --allow-destructive)"
            )
        return self._transport.process_request(qbxml)

    def close(self) -> None:
        if self._transport is not None:
            try:
                self._transport.close()
            finally:
                self._entered = False


SESSION_OPEN_TIMEOUT = 120  # BeginSession blocks while QuickBooks shows its Application Certificate dialog


class _SessionWorker:
    """One QuickBooks session, owned by one thread (COM objects are thread-affine)."""

    def __init__(self, conn_kwargs: dict) -> None:
        self.ready = threading.Event()
        self.start_error: BaseException | None = None
        self._jobs: queue.Queue = queue.Queue()
        self.thread = threading.Thread(target=self._run, args=(conn_kwargs,), name="qbctl-com", daemon=True)
        self.thread.start()

    def run(self, qbxml: str) -> str:
        reply: queue.Queue = queue.Queue(maxsize=1)
        self._jobs.put((qbxml, reply))
        kind, payload = reply.get()
        if kind == "err":
            raise payload
        return payload

    def stop(self, timeout: float = 30) -> None:
        self._jobs.put(None)
        if threading.current_thread() is not self.thread:
            self.thread.join(timeout=timeout)

    def _run(self, conn_kwargs: dict) -> None:
        conn = QuickBooksConnection(**conn_kwargs)
        try:
            conn.__enter__()
        except Exception as exc:
            self.start_error = exc
            conn.close()
            self.ready.set()
            return
        self.ready.set()
        try:
            while True:
                job = self._jobs.get()
                if job is None:
                    break
                qbxml, reply = job
                try:
                    reply.put(("ok", conn.process(qbxml)))
                except Exception as exc:
                    reply.put(("err", exc))
        finally:
            conn.close()


class SerialQuickBooks:
    """Run all COM work on a single thread (STA-safe for the HTTP API).

    Requests run one at a time on a long-lived session. When the session fails
    (QuickBooks restarted, company file closed, COM bridge died) it is discarded
    and the next request opens a fresh one. A read-only request is retried once
    on the fresh session; a write is not, because QuickBooks may already have
    applied it.
    """

    def __init__(self, **conn_kwargs) -> None:
        self._conn_kwargs = conn_kwargs
        self.company_file = conn_kwargs.get("company_file", "")
        self._closed = False
        self._request_lock = threading.Lock()
        self._worker: _SessionWorker | None = None

    def process(self, qbxml: str) -> str:
        with self._request_lock:
            worker = self._session()
            try:
                return worker.run(qbxml)
            except QBRefusedError:
                raise
            except QBConnectionError:
                self._discard(worker)
                if not is_read_only_qbxml(qbxml):
                    raise
            return self._session().run(qbxml)

    def close(self) -> None:
        self._closed = True
        worker, self._worker = self._worker, None
        if worker is not None:
            worker.stop()

    def _session(self) -> _SessionWorker:
        if self._closed:
            raise QBConnectionError("QuickBooks runtime is closed")
        if self._worker is None:
            self._worker = _SessionWorker(self._conn_kwargs)
        worker = self._worker
        if not worker.ready.wait(timeout=SESSION_OPEN_TIMEOUT):
            raise QBConnectionError(
                "Timed out opening a QuickBooks session. If QuickBooks is showing an "
                "Application Certificate dialog for qbctl, approve it and retry."
            )
        if worker.start_error is not None:
            self._worker = None
            raise worker.start_error
        return worker

    def _discard(self, worker: _SessionWorker) -> None:
        if self._worker is worker:
            self._worker = None
        worker.stop(timeout=15)


def python_process_info() -> dict[str, str | int]:
    import platform
    import struct

    return {
        "executable": sys.executable,
        "version": sys.version.split()[0],
        "platform": sys.platform,
        "machine": platform.machine(),
        "pointer_bits": struct.calcsize("P") * 8,
        "os_name": os.name,
    }
