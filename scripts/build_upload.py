"""Build a reviewable QuickBooks upload file from Mercury / Schwab / ADP exports.

Usage:
    .venv/bin/python scripts/build_upload.py [InstitutionData] [staging.csv]

Reads:
    InstitutionData/transactions/Mercury*.csv   (Mercury bank export)
    InstitutionData/transactions/Schwab*.csv    (Schwab bank export)
    InstitutionData/payroll/*.xlsx              (ADP "GL package" workbooks)
    imports/rules.csv                           (payee -> expense account)
    exports/transactions.csv                    (what QuickBooks already has)
    imports/posted.csv                          (what post_upload.py already posted)

Writes one staging CSV. Every row has a status:
    ready           safe to post
    review          needs a human: fill in `account`, then set status to ready
    skip_duplicate  already in QuickBooks (or already posted) — left for audit
    skip_transfer   covered by a journal entry generated from the Schwab side

Review the file, edit as needed, then run post_upload.py on it.
"""

from __future__ import annotations

import csv
import re
import sys
from datetime import datetime, timedelta
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


def _load_config() -> dict:
    """Read config.toml (your chart of accounts), falling back to the example."""
    try:
        import tomllib
    except ModuleNotFoundError:  # Python < 3.11
        sys.exit("scripts/build_upload.py needs Python 3.11+ (tomllib)")
    for name in ("config.toml", "config.example.toml"):
        path = REPO / name
        if path.is_file():
            if name == "config.example.toml":
                print(
                    "warning: no config.toml — using config.example.toml placeholders, "
                    "which will not match your chart of accounts",
                    file=sys.stderr,
                )
            with path.open("rb") as fh:
                return tomllib.load(fh)
    sys.exit("no config.toml found; copy config.example.toml to config.toml and edit it")


CONFIG = _load_config()

# QuickBooks account names (must match the chart of accounts exactly)
SCHWAB_CASH = CONFIG["accounts"]["brokerage_cash"]
INTEREST_INCOME = CONFIG["accounts"]["interest_income"]
CONSULTING_INCOME = CONFIG["accounts"]["client_income"]
MERCURY_CHECKING = CONFIG["accounts"]["operating_checking"]

# A recurring incoming client wire of exactly this amount books straight to
# income; any other incoming wire waits for review. Empty means review them all.
QUARTERLY_FEE = CONFIG["wires"]["recognized_amount"]

BANK_TXN_TYPES = {False: "Check", True: "Deposit"}          # by amount > 0
CARD_TXN_TYPES = {False: "CreditCardCharge", True: "CreditCardCredit"}
CARD_ACCOUNTS = set(CONFIG["cards"]["card_accounts"])  # source accounts that are cards


def load_rules() -> list[tuple[str, str]]:
    rules = []
    path = REPO / "imports" / "rules.csv"
    if path.is_file():
        for row in csv.DictReader(path.open()):
            if row.get("match") and row.get("account"):
                rules.append((row["match"].strip().lower(), row["account"].strip()))
    rules.sort(key=lambda r: -len(r[0]))  # longest (most specific) match first
    return rules


def match_rule(rules: list[tuple[str, str]], payee: str) -> str | None:
    p = payee.strip().lower()
    for needle, account in rules:
        # Only exact, or the rule appearing inside the payee. Matching the other
        # way round let a short payee ("Google") hit a longer, unrelated rule
        # ("Google Play") and book Google Ads to Dues and Subscriptions.
        if needle == p or needle in p:
            return account
    return None


def load_known_accounts() -> set[str]:
    path = REPO / "exports" / "accounts.csv"
    if not path.is_file():
        return set()
    return {r["full_name"] for r in csv.DictReader(path.open()) if r.get("full_name")}


