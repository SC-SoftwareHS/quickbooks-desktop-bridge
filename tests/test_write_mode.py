"""The read-only guard stays on by default and opens only with read_only=False."""

import pytest

from quickbooks.connection import QuickBooksConnection
from quickbooks.errors import QBConnectionError

WRITE_DOC = """<?xml version="1.0"?>
<?qbxml version="13.0"?>
<QBXML>
  <QBXMLMsgsRq onError="stopOnError">
    <CustomerAddRq>
      <CustomerAdd><Name>Test</Name></CustomerAdd>
    </CustomerAddRq>
  </QBXMLMsgsRq>
</QBXML>
"""


class FakeTransport:
    name = "fake"

    def __init__(self):
        self.sent = []

    def open(self, app_name, company_file, file_mode, connection_type):
        pass

    def process_request(self, qbxml):
        self.sent.append(qbxml)
        return "<QBXML/>"

    def close(self):
        pass


def test_default_connection_blocks_writes():
    with QuickBooksConnection(transport=FakeTransport()) as conn:
        with pytest.raises(QBConnectionError, match="read-only"):
            conn.process(WRITE_DOC)


def test_write_mode_sends_writes():
    transport = FakeTransport()
    with QuickBooksConnection(transport=transport, read_only=False) as conn:
        assert conn.process(WRITE_DOC) == "<QBXML/>"
    assert transport.sent == [WRITE_DOC]


DELETE_DOC = "<QBXML><QBXMLMsgsRq><TxnDelRq/></QBXMLMsgsRq></QBXML>"


def test_add_only_blocks_destructive():
    with QuickBooksConnection(transport=FakeTransport(), read_only=False) as conn:
        with pytest.raises(QBConnectionError, match="add-only"):
            conn.process(DELETE_DOC)


def test_destructive_mode_sends_deletes():
    transport = FakeTransport()
    with QuickBooksConnection(transport=transport, read_only=False, allow_destructive=True) as conn:
        assert conn.process(DELETE_DOC) == "<QBXML/>"
