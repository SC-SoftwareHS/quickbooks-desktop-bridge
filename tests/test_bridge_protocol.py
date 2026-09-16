"""The PowerShell COM bridge protocol keeps paths intact even with spaces or pipes."""

import re

import pytest

from quickbooks.connection import PowerShellTransport, _escape
from quickbooks.errors import QBConnectionError


def _bridge_split(payload):
    r"""Mirror com_bridge.ps1: [regex]::Split($payload, '(?<!\\)\|') then Unescape-Field."""
    return [p.replace("\\|", "|").replace("\\\\", "\\") for p in re.split(r"(?<!\\)\|", payload)]


def test_fields_survive_spaces_and_pipes():
    fields = [r"C:\Users\Jane Doe\AppData\Local\Temp\qbctl-1\request.xml", r"C:\Books\Acme|2025.QBW"]
    assert _bridge_split("|".join(_escape(f) for f in fields)) == fields


def test_process_request_sends_pipe_delimited_paths(monkeypatch):
    sent = []
    transport = PowerShellTransport("powershell.exe")
    monkeypatch.setattr(transport, "_send", sent.append)
    with pytest.raises(QBConnectionError, match="did not write a response"):
        transport.process_request("<QBXML/>")
    assert sent[0].startswith("PROCESS ")
    request_path, response_path = _bridge_split(sent[0][len("PROCESS "):])
    assert request_path.endswith("request.xml")
    assert response_path.endswith("response.xml")
