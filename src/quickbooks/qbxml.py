"""Construct qbXML request documents. Read-only queries only."""

from __future__ import annotations

import xml.etree.ElementTree as ET

DEFAULT_QBXML_VERSION = "13.0"

# Requests that mutate QuickBooks. The bridge refuses to send these.
WRITE_REQUEST_TAGS = (
    "AddRq",
    "ModRq",
    "DelRq",
    "VoidRq",
    "TxnVoidRq",
    "ClearedStatusModRq",
    "DataExtAdd",
    "DataExtMod",
    "DataExtDel",
    "ListDeletedQuery",  # query is fine; listed so we don't over-match Add
)


def is_read_only_qbxml(document: str) -> bool:
    """True if the document contains no add/mod/del/void request tags."""
    lowered = document.lower()
    # Cheap tag scan — no trailing ">" so self-closing tags (<XAddRq/>) and
    # tags with attributes are caught too. Over-matching is the safe direction.
    forbidden = (
        "addrq",
        "modrq",
        "delrq",
        "voidrq",
        "<dataextadd",
        "<dataextmod",
        "<dataextdel",
    )
    return not any(token in lowered for token in forbidden)


def is_additive_qbxml(document: str) -> bool:
    """True if the document contains no mod/del/void tags (queries and adds only)."""
    lowered = document.lower()
    destructive = (
        "modrq",
        "delrq",
        "voidrq",
        "<dataextmod",
        "<dataextdel",
    )
    return not any(token in lowered for token in destructive)


def envelope(inner_xml: str, version: str = DEFAULT_QBXML_VERSION, on_error: str = "stopOnError") -> str:
    inner = inner_xml.strip()
    return (
        '<?xml version="1.0" encoding="utf-8"?>\n'
        f'<?qbxml version="{version}"?>\n'
        "<QBXML>\n"
        f'  <QBXMLMsgsRq onError="{on_error}">\n'
        f"    {inner}\n"
        "  </QBXMLMsgsRq>\n"
        "</QBXML>\n"
    )


def _elem(tag: str, text: str | None = None, attrib: dict[str, str] | None = None) -> ET.Element:
    el = ET.Element(tag, attrib or {})
    if text is not None:
        el.text = text
    return el


def _tostring(el: ET.Element) -> str:
    return ET.tostring(el, encoding="unicode")


def host_query(version: str = DEFAULT_QBXML_VERSION) -> str:
    return envelope(_tostring(_elem("HostQueryRq")), version=version)


def company_query(version: str = DEFAULT_QBXML_VERSION) -> str:
    return envelope(_tostring(_elem("CompanyQueryRq")), version=version)


def account_query(
    *,
    active_status: str = "All",
    iterator: str | None = "Start",
    iterator_id: str | None = None,
    max_returned: int = 100,
    version: str = DEFAULT_QBXML_VERSION,
) -> str:
    attrib: dict[str, str] = {}
    if iterator:
        attrib["iterator"] = iterator
    if iterator_id:
        attrib["iteratorID"] = iterator_id
    rq = _elem("AccountQueryRq", attrib=attrib)
    if iterator:
        rq.append(_elem("MaxReturned", str(max_returned)))
    rq.append(_elem("ActiveStatus", active_status))
    return envelope(_tostring(rq), version=version)


def transaction_query(
    *,
    from_date: str | None = None,
    to_date: str | None = None,
    iterator: str | None = "Start",
    iterator_id: str | None = None,
    max_returned: int = 100,
    version: str = DEFAULT_QBXML_VERSION,
) -> str:
    attrib: dict[str, str] = {}
    if iterator:
        attrib["iterator"] = iterator
    if iterator_id:
        attrib["iteratorID"] = iterator_id
    rq = _elem("TransactionQueryRq", attrib=attrib)
    if iterator:
        rq.append(_elem("MaxReturned", str(max_returned)))
    if from_date or to_date:
        # The unified TransactionQueryRq names this TransactionDateRangeFilter;
        # TxnDateRangeFilter belongs to the per-type queries (InvoiceQuery etc).
        filt = _elem("TransactionDateRangeFilter")
        if from_date:
            filt.append(_elem("FromTxnDate", from_date))
        if to_date:
            filt.append(_elem("ToTxnDate", to_date))
        rq.append(filt)
    return envelope(_tostring(rq), version=version)


def general_summary_report(
    report_type: str,
    *,
    from_date: str | None = None,
    to_date: str | None = None,
    report_basis: str | None = None,
    version: str = DEFAULT_QBXML_VERSION,
) -> str:
    rq = _elem("GeneralSummaryReportQueryRq")
    rq.append(_elem("GeneralSummaryReportType", report_type))
    rq.append(_elem("DisplayReport", "false"))
    if from_date or to_date:
        period = _elem("ReportPeriod")
        if from_date:
            period.append(_elem("FromReportDate", from_date))
        if to_date:
            period.append(_elem("ToReportDate", to_date))
        rq.append(period)
    if report_basis:
        rq.append(_elem("ReportBasis", report_basis))
    return envelope(_tostring(rq), version=version)


def trial_balance_query(
    *,
    as_of: str,
    report_basis: str | None = None,
    version: str = DEFAULT_QBXML_VERSION,
) -> str:
    return general_summary_report(
        "TrialBalance",
        to_date=as_of,
        report_basis=report_basis,
        version=version,
    )


def profit_loss_query(
    *,
    from_date: str,
    to_date: str,
    report_basis: str | None = None,
    version: str = DEFAULT_QBXML_VERSION,
) -> str:
    return general_summary_report(
        "ProfitAndLossStandard",
        from_date=from_date,
        to_date=to_date,
        report_basis=report_basis,
        version=version,
    )
