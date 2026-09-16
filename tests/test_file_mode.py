"""--file-mode / QBCTL_FILE_MODE reaches BeginSession, and the default stays do-not-care."""

import pytest

from quickbooks import cli
from quickbooks.connection import FILE_MODE_DO_NOT_CARE, FILE_MODE_SINGLE_USER


def _parse(argv):
    return cli._build_parser().parse_args(argv)


def test_default_file_mode_is_do_not_care(monkeypatch):
    monkeypatch.delenv("QBCTL_FILE_MODE", raising=False)
    assert cli._connection(_parse(["company"])).file_mode == FILE_MODE_DO_NOT_CARE


def test_flag_sets_single_user(monkeypatch):
    monkeypatch.delenv("QBCTL_FILE_MODE", raising=False)
    assert cli._connection(_parse(["company", "--file-mode", "single-user"])).file_mode == FILE_MODE_SINGLE_USER


def test_environment_sets_single_user(monkeypatch):
    monkeypatch.setenv("QBCTL_FILE_MODE", "single-user")
    assert cli._connection(_parse(["company"])).file_mode == FILE_MODE_SINGLE_USER


def test_serve_passes_file_mode_to_the_api(monkeypatch):
    pytest.importorskip("fastapi")
    from quickbooks import api

    seen = {}
    monkeypatch.setattr(api, "run_server", lambda **kwargs: seen.update(kwargs))
    monkeypatch.delenv("QBCTL_FILE_MODE", raising=False)
    assert cli.main(["serve", "--host", "127.0.0.1", "--file-mode", "single-user"]) == 0
    assert seen["file_mode"] == FILE_MODE_SINGLE_USER


def test_api_runtime_gets_file_mode(monkeypatch):
    pytest.importorskip("fastapi")
    from quickbooks import api

    seen = {}

    class FakeRuntime:
        def __init__(self, **kwargs):
            seen.update(kwargs)

        def close(self):
            pass

    monkeypatch.setattr(api, "SerialQuickBooks", FakeRuntime)
    api.create_app(token="t", file_mode=FILE_MODE_SINGLE_USER)
    assert seen["file_mode"] == FILE_MODE_SINGLE_USER
