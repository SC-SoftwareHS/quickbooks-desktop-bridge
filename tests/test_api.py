"""HTTP API wiring tests with a faked COM runtime (no QuickBooks needed)."""

import pytest

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient

from quickbooks import api as api_module

ACCOUNT_RS = """<?xml version="1.0"?>
<QBXML>
  <QBXMLMsgsRs>
    <AccountQueryRs statusCode="0" statusSeverity="Info" statusMessage="OK">
      <AccountRet>
        <ListID>A-1</ListID>
        <Name>Checking</Name>
        <FullName>Checking</FullName>
        <IsActive>true</IsActive>
        <AccountType>Bank</AccountType>
        <Balance>100.00</Balance>
      </AccountRet>
    </AccountQueryRs>
  </QBXMLMsgsRs>
</QBXML>
"""

WRITE_DOC = "<QBXML><QBXMLMsgsRq><CustomerAddRq/></QBXMLMsgsRq></QBXML>"
DESTRUCTIVE_DOC = "<QBXML><QBXMLMsgsRq><TxnDelRq/></QBXMLMsgsRq></QBXML>"
READ_DOC = "<QBXML><QBXMLMsgsRq><CompanyQueryRq/></QBXMLMsgsRq></QBXML>"


class FakeRuntime:
    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.requests = []

    def process(self, document):
        self.requests.append(document)
        return ACCOUNT_RS

    def close(self):
        pass


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(api_module, "SerialQuickBooks", FakeRuntime)
    app = api_module.create_app(token="secret")
    with TestClient(app) as tc:
        yield tc


AUTH = {"Authorization": "Bearer secret"}


def test_health_reports_mode(client):
    assert client.get("/health").json() == {"status": "ok", "mode": "read-only"}


def test_auth_required(client):
    assert client.get("/accounts").status_code == 401


def test_accounts_roundtrip(client):
    resp = client.get("/accounts", headers=AUTH)
    assert resp.status_code == 200
    accounts = resp.json()
    assert accounts[0]["full_name"] == "Checking"
    assert accounts[0]["balance"] == "100.00"


def test_query_accepts_raw_body(client):
    resp = client.post("/query", content=READ_DOC, headers=AUTH)
    assert resp.status_code == 200
    assert "AccountQueryRs" in resp.text


def test_query_rejects_writes_by_default(client):
    resp = client.post("/query", content=WRITE_DOC, headers=AUTH)
    assert resp.status_code == 400
    assert "allow-writes" in resp.json()["detail"]


def test_add_only_mode_allows_adds_but_not_deletes(monkeypatch):
    monkeypatch.setattr(api_module, "SerialQuickBooks", FakeRuntime)
    app = api_module.create_app(token="secret", allow_writes=True)
    with TestClient(app) as tc:
        assert tc.get("/health").json()["mode"] == "add-only"
        assert tc.post("/query", content=WRITE_DOC, headers=AUTH).status_code == 200
        resp = tc.post("/query", content=DESTRUCTIVE_DOC, headers=AUTH)
        assert resp.status_code == 400
        assert "allow-destructive" in resp.json()["detail"]


def test_destructive_mode_allows_everything(monkeypatch):
    monkeypatch.setattr(api_module, "SerialQuickBooks", FakeRuntime)
    app = api_module.create_app(token="secret", allow_writes=True, allow_destructive=True)
    with TestClient(app) as tc:
        assert tc.get("/health").json()["mode"] == "read-write"
        assert tc.post("/query", content=DESTRUCTIVE_DOC, headers=AUTH).status_code == 200
