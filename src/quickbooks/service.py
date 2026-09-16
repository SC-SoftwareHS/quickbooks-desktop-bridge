"""Read-only QuickBooks operations used by the CLI and HTTP API."""

from __future__ import annotations

from dataclasses import replace

from . import qbxml
from .connection import QuickBooksConnection
from .models import Account, CompanyInfo, HostInfo, Report, Transaction
from .parsers import (
    parse_accounts,
    parse_company,
    parse_host,
    parse_report_document,
    parse_transactions,
)


class QuickBooksService:
    def __init__(self, connection: QuickBooksConnection, qbxml_version: str = qbxml.DEFAULT_QBXML_VERSION) -> None:
        self.connection = connection
        self.qbxml_version = qbxml_version

    def process(self, document: str) -> str:
        return self.connection.process(document)

    def host(self) -> HostInfo:
        xml = self.process(qbxml.host_query(version=self.qbxml_version))
        info = parse_host(xml)
        if info.supported_qbxml_versions:
            self.qbxml_version = _highest_version(info.supported_qbxml_versions, fallback=self.qbxml_version)
        return info

    def company(self) -> CompanyInfo:
        xml = self.process(qbxml.company_query(version=self.qbxml_version))
        info = parse_company(xml)
        if self.connection.company_file:
            return replace(info, company_file_path=self.connection.company_file)
        return info

    # AccountQueryRq and the unified TransactionQueryRq do not accept the
    # iterator attribute; QuickBooks rejects the whole document as unparseable.
    def accounts(self, *, active_status: str = "All") -> list[Account]:
        xml = self.process(
            qbxml.account_query(active_status=active_status, iterator=None, version=self.qbxml_version)
        )
        items, _, _ = parse_accounts(xml)
        return items

    def transactions(self, *, from_date: str | None = None, to_date: str | None = None) -> list[Transaction]:
        xml = self.process(
            qbxml.transaction_query(
                from_date=from_date, to_date=to_date, iterator=None, version=self.qbxml_version
            )
        )
        items, _, _ = parse_transactions(xml)
        return items

    def trial_balance(self, *, as_of: str, report_basis: str | None = None) -> Report:
        xml = self.process(
            qbxml.trial_balance_query(as_of=as_of, report_basis=report_basis, version=self.qbxml_version)
        )
        return parse_report_document(xml)

    def profit_loss(
        self, *, from_date: str, to_date: str, report_basis: str | None = None
    ) -> Report:
        xml = self.process(
            qbxml.profit_loss_query(
                from_date=from_date,
                to_date=to_date,
                report_basis=report_basis,
                version=self.qbxml_version,
            )
        )
        return parse_report_document(xml)

def _highest_version(versions: list[str], fallback: str) -> str:
    best = fallback
    best_n = _version_tuple(fallback)
    for raw in versions:
        n = _version_tuple(raw)
        if n > best_n:
            best, best_n = raw, n
    return best


def _version_tuple(value: str) -> tuple[int, int]:
    parts = value.strip().split(".")
    try:
        major = int(parts[0])
        minor = int(parts[1]) if len(parts) > 1 else 0
        return major, minor
    except ValueError:
        return (0, 0)
