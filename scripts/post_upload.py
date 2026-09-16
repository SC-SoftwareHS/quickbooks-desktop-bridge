"""Post a reviewed staging CSV into QuickBooks via the qbctl API.

Usage:
    QBCTL_URL=... QBCTL_TOKEN=... .venv/bin/python scripts/post_upload.py staging/upload.csv           # dry run
    QBCTL_URL=... QBCTL_TOKEN=... .venv/bin/python scripts/post_upload.py staging/upload.csv --post    # write

Only rows with status=ready are considered. The server must be running with
--allow-writes for --post. Missing payees are auto-created as Vendors.
Posted rows are logged to imports/posted.csv so reruns never double-post.
"""

from __future__ import annotations

import csv
import os
import sys
import xml.etree.ElementTree as ET
from collections import defaultdict
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from xml.sax.saxutils import escape

REPO = Path(__file__).resolve().parent.parent
URL = os.environ["QBCTL_URL"].rstrip("/")
TOKEN = os.environ["QBCTL_TOKEN"]
POSTED_LOG = REPO / "imports" / "posted.csv"
POSTED_COLUMNS = ["key", "txn_id", "txn_type", "date", "amount", "bank_account", "payee"]


def api_query(document: str) -> str:
    req = Request(
        URL + "/query",
        data=document.encode("utf-8"),
        method="POST",
        headers={"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/xml"},
    )
    try:
        with urlopen(req, timeout=300) as resp:
            return resp.read().decode("utf-8")
    except HTTPError as exc:
        raise SystemExit(f"HTTP {exc.code}: {exc.read().decode('utf-8', 'replace')[:300]}")


def envelope(inner: str) -> str:
    return (
        '<?xml version="1.0" encoding="utf-8"?>\n<?qbxml version="13.0"?>\n'
        f'<QBXML><QBXMLMsgsRq onError="stopOnError">{inner}</QBXMLMsgsRq></QBXML>'
    )


def response_status(xml: str) -> tuple[int, str, str | None]:
    """(statusCode, statusMessage, TxnID) of the first request-level *Rs element."""
    root = ET.fromstring(xml)
    for el in root.iter():
        # Skip the QBXMLMsgsRs wrapper — status lives on CheckAddRs et al.
        if el.tag.endswith("Rs") and el.tag != "QBXMLMsgsRs":
            code = int(el.get("statusCode") or 0)
            txn_id = None
            for ret in el:
                child = ret.find("TxnID")
                if child is not None:
                    txn_id = child.text
            return code, el.get("statusMessage") or "", txn_id
    return -1, "no response element", None


def row_key(row: dict) -> str:
    return "|".join([row["source"], row["source_ref"], row["date"], row["amount"], row["txn_type"]])


def load_posted() -> set[str]:
    if not POSTED_LOG.is_file():
        return set()
    return {r["key"] for r in csv.DictReader(POSTED_LOG.open())}


def log_posted(entries: list[dict]) -> None:
    POSTED_LOG.parent.mkdir(parents=True, exist_ok=True)
    new_file = not POSTED_LOG.is_file()
    with POSTED_LOG.open("a", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=POSTED_COLUMNS, extrasaction="ignore")
        if new_file:
            writer.writeheader()
        writer.writerows(entries)


def existing_names() -> set[str]:
    names: set[str] = set()
    for rq in ("VendorQueryRq", "CustomerQueryRq", "EmployeeQueryRq", "OtherNameQueryRq"):
        xml = api_query(envelope(f"<{rq}/>"))
        root = ET.fromstring(xml)
        for el in root.iter("Name"):
            if el.text:
                names.add(el.text.strip().lower())
    return names


def vendor_add(name: str) -> None:
    xml = api_query(envelope(f"<VendorAddRq><VendorAdd><Name>{escape(name[:41])}</Name></VendorAdd></VendorAddRq>"))
    code, msg, _ = response_status(xml)
    if code not in (0, 3100):  # 3100 = already exists
        raise SystemExit(f"VendorAdd {name!r} failed: {code} {msg}")


def build_txn_xml(row: dict) -> str:
    t = row["txn_type"]
    date, memo = row["date"], escape(row["memo"][:200])
    payee, amount = escape(row["payee"][:41]), row["amount"]
    bank, acct = escape(row["bank_account"]), escape(row["account"])
    if t == "Check":
        return (
            f"<CheckAddRq><CheckAdd><AccountRef><FullName>{bank}</FullName></AccountRef>"
            f"<PayeeEntityRef><FullName>{payee}</FullName></PayeeEntityRef>"
            f"<TxnDate>{date}</TxnDate><Memo>{memo}</Memo>"
            f"<ExpenseLineAdd><AccountRef><FullName>{acct}</FullName></AccountRef>"
            f"<Amount>{amount}</Amount><Memo>{memo}</Memo></ExpenseLineAdd>"
            f"</CheckAdd></CheckAddRq>"
        )
    if t in ("CreditCardCharge", "CreditCardCredit"):
        tag = f"{t}Add"
        return (
            f"<{tag}Rq><{tag}><AccountRef><FullName>{bank}</FullName></AccountRef>"
            f"<PayeeEntityRef><FullName>{payee}</FullName></PayeeEntityRef>"
            f"<TxnDate>{date}</TxnDate><Memo>{memo}</Memo>"
            f"<ExpenseLineAdd><AccountRef><FullName>{acct}</FullName></AccountRef>"
            f"<Amount>{amount}</Amount><Memo>{memo}</Memo></ExpenseLineAdd>"
            f"</{tag}></{tag}Rq>"
        )
    if t == "Deposit":
        return (
            f"<DepositAddRq><DepositAdd><TxnDate>{date}</TxnDate>"
            f"<DepositToAccountRef><FullName>{bank}</FullName></DepositToAccountRef>"
            f"<Memo>{memo}</Memo>"
            f"<DepositLineAdd><AccountRef><FullName>{acct}</FullName></AccountRef>"
            f"<Memo>{memo}</Memo><Amount>{amount}</Amount></DepositLineAdd>"
            f"</DepositAdd></DepositAddRq>"
        )
    if t == "JournalEntry":  # single debit/credit pair: debit bank_account, credit account
        return (
            f"<JournalEntryAddRq><JournalEntryAdd><TxnDate>{date}</TxnDate>"
            f"<JournalDebitLine><AccountRef><FullName>{bank}</FullName></AccountRef>"
            f"<Amount>{amount}</Amount><Memo>{memo}</Memo></JournalDebitLine>"
            f"<JournalCreditLine><AccountRef><FullName>{acct}</FullName></AccountRef>"
            f"<Amount>{amount}</Amount><Memo>{memo}</Memo></JournalCreditLine>"
            f"</JournalEntryAdd></JournalEntryAddRq>"
        )
    raise ValueError(f"unsupported txn_type {t!r}")


