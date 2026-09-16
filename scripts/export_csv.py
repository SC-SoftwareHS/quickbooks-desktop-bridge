"""Export QuickBooks company data to CSV via the qbctl HTTP API.

Usage:
    QBCTL_URL=http://qb-vm:8765 QBCTL_TOKEN=... python scripts/export_csv.py [output_dir]

Writes: accounts.csv, transactions.csv, journal_entries.csv,
journal_entry_lines.csv, trial_balance.csv
"""

from __future__ import annotations

import csv
import json
import os
import sys
import xml.etree.ElementTree as ET
from datetime import date
from pathlib import Path
from urllib.request import Request, urlopen

URL = os.environ["QBCTL_URL"].rstrip("/")
TOKEN = os.environ["QBCTL_TOKEN"]
FROM_DATE = os.environ.get("QBCTL_FROM", "2017-01-01")
TO_DATE = os.environ.get("QBCTL_TO", date.today().isoformat())


def api(path: str, body: bytes | None = None) -> str:
    req = Request(
        URL + path,
        data=body,
        method="POST" if body else "GET",
        headers={
            "Authorization": f"Bearer {TOKEN}",
            **({"Content-Type": "application/xml"} if body else {}),
        },
    )
    with urlopen(req, timeout=300) as resp:
        return resp.read().decode("utf-8")


def get_json(path: str):
    return json.loads(api(path))


def write_csv(path: Path, rows: list[dict], columns: list[str]) -> None:
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    print(f"{path.name}: {len(rows)} rows")


def flatten_ref(row: dict, key: str) -> None:
    ref = row.get(key)
    row[key] = (ref or {}).get("full_name") if isinstance(ref, dict) else ref


def export_accounts(out: Path) -> list[dict]:
    accounts = get_json("/accounts")
    for a in accounts:
        flatten_ref(a, "parent")
    write_csv(
        out / "accounts.csv",
        accounts,
        [
            "full_name", "account_type", "account_number", "balance",
            "total_balance", "is_active", "sublevel", "parent", "desc",
            "special_account_type", "cash_flow_classification", "list_id",
            "time_modified",
        ],
    )
    return accounts


def export_transactions(out: Path) -> list[dict]:
    # Via /query with locally-built XML so a stale server build can't break this.
    from quickbooks import qbxml
    from quickbooks.models import to_dict
    from quickbooks.parsers import parse_transactions

    xml = api(
        "/query",
        qbxml.transaction_query(from_date=FROM_DATE, to_date=TO_DATE, iterator=None).encode("utf-8"),
    )
    items, _, _ = parse_transactions(xml)
    txns = [to_dict(t) for t in items]
    for t in txns:
        flatten_ref(t, "entity")
        flatten_ref(t, "account")
    txns.sort(key=lambda t: (t.get("txn_date") or "", t.get("txn_id") or ""))
    write_csv(
        out / "transactions.csv",
        txns,
        [
            "txn_date", "txn_type", "ref_number", "amount", "account",
            "entity", "memo", "txn_id", "txn_line_id",
        ],
    )
    return txns


def journal_entry_query(iterator: str, iterator_id: str | None) -> str:
    attrs = f'iterator="{iterator}"'
    if iterator_id:
        attrs += f' iteratorID="{iterator_id}"'
    return (
        '<?xml version="1.0" encoding="utf-8"?>\n'
        '<?qbxml version="13.0"?>\n'
        "<QBXML>\n"
        '  <QBXMLMsgsRq onError="stopOnError">\n'
        f"    <JournalEntryQueryRq {attrs}>\n"
        "      <MaxReturned>100</MaxReturned>\n"
        "      <IncludeLineItems>true</IncludeLineItems>\n"
        "    </JournalEntryQueryRq>\n"
        "  </QBXMLMsgsRq>\n"
        "</QBXML>\n"
    )


def text(el: ET.Element | None, tag: str) -> str | None:
    child = el.find(tag) if el is not None else None
    return child.text if child is not None else None


def ref_name(el: ET.Element, tag: str) -> str | None:
    return text(el.find(tag), "FullName")


