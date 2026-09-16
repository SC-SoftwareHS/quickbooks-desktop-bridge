from pathlib import Path

from quickbooks.parsers import parse_accounts, parse_company, parse_report_document
from quickbooks.errors import QBXMLStatusError
from quickbooks.parsers import response_element, parse_xml

FIXTURES = Path(__file__).parent / "fixtures"


def test_parse_company():
    info = parse_company((FIXTURES / "company_rs.xml").read_text())
    assert info.company_name == "Acme Holdings"
    assert info.legal_company_name == "Acme Holdings, Inc."
    assert info.address is not None
    assert info.address.city == "Austin"
    assert info.ein == "12-3456789"
    assert info.is_sample_company is False


def test_parse_accounts():
    accounts, remaining, iterator_id = parse_accounts((FIXTURES / "account_rs.xml").read_text())
    assert remaining == 0
    assert iterator_id is None
    assert accounts[0].full_name == "Checking"
    assert accounts[0].account_type == "Bank"
    assert accounts[0].balance == "2500.00"
    assert accounts[1].parent is not None
    assert accounts[1].parent.full_name == "Income"


def test_parse_report():
    report = parse_report_document((FIXTURES / "report_rs.xml").read_text())
    assert report.title == "Trial Balance"
    assert report.basis == "Accrual"
    assert len(report.columns) == 3
    assert report.rows[0].kind == "data"
    assert report.rows[0].cells[2] == "2500.00"
    assert report.rows[1].kind == "total"


def test_status_error():
    xml = """<?xml version="1.0"?>
<QBXML>
  <QBXMLMsgsRs>
    <CompanyQueryRs statusCode="3120" statusSeverity="Error" statusMessage="Object not found"/>
  </QBXMLMsgsRs>
</QBXML>
"""
    try:
        response_element(parse_xml(xml))
        raise AssertionError("expected QBXMLStatusError")
    except QBXMLStatusError as exc:
        assert exc.status_code == 3120
