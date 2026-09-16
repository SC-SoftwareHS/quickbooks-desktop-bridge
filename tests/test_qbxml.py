from quickbooks import qbxml


def test_company_query_is_read_only_envelope():
    doc = qbxml.company_query()
    assert "<?qbxml version=\"13.0\"?>" in doc
    assert "<CompanyQueryRq" in doc
    assert qbxml.is_read_only_qbxml(doc)


def test_account_query_active_status():
    doc = qbxml.account_query(active_status="All", iterator=None)
    assert "<ActiveStatus>All</ActiveStatus>" in doc
    assert "iterator=" not in doc


def test_transaction_query_date_range():
    doc = qbxml.transaction_query(from_date="2026-01-01", to_date="2026-12-31", iterator=None)
    assert "<FromTxnDate>2026-01-01</FromTxnDate>" in doc
    assert "<ToTxnDate>2026-12-31</ToTxnDate>" in doc


def test_trial_balance_and_pnl():
    tb = qbxml.trial_balance_query(as_of="2026-12-31")
    assert "TrialBalance" in tb
    assert "<ToReportDate>2026-12-31</ToReportDate>" in tb
    assert "<DisplayReport>false</DisplayReport>" in tb

    pnl = qbxml.profit_loss_query(from_date="2026-01-01", to_date="2026-12-31")
    assert "ProfitAndLossStandard" in pnl
    assert "<FromReportDate>2026-01-01</FromReportDate>" in pnl


def test_read_only_guard_blocks_add():
    add = """<?xml version="1.0"?>
<?qbxml version="13.0"?>
<QBXML>
  <QBXMLMsgsRq onError="stopOnError">
    <CustomerAddRq>
      <CustomerAdd><Name>Nope</Name></CustomerAdd>
    </CustomerAddRq>
  </QBXMLMsgsRq>
</QBXML>
"""
    assert not qbxml.is_read_only_qbxml(add)
    assert qbxml.is_read_only_qbxml(qbxml.host_query())