def load_existing_keys() -> set[tuple[str, str, str]]:
    """(date, unsigned amount, bank account) for everything QuickBooks has or we posted."""
    keys = set()
    tx_path = REPO / "exports" / "transactions.csv"
    if tx_path.is_file():
        for r in csv.DictReader(tx_path.open()):
            amt = _num(r.get("amount"))
            if r.get("txn_date") and amt is not None:
                keys.add((r["txn_date"], f"{abs(amt):.2f}", r.get("account") or ""))
    posted = REPO / "imports" / "posted.csv"
    if posted.is_file():
        for r in csv.DictReader(posted.open()):
            if r.get("date") and r.get("amount"):
                keys.add((r["date"], f"{abs(float(r['amount'])):.2f}", r.get("bank_account") or ""))
    return keys


def _num(value) -> float | None:
    if value is None:
        return None
    s = re.sub(r"[$,\s]", "", str(value))
    if not s or not re.search(r"\d", s):
        return None
    try:
        return float(s)
    except ValueError:
        return None


def _mercury_date(raw: str) -> str:
    return datetime.strptime(raw.strip(), "%m-%d-%Y").date().isoformat()


def _schwab_date(raw: str) -> str:
    # "07/06/2026 as of 07/03/2026" -> use the "as of" (effective) date
    part = raw.split("as of")[-1].strip()
    return datetime.strptime(part, "%m/%d/%Y").date().isoformat()


def parse_mercury(path: Path, rules) -> list[dict]:
    rows = []
    for r in csv.DictReader(path.open()):
        status = (r.get("Status") or "").strip()
        if status not in ("Sent",):  # Pending / Failed: not settled, never book
            continue
        amount = _num(r.get("Amount"))
        source = (r.get("Source Account") or "").strip()
        if amount is None or not source:
            continue
        payee = (r.get("Description") or "").strip()
        is_card = source in CARD_ACCOUNTS
        txn_type = (CARD_TXN_TYPES if is_card else BANK_TXN_TYPES)[amount > 0]
        account = match_rule(rules, payee)
        memo_bits = [b for b in ((r.get("Bank Description") or "").strip(),
                                 (r.get("Note") or "").strip()) if b]
        rows.append(
            {
                "source": path.name,
                "source_ref": (r.get("Timestamp") or "").strip(),
                "date": _mercury_date(r["Date (UTC)"]),
                "txn_type": txn_type,
                "bank_account": source,
                "payee": payee,
                "account": account or "",
                "amount": f"{abs(amount):.2f}",
                "memo": " | ".join(memo_bits)[:200],
                "status": "ready" if account else "review",
                "note": "" if account else "no rule for payee — set account, flip status to ready",
            }
        )
    return rows


def parse_schwab(path: Path, rules) -> list[dict]:
    rows = []
    for r in csv.DictReader(path.open()):
        amount = _num(r.get("Amount"))
        if amount is None:
            continue
        action = (r.get("Action") or "").strip()
        date = _schwab_date(r["Date"])
        desc = (r.get("Description") or "").strip()
        base = {
            "source": path.name,
            "source_ref": f"{action} {r.get('Date','').strip()}",
            "date": date,
            "payee": "Schwab",
            "amount": f"{abs(amount):.2f}",
            "memo": desc[:200],
        }
        if action == "Bank Interest":
            rows.append(base | {
                "txn_type": "JournalEntry",
                "bank_account": SCHWAB_CASH,     # debit
                "account": INTEREST_INCOME,      # credit
                "status": "ready",
                "note": f"interest: debit {SCHWAB_CASH}, credit {INTEREST_INCOME}",
            })
        elif action == "Wire Sent":
            rows.append(base | {
                "txn_type": "JournalEntry",
                "bank_account": MERCURY_CHECKING,  # debit (money arrives at Mercury)
                "account": SCHWAB_CASH,            # credit
                "status": "ready",
                "note": f"transfer {SCHWAB_CASH} -> {MERCURY_CHECKING} "
                        "(the matching deposit row is skipped)",
            })
        elif action == "Wire Received":
            is_fee = bool(QUARTERLY_FEE) and base["amount"] == QUARTERLY_FEE
            rows.append(base | {
                "txn_type": "JournalEntry",
                "bank_account": SCHWAB_CASH,     # debit
                "account": CONSULTING_INCOME,    # credit
                "status": "ready" if is_fee else "review",
                "note": f"client fee in: debit {SCHWAB_CASH}, credit {CONSULTING_INCOME}"
                        if is_fee else
                        "incoming wire is not the recognized recurring fee — confirm it is "
                        "income (not a loan or capital contribution) before flipping to ready",
            })
        else:
            rows.append(base | {
                "txn_type": "JournalEntry", "bank_account": SCHWAB_CASH, "account": "",
                "status": "review", "note": f"unrecognized Schwab action {action!r}",
            })
    return rows


