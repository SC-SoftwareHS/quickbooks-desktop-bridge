"""QuickBooks Desktop SDK errors."""

from __future__ import annotations


class QBError(Exception):
    """Base error for the QuickBooks bridge."""


class QBNotOnWindows(QBError):
    """COM / QuickBooks Desktop SDK is only available on Windows."""


class QBConnectionError(QBError):
    """OpenConnection / BeginSession failed."""


class QBRefusedError(QBConnectionError):
    """The read-only / add-only guard refused to send a qbXML document."""


class QBXMLStatusError(QBError):
    """A qbXML response returned a non-success statusCode."""

    def __init__(
        self,
        status_code: int,
        status_severity: str,
        status_message: str,
        request_name: str = "",
    ) -> None:
        self.status_code = status_code
        self.status_severity = status_severity
        self.status_message = status_message
        self.request_name = request_name
        prefix = f"{request_name}: " if request_name else ""
        super().__init__(
            f"{prefix}statusCode={status_code} ({status_severity}): {status_message}"
        )


class QBTransportError(QBError):
    """The COM / PowerShell transport could not be created or used."""


# Common HRESULTs seen with QBXMLRP2. Extra keys are looked up when present.
COM_HRESULT_HINTS: dict[int, str] = {
    -2147221005: (
        "Invalid class string. QBXMLRP2.RequestProcessor is not registered. "
        "Install QuickBooks Desktop and/or the Desktop SDK (QBXMLRP2Installer), "
        "then retry from a 32-bit x86 process."
    ),
    -2147221164: (
        "Class not registered. QBXMLRP2 COM class is missing. "
        "Re-register QBXMLRP2.dll with 32-bit regsvr32."
    ),
    -2147024770: "The specified module could not be found (QBXMLRP2.dll missing or wrong arch).",
    -2147024891: "Access denied. Run as the same Windows user that owns the QuickBooks session, not as a service/SYSTEM account.",
}


def hint_for_hresult(hresult: int | None) -> str | None:
    if hresult is None:
        return None
    # Normalize to signed 32-bit.
    hr = hresult if hresult < 0 else hresult - (1 << 32) if hresult >= 0x80000000 else hresult
    if hr in COM_HRESULT_HINTS:
        return COM_HRESULT_HINTS[hr]
    unsigned = hr & 0xFFFFFFFF
    text = {
        0x80040417: "Company file is not open. Open the .QBW file in QuickBooks Desktop, or pass --company-file.",
        0x80040416: "Company file is not open. Open the .QBW file in QuickBooks Desktop, or pass --company-file.",
        0x80040408: "Could not start QuickBooks. Is QuickBooks Desktop installed and licensed on this machine?",
        0x8004040A: "This application is not authorized to access the company file. Approve it in the QuickBooks Application Certificate dialog.",
        0x8004041B: "This application is not authorized to access the company file.",
        0x80040400: "QuickBooks rejected the request. Check the Application Certificate and that the company file is open.",
        0x80040401: "A QuickBooks session could not be begun. Another application may hold an exclusive session.",
        0x80042584: "Internal QuickBooks error while opening the company file. Try closing and reopening QuickBooks.",
    }.get(unsigned)
    return text
