"""Domain models for read-only QuickBooks Desktop queries."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


def _asdict(obj: Any) -> Any:
    if dataclasses_is_dataclass(obj):
        return {k: _asdict(v) for k, v in asdict(obj).items()}
    if isinstance(obj, list):
        return [_asdict(v) for v in obj]
    return obj


def dataclasses_is_dataclass(obj: Any) -> bool:
    return hasattr(obj, "__dataclass_fields__")


@dataclass
class ListRef:
    list_id: str | None = None
    full_name: str | None = None


@dataclass
class Address:
    addr1: str | None = None
    addr2: str | None = None
    addr3: str | None = None
    addr4: str | None = None
    addr5: str | None = None
    city: str | None = None
    state: str | None = None
    postal_code: str | None = None
    country: str | None = None


@dataclass
class HostInfo:
    product_name: str | None = None
    major_version: str | None = None
    minor_version: str | None = None
    country: str | None = None
    supported_qbxml_versions: list[str] = field(default_factory=list)
    is_automatic_login: bool | None = None
    qb_file_mode: str | None = None


@dataclass
class CompanyInfo:
    company_name: str | None = None
    legal_company_name: str | None = None
    address: Address | None = None
    legal_address: Address | None = None
    phone: str | None = None
    email: str | None = None
    company_email_for_customer: str | None = None
    first_month_fiscal_year: str | None = None
    first_month_income_tax_year: str | None = None
    company_type: str | None = None
    ein: str | None = None
    is_sample_company: bool | None = None
    company_file_path: str | None = None


@dataclass
class Account:
    list_id: str | None = None
    name: str | None = None
    full_name: str | None = None
    is_active: bool | None = None
    account_type: str | None = None
    account_number: str | None = None
    desc: str | None = None
    balance: str | None = None
    total_balance: str | None = None
    sublevel: int | None = None
    parent: ListRef | None = None
    special_account_type: str | None = None
    cash_flow_classification: str | None = None
    time_modified: str | None = None


@dataclass
class Transaction:
    txn_type: str | None = None
    txn_id: str | None = None
    txn_date: str | None = None
    ref_number: str | None = None
    amount: str | None = None
    memo: str | None = None
    entity: ListRef | None = None
    account: ListRef | None = None
    txn_line_id: str | None = None


@dataclass
class ReportColumn:
    col_id: int
    col_type: str | None = None
    data_type: str | None = None
    titles: list[str] = field(default_factory=list)


@dataclass
class ReportRow:
    kind: str  # data, subtotal, total, text
    row_type: str | None = None
    row_value: str | None = None
    row_number: int | None = None
    cells: dict[int, str] = field(default_factory=dict)


@dataclass
class Report:
    title: str | None = None
    subtitle: str | None = None
    basis: str | None = None
    num_rows: int | None = None
    num_columns: int | None = None
    columns: list[ReportColumn] = field(default_factory=list)
    rows: list[ReportRow] = field(default_factory=list)


def to_dict(obj: Any) -> Any:
    return _asdict(obj)