def parse_adp_workbook(path: Path, known_accounts: set[str]) -> list[dict]:
    import openpyxl

    rows = []
    wb = openpyxl.load_workbook(path, read_only=True)
    for ws in wb.worksheets:
        header, lines, je_date = None, [], None
        for raw in ws.iter_rows(values_only=True):
            cells = ["" if v is None else str(v).strip() for v in raw]
            if not any(cells):
                continue
            if cells[0] == "Account Number":
                header = cells
                continue
            if header is None or cells[0] in ("Totals",):
                continue
            name, debit, credit = cells[1], _num(cells[2]), _num(cells[3])
            date_cell = raw[4]
            if isinstance(date_cell, datetime):
                je_date = date_cell.date().isoformat()
            elif cells[4]:
                try:
                    je_date = datetime.strptime(cells[4], "%m/%d/%Y").date().isoformat()
                except ValueError:
                    pass
            if name and (debit or credit):
                lines.append((name, debit or 0.0, credit or 0.0))
        if not lines or not je_date:
            continue
        unknown = [n for n, _, _ in lines if n not in known_accounts] if known_accounts else []
        for name, debit, credit in lines:
            side_amount = debit if debit else credit
            rows.append(
                {
                    "source": f"{path.name}:{ws.title.strip()}",
                    "source_ref": ws.title.strip(),
                    "date": je_date,
                    "txn_type": "JournalEntryLine",
                    "bank_account": name if debit else "",   # debit side
                    "account": name if credit else "",       # credit side
                    "payee": "ADP",
                    "amount": f"{side_amount:.2f}",
                    "memo": f"ADP payroll {je_date}",
                    "status": "review" if unknown else "ready",
                    "note": (f"accounts not in QB: {', '.join(sorted(set(unknown)))}"
                             if unknown else "one balanced JE per sheet"),
                }
            )
    return rows


def load_existing_je_keys() -> set[tuple[str, str]]:
    """(date, total_debit) of journal entries already in QuickBooks."""
    path = REPO / "exports" / "journal_entries.csv"
    if not path.is_file():
        return set()
    return {
        (r["txn_date"], f"{float(r['total_debit']):.2f}")
        for r in csv.DictReader(path.open())
        if r.get("txn_date")
    }


def mark_duplicates(rows: list[dict], existing: set[tuple[str, str, str]]) -> None:
    # A generated JE (one row, or an ADP group sharing a source sheet) is a
    # duplicate as a whole if QuickBooks already has a JE with the same
    # date and total debits — line-level matching would miss regrouped lines.
    existing_jes = load_existing_je_keys()
    groups: dict[str, list[dict]] = {}
    for row in rows:
        if row["txn_type"] == "JournalEntryLine":
            groups.setdefault(row["source"], []).append(row)
        elif row["txn_type"] == "JournalEntry":
            groups.setdefault(f"single:{id(row)}", []).append(row)
    for group in groups.values():
        total = sum(float(r["amount"]) for r in group if r["bank_account"])
        if (group[0]["date"], f"{total:.2f}") in existing_jes:
            for row in group:
                row["status"] = "skip_duplicate"
                row["note"] = "JE with same date+total already in QuickBooks"

    for row in rows:
        if row["txn_type"] in ("JournalEntry", "JournalEntryLine"):
            continue
        acct = row["bank_account"] or row["account"]
        if (row["date"], row["amount"], acct) in existing:
            row["status"] = "skip_duplicate"
            row["note"] = "same date+amount+account already in QuickBooks"


