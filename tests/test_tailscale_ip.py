"""serve --tailscale waits for Tailscale to come up instead of failing at logon."""

import shutil
import subprocess
import time
from types import SimpleNamespace

import pytest

from quickbooks import cli
from quickbooks.errors import QBError


def test_waits_until_tailscale_reports_an_address(monkeypatch):
    replies = [
        SimpleNamespace(returncode=1, stdout="", stderr="no current Tailscale IPs; state: Starting"),
        SimpleNamespace(returncode=0, stdout="100.64.0.11\n", stderr=""),
    ]
    monkeypatch.setattr(shutil, "which", lambda name: "tailscale")
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: replies.pop(0))
    monkeypatch.setattr(time, "sleep", lambda s: None)
    assert cli._tailscale_ipv4(wait_seconds=60) == "100.64.0.11"


def test_gives_up_after_the_deadline(monkeypatch):
    monkeypatch.setattr(shutil, "which", lambda name: "tailscale")
    monkeypatch.setattr(
        subprocess, "run", lambda *a, **k: SimpleNamespace(returncode=1, stdout="", stderr="Logged out.")
    )
    monkeypatch.setattr(time, "sleep", lambda s: None)
    with pytest.raises(QBError, match="Logged out"):
        cli._tailscale_ipv4(wait_seconds=0)
