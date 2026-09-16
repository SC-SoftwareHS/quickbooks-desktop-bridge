"""SerialQuickBooks recovers from a dead QuickBooks session without double-sending writes."""

import pytest

from quickbooks import connection
from quickbooks.connection import SerialQuickBooks
from quickbooks.errors import QBConnectionError, QBRefusedError

READ_DOC = "<QBXML><QBXMLMsgsRq><CompanyQueryRq/></QBXMLMsgsRq></QBXML>"
WRITE_DOC = "<QBXML><QBXMLMsgsRq><CustomerAddRq/></QBXMLMsgsRq></QBXML>"


class FakeTransport:
    name = "fake"

    def __init__(self, fail_open=False, fail_first_request=False):
        self.fail_open = fail_open
        self.fail_first_request = fail_first_request
        self.sent = []
        self.closed = False

    def open(self, app_name, company_file, file_mode, connection_type):
        if self.fail_open:
            raise QBConnectionError("BeginSession failed: company file is not open")

    def process_request(self, qbxml):
        if self.fail_first_request:
            self.fail_first_request = False
            raise QBConnectionError("ProcessRequest failed: QuickBooks was closed")
        self.sent.append(qbxml)
        return "<QBXML/>"

    def close(self):
        self.closed = True


@pytest.fixture
def transports(monkeypatch):
    queue = []
    monkeypatch.setattr(connection, "create_transport", lambda: queue.pop(0))
    return queue


def test_read_is_retried_on_a_fresh_session(transports):
    dead, fresh = FakeTransport(fail_first_request=True), FakeTransport()
    transports += [dead, fresh]
    runtime = SerialQuickBooks()
    try:
        assert runtime.process(READ_DOC) == "<QBXML/>"
    finally:
        runtime.close()
    assert dead.closed
    assert fresh.sent == [READ_DOC]


def test_write_is_not_retried_but_next_request_reconnects(transports):
    dead, fresh = FakeTransport(fail_first_request=True), FakeTransport()
    transports += [dead, fresh]
    runtime = SerialQuickBooks(read_only=False)
    try:
        with pytest.raises(QBConnectionError, match="QuickBooks was closed"):
            runtime.process(WRITE_DOC)
        assert fresh.sent == []
        assert runtime.process(WRITE_DOC) == "<QBXML/>"
    finally:
        runtime.close()
    assert fresh.sent == [WRITE_DOC]


def test_refused_document_keeps_the_session(transports):
    only = FakeTransport()
    transports.append(only)
    runtime = SerialQuickBooks()
    try:
        with pytest.raises(QBRefusedError):
            runtime.process(WRITE_DOC)
        assert runtime.process(READ_DOC) == "<QBXML/>"
    finally:
        runtime.close()
    assert only.sent == [READ_DOC]


def test_failed_session_open_is_retried_on_next_request(transports):
    transports += [FakeTransport(fail_open=True), FakeTransport()]
    runtime = SerialQuickBooks()
    try:
        with pytest.raises(QBConnectionError, match="company file is not open"):
            runtime.process(READ_DOC)
        assert runtime.process(READ_DOC) == "<QBXML/>"
    finally:
        runtime.close()