def mark_transfers(rows: list[dict]) -> None:
    """Skip the receiving side of transfers we book from the sending side."""
    # Mercury deposits that are the receiving side of a Schwab wire
    wires = [r for r in rows if r["txn_type"] == "JournalEntry" and r["account"] == SCHWAB_CASH]
    for wire in wires:
        wdate = datetime.fromisoformat(wire["date"])
        for row in rows:
            if (
                row["txn_type"] == "Deposit"
                and row["status"] in ("ready", "review")
                and abs(float(row["amount"]) - float(wire["amount"])) < 1.00
                and abs((datetime.fromisoformat(row["date"]) - wdate).days) <= 5
            ):
                row["status"] = "skip_transfer"
                row["note"] = f"receiving side of Schwab wire {wire['date']}"
                break

    # Card payments: the checking side books a Check against the card account;
    # the card statement's matching CreditCardCredit is the same money.
    payments = [
        r for r in rows
        if r["txn_type"] == "Check" and r["account"] in CARD_ACCOUNTS and r["status"] == "ready"
    ]
    for pay in payments:
        pdate = datetime.fromisoformat(pay["date"])
        for row in rows:
            if (
                row["txn_type"] == "CreditCardCredit"
                and row["status"] in ("ready", "review")
                and row["bank_account"] == pay["account"]
                and abs(float(row["amount"]) - float(pay["amount"])) < 0.005
                and abs((datetime.fromisoformat(row["date"]) - pdate).days) <= 5
            ):
                row["status"] = "skip_transfer"
                row["note"] = f"card side of payment from checking {pay['date']}"
                break


COLUMNS = [
    "status", "date", "txn_type", "bank_account", "payee", "account",
    "amount", "memo", "note", "source", "source_ref",
]


def _inputs(directory: Path, pattern: str) -> list[Path]:
    """Glob, skipping macOS AppleDouble sidecars (._name) — they are binary."""
    return [p for p in directory.glob(pattern) if not p.name.startswith("._")]


def main() -> None:
    data_dir = Path(sys.argv[1]) if len(sys.argv) > 1 else REPO / "InstitutionData"
    out_path = Path(sys.argv[2]) if len(sys.argv) > 2 else REPO / "staging" / "upload.csv"
    out_path.parent.mkdir(parents=True, exist_ok=True)

    rules = load_rules()
    known_accounts = load_known_accounts()
    existing = load_existing_keys()
    print(f"{len(rules)} rules, {len(existing)} existing QB transactions for dedup")

    rows: list[dict] = []
    for f in sorted(_inputs(data_dir / "transactions", "*.csv")):
        parsed = parse_mercury(f, rules) if "mercury" in f.name.lower() else parse_schwab(f, rules)
        print(f"{f.name}: {len(parsed)} rows")
        rows.extend(parsed)
    for f in sorted(_inputs(data_dir / "payroll", "*.xlsx")):
        parsed = parse_adp_workbook(f, known_accounts)
        print(f"{f.name}: {len(parsed)} JE lines")
        rows.extend(parsed)

    mark_transfers(rows)
    mark_duplicates(rows, existing)
    rows.sort(key=lambda r: (r["date"], r["source"], r["txn_type"]))

    with out_path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=COLUMNS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)

    by_status = {}
    for r in rows:
        by_status[r["status"]] = by_status.get(r["status"], 0) + 1
    print(f"\n{out_path}: {len(rows)} rows -> {by_status}")
    print("Review the file (especially status=review), then run post_upload.py")


if __name__ == "__main__":
    main()