def export_journal_entries(out: Path) -> tuple[list[dict], list[dict]]:
    entries: list[dict] = []
    lines: list[dict] = []
    iterator, iterator_id = "Start", None
    while True:
        xml = api("/query", journal_entry_query(iterator, iterator_id).encode("utf-8"))
        root = ET.fromstring(xml)
        rs = root.find(".//JournalEntryQueryRs")
        if rs is None:
            raise SystemExit(f"No JournalEntryQueryRs in response: {xml[:500]}")
        if rs.get("statusSeverity") == "Error":
            raise SystemExit(f"JournalEntryQuery error: {rs.get('statusMessage')}")
        for ret in rs.findall("JournalEntryRet"):
            txn_id = text(ret, "TxnID")
            entry = {
                "txn_id": txn_id,
                "txn_number": text(ret, "TxnNumber"),
                "txn_date": text(ret, "TxnDate"),
                "ref_number": text(ret, "RefNumber"),
                "is_adjustment": text(ret, "IsAdjustment"),
                "time_created": text(ret, "TimeCreated"),
                "time_modified": text(ret, "TimeModified"),
            }
            total_debit = total_credit = 0.0
            for kind, tag in (("debit", "JournalDebitLine"), ("credit", "JournalCreditLine")):
                for line in ret.findall(tag):
                    amount = text(line, "Amount") or "0"
                    if kind == "debit":
                        total_debit += float(amount)
                    else:
                        total_credit += float(amount)
                    lines.append(
                        {
                            "txn_id": txn_id,
                            "txn_date": entry["txn_date"],
                            "ref_number": entry["ref_number"],
                            "txn_line_id": text(line, "TxnLineID"),
                            "side": kind,
                            "account": ref_name(line, "AccountRef"),
                            "amount": amount,
                            "memo": text(line, "Memo"),
                            "entity": ref_name(line, "EntityRef"),
                            "class": ref_name(line, "ClassRef"),
                        }
                    )
            entry["total_debit"] = f"{total_debit:.2f}"
            entry["total_credit"] = f"{total_credit:.2f}"
            entries.append(entry)
        remaining = int(rs.get("iteratorRemainingCount") or 0)
        iterator_id = rs.get("iteratorID")
        if remaining <= 0 or not iterator_id:
            break
        iterator = "Continue"
    entries.sort(key=lambda e: (e.get("txn_date") or "", e.get("txn_number") or ""))
    lines.sort(key=lambda l: (l.get("txn_date") or "", l.get("txn_id") or "", l.get("side") or ""))
    write_csv(
        out / "journal_entries.csv",
        entries,
        [
            "txn_date", "ref_number", "txn_number", "total_debit", "total_credit",
            "is_adjustment", "txn_id", "time_created", "time_modified",
        ],
    )
    write_csv(
        out / "journal_entry_lines.csv",
        lines,
        [
            "txn_date", "ref_number", "side", "account", "amount", "memo",
            "entity", "class", "txn_id", "txn_line_id",
        ],
    )
    return entries, lines


def export_trial_balance(out: Path) -> None:
    report = get_json(f"/reports/trial-balance?date={TO_DATE}")
    col_ids = [c["col_id"] for c in report.get("columns", [])]
    titles = [
        " / ".join(t for t in c.get("titles", []) if t) or c.get("col_type") or str(c["col_id"])
        for c in report.get("columns", [])
    ]
    rows = []
    for row in report.get("rows", []):
        cells = {int(k): v for k, v in row.get("cells", {}).items()}
        out_row = {"kind": row.get("kind"), "label": row.get("row_value")}
        for cid, title in zip(col_ids, titles):
            out_row[title] = cells.get(cid, "")
        rows.append(out_row)
    columns = ["kind", "label"] + [t for t in titles if t not in ("kind", "label")]
    write_csv(out / "trial_balance.csv", rows, columns)


def main() -> None:
    out = Path(sys.argv[1] if len(sys.argv) > 1 else "exports")
    out.mkdir(parents=True, exist_ok=True)
    print(f"Exporting {FROM_DATE}..{TO_DATE} from {URL} to {out}/")
    export_accounts(out)
    export_transactions(out)
    export_journal_entries(out)
    export_trial_balance(out)


if __name__ == "__main__":
    main()
