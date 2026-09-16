"""Parse qbXML responses into domain models."""

from __future__ import annotations

import xml.etree.ElementTree as ET

from .errors import QBXMLStatusError
from .models import Account, Address, CompanyInfo, HostInfo, ListRef, Transaction
from .reports import parse_report
from .models import Report


def parse_xml(document: str) -> ET.Element:
    text = document.lstrip("\ufeff").strip()
    return ET.fromstring(text)


def _text(el: ET.Element | None, path: str | None = None, default: str | None = None) -> str | None:
    node = el.find(path) if (el is not None and path) else el
    if node is None or node.text is None:
        return default
    value = node.text.strip()
    return value if value != "" else default


def _bool(el: ET.Element | None, path: str, default: bool | None = None) -> bool | None:
    value = _text(el, path)
    if value is None:
        return default
    return value.lower() in ("true", "1")


def _int(el: ET.Element | None, path: str, default: int | None = None) -> int | None:
    value = _text(el, path)
    if value is None:
        return default
    try:
        return int(value)
    except ValueError:
        return default


def _ref(el: ET.Element | None, path: str) -> ListRef | None:
    if el is None:
        return None
    node = el.find(path)
    if node is None:
        return None
    list_id = _text(node, "ListID")
    full_name = _text(node, "FullName")
    if list_id is None and full_name is None:
        return None
    return ListRef(list_id=list_id, full_name=full_name)


def _address(el: ET.Element | None, path: str) -> Address | None:
    if el is None:
        return None
    node = el.find(path)
    if node is None:
        return None
    addr = Address(
        addr1=_text(node, "Addr1"),
        addr2=_text(node, "Addr2"),
        addr3=_text(node, "Addr3"),
        addr4=_text(node, "Addr4"),
        addr5=_text(node, "Addr5"),
        city=_text(node, "City"),
        state=_text(node, "State"),
        postal_code=_text(node, "PostalCode"),
        country=_text(node, "Country"),
    )
    if all(v is None for v in addr.__dict__.values()):
        return None
    return addr


def response_element(root: ET.Element) -> ET.Element:
    """Return the first *Rs element and raise on error status codes."""
    rs = None
    for el in root.iter():
        if el.tag.endswith("Rs") and "statusCode" in el.attrib:
            rs = el
            break
    if rs is None:
        raise QBXMLStatusError(0, "Error", "No *Rs element with statusCode in response", "unknown")
    code = int(rs.attrib.get("statusCode", "0"))
    severity = rs.attrib.get("statusSeverity", "")
    message = rs.attrib.get("statusMessage", "")
    # 0 = OK, 1 = no matching objects (empty success).
    if code not in (0, 1):
        raise QBXMLStatusError(code, severity, message, rs.tag)
    return rs


def iterator_state(rs: ET.Element) -> tuple[int, str | None]:
    remaining_raw = rs.attrib.get("iteratorRemainingCount")
    remaining = int(remaining_raw) if remaining_raw not in (None, "") else 0
    iterator_id = rs.attrib.get("iteratorID") or None
    return remaining, iterator_id


def parse_host(document: str) -> HostInfo:
    rs = response_element(parse_xml(document))
    ret = rs.find("HostRet")
    versions = []
    if ret is not None:
        versions = [
            (v.text or "").strip()
            for v in ret.findall("SupportedQBXMLVersion")
            if v.text and v.text.strip()
        ]
    return HostInfo(
        product_name=_text(ret, "ProductName"),
        major_version=_text(ret, "MajorVersion"),
        minor_version=_text(ret, "MinorVersion"),
        country=_text(ret, "Country"),
        supported_qbxml_versions=versions,
        is_automatic_login=_bool(ret, "IsAutomaticLogin"),
        qb_file_mode=_text(ret, "QBFileMode"),
    )


def parse_company(document: str) -> CompanyInfo:
    rs = response_element(parse_xml(document))
    ret = rs.find("CompanyRet")
    return CompanyInfo(
        company_name=_text(ret, "CompanyName"),
        legal_company_name=_text(ret, "LegalCompanyName"),
        address=_address(ret, "Address"),
        legal_address=_address(ret, "LegalAddress"),
        phone=_text(ret, "Phone"),
        email=_text(ret, "Email"),
        company_email_for_customer=_text(ret, "CompanyEmailForCustomer"),
        first_month_fiscal_year=_text(ret, "FirstMonthFiscalYear"),
        first_month_income_tax_year=_text(ret, "FirstMonthIncomeTaxYear"),
        company_type=_text(ret, "CompanyType"),
        ein=_text(ret, "EIN"),
        is_sample_company=_bool(ret, "IsSampleCompany"),
    )


def parse_accounts(document: str) -> tuple[list[Account], int, str | None]:
    rs = response_element(parse_xml(document))
    accounts = []
    for ret in rs.findall("AccountRet"):
        accounts.append(
            Account(
                list_id=_text(ret, "ListID"),
                name=_text(ret, "Name"),
                full_name=_text(ret, "FullName"),
                is_active=_bool(ret, "IsActive"),
                account_type=_text(ret, "AccountType"),
                account_number=_text(ret, "AccountNumber"),
                desc=_text(ret, "Desc"),
                balance=_text(ret, "Balance"),
                total_balance=_text(ret, "TotalBalance"),
                sublevel=_int(ret, "Sublevel"),
                parent=_ref(ret, "ParentRef"),
                special_account_type=_text(ret, "SpecialAccountType"),
                cash_flow_classification=_text(ret, "CashFlowClassification"),
                time_modified=_text(ret, "TimeModified"),
            )
        )
    remaining, iterator_id = iterator_state(rs)
    return accounts, remaining, iterator_id


def parse_transactions(document: str) -> tuple[list[Transaction], int, str | None]:
    rs = response_element(parse_xml(document))
    txns = []
    for ret in rs.findall("TransactionRet"):
        txns.append(
            Transaction(
                txn_type=_text(ret, "TxnType"),
                txn_id=_text(ret, "TxnID"),
                txn_date=_text(ret, "TxnDate"),
                ref_number=_text(ret, "RefNumber"),
                amount=_text(ret, "Amount"),
                memo=_text(ret, "Memo"),
                entity=_ref(ret, "EntityRef"),
                account=_ref(ret, "AccountRef"),
                txn_line_id=_text(ret, "TxnLineID"),
            )
        )
    remaining, iterator_id = iterator_state(rs)
    return txns, remaining, iterator_id


def parse_report_document(document: str) -> Report:
    rs = response_element(parse_xml(document))
    ret = rs.find("ReportRet")
    if ret is None:
        return Report()
    return parse_report(ret)