def build_grouped_je_xml(group: list[dict]) -> str:
    date = group[0]["date"]
    memo = escape(group[0]["memo"][:200])
    debits, credits = [], []
    for row in group:
        amount = row["amount"]
        if row["bank_account"]:
            debits.append(
                f"<JournalDebitLine><AccountRef><FullName>{escape(row['bank_account'])}</FullName>"
                f"</AccountRef><Amount>{amount}</Amount><Memo>{memo}</Memo></JournalDebitLine>"
            )
        else:
            credits.append(
                f"<JournalCreditLine><AccountRef><FullName>{escape(row['account'])}</FullName>"
                f"</AccountRef><Amount>{amount}</Amount><Memo>{memo}</Memo></JournalCreditLine>"
            )
    return (
        f"<JournalEntryAddRq><JournalEntryAdd><TxnDate>{date}</TxnDate>"
        + "".join(debits) + "".join(credits)
        + "</JournalEntryAdd></JournalEntryAddRq>"
    )


def main() -> None:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    do_post = "--post" in sys.argv
    staging = Path(args[0]) if args else REPO / "staging" / "upload.csv"
    rows = [r for r in csv.DictReader(staging.open())]
    posted_keys = load_posted()

    ready = [r for r in rows if r["status"] == "ready" and row_key(r) not in posted_keys]
    skipped_posted = sum(1 for r in rows if r["status"] == "ready" and row_key(r) in posted_keys)
    print(f"{staging}: {len(rows)} rows, {len(ready)} ready to post, "
          f"{skipped_posted} already posted, "
          f"{sum(1 for r in rows if r['status'] == 'review')} still in review")

    # Batch: single txns, plus ADP JE lines grouped per source sheet
    singles = [r for r in ready if r["txn_type"] != "JournalEntryLine"]
    je_groups = defaultdict(list)
    for r in ready:
        if r["txn_type"] == "JournalEntryLine":
            je_groups[r["source"]].append(r)
    for src, group in je_groups.items():
        debit = sum(float(r["amount"]) for r in group if r["bank_account"])
        credit = sum(float(r["amount"]) for r in group if not r["bank_account"])
        if abs(debit - credit) > 0.005:
            raise SystemExit(f"JE group {src} unbalanced: debits {debit:.2f} != credits {credit:.2f}")

    jobs: list[tuple[str, list[dict]]] = [(build_txn_xml(r), [r]) for r in singles]
    jobs += [(build_grouped_je_xml(g), g) for g in je_groups.values()]
    print(f"{len(jobs)} QuickBooks transactions to create "
          f"({len(singles)} bank/card, {len(je_groups)} journal entries)")

    if not do_post:
        for xml, group in jobs[:3]:
            print("\n--- preview:", group[0]["date"], group[0]["txn_type"], group[0]["payee"])
            print(envelope(xml))
        print(f"\nDRY RUN — nothing posted. Re-run with --post to write to QuickBooks.")
        return

    names = existing_names()
    needed = {r["payee"] for r in singles if r["txn_type"] in ("Check", "CreditCardCharge", "CreditCardCredit")}
    missing = sorted(n for n in needed if n and n.strip().lower() not in names)
    for name in missing:
        print(f"creating vendor: {name}")
        vendor_add(name)

    posted_rows, failures = 0, []
    for xml, group in jobs:
        first = group[0]
        label = f"{first['date']} {first['txn_type']} {first['payee']} {first['amount']}"
        try:
            response = api_query(envelope(xml))
        except SystemExit as exc:
            raise SystemExit(
                f"{exc}\nStopped at: {label}\nEverything before it is logged in {POSTED_LOG}. "
                "This one may or may not have been created; check QuickBooks before re-running."
            ) from None
        code, msg, txn_id = response_status(response)
        if code == 0:
            print(f"ok    {label}  -> {txn_id}")
            # Log each success immediately so a failure later in the run can't cause a double-post.
            log_posted([{
                "key": row_key(row), "txn_id": txn_id or "", "txn_type": row["txn_type"],
                "date": row["date"], "amount": row["amount"],
                "bank_account": row["bank_account"], "payee": row["payee"],
            } for row in group])
            posted_rows += len(group)
        else:
            print(f"FAIL  {label}  -> {code} {msg}")
            failures.append(label)
    print(f"\nposted {posted_rows} rows across {len(jobs) - len(failures)} transactions, "
          f"{len(failures)} failures, log: {POSTED_LOG}")


if __name__ == "__main__":
    main()
