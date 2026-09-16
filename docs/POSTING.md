# Posting transactions into QuickBooks on the Windows VM

End-to-end runbook for the bank-import pipeline: getting Mercury / Schwab /
ADP data out of CSV and Excel exports and into QuickBooks Desktop Pro 2021
running on a Windows host. Everything except the HTTP server runs on the
client (any machine on your network with this repo works). The server on
Windows is the only thing that touches QuickBooks.

```
client (~/quickbooks)
  export_csv.py        pull current QB state → exports/*.csv   (feeds dedup)
  build_upload.py      InstitutionData/* + rules → staging/upload.csv
  [human review]       fix status=review rows
  post_upload.py       staging/upload.csv → POST /query
        │
        ▼  HTTP over Tailscale (bearer token)
Windows host (QB-VM)
  "qbctl API" logon task → start-server.ps1 → qbctl serve --tailscale --allow-writes   :8765
        │
        ▼  qbXML via 32-bit SysWOW64 PowerShell COM bridge (com_bridge.ps1)
  QBXMLRP2.RequestProcessor → QuickBooks Desktop 2021 → .QBW file
```

## 1. Prerequisites

### Windows host

- QuickBooks Desktop 2021 must be **open with the company file loaded** by
  the Windows user the server runs as. `/health` responds regardless, but every real call needs the
  session. Keep the company file on the VM's local disk (`Documents`):
  QuickBooks cannot open it from a OneDrive folder (error -6123).
- The qbctl Application Certificate must be approved in QuickBooks
  ("Yes, whenever QuickBooks is running"). The first request after a fresh
  install makes QuickBooks show that dialog; until someone approves it,
  requests fail with "Timed out opening a QuickBooks session".
- The repo lives at `C:\Users\<user>\quickbooks`, **not OneDrive**
  (placeholder sync once silently dropped `.py` files). Python is 64-bit
  3.13 in `.venv`. QBXMLRP2 is registered 32-bit only, so qbctl reaches it
  through the 32-bit PowerShell bridge automatically; no x86 Python or
  pywin32 is needed.
- The server runs from the **"qbctl API" scheduled task** at that user's
  logon, inside their session: the SDK cannot reach QuickBooks from
  SYSTEM or another session. `scripts\start-server.ps1` restarts it whenever
  it exits and logs to `logs\`. It runs **add-only**, the intended mode for
  imports. One-time setup, in an elevated PowerShell:

```powershell
cd C:\Users\<user>\quickbooks
& "C:\Program Files\Python313\python.exe" -m venv .venv
.venv\Scripts\python -m pip install -e ".[api]"
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\install-server-task.ps1 -User QB-VM\<windows-user>
Start-ScheduledTask -TaskName "qbctl API"
```

- After a restart (for example Windows Update overnight), Windows can sign the
  user in automatically (Sysinternals Autologon), the "qbctl API" task starts,
  and the console locks itself shortly afterwards. The task passes `-CompanyFile` and
  `-FileMode single-user`, and QuickBooks' database service (`QuickBooksDB31`)
  starts automatically at boot (only administrators may start it, so otherwise
  an unattended open stalls on a UAC prompt). The first request therefore
  opens the company file without the QuickBooks window, with UAC at its
  default —
  provided qbctl is allowed to log in automatically (QuickBooks: Edit →
  Preferences → Integrated Applications → Company Preferences → qbctl →
  Properties). Without that permission, requests fail until someone signs in
  and opens the company file in QuickBooks.
- Start QuickBooks normally, never "Run as administrator": qbctl runs without
  elevation and cannot attach to an elevated QuickBooks (it times out opening
  a session instead).

### Token

The token is pinned in `C:\ProgramData\qbctl\token` (readable only by the
server's user and administrators), so it survives server restarts. The client
keeps a copy, with the URL, in `~/.config/qbctl/env` (mode 600). To rotate it,
delete the token file, rerun `install-server-task.ps1`, restart the task, and
copy the new value to the client.

### Client

```bash
cd ~/quickbooks
source .venv/bin/activate
set -a; . ~/.config/qbctl/env; set +a     # QBCTL_URL=http://qb-vm:8765, QBCTL_TOKEN=...
```

Sanity check — `/health` also reports which write mode the server is in
(`read-only` / `add-only` / `read-write`):

```bash
curl -s $QBCTL_URL/health
qbctl company
```

Note: CLI flags go **after** the subcommand (`qbctl company --url ...`);
the usage hint that `serve` prints has them in the wrong order.

### Configuration

`scripts/build_upload.py` reads `config.toml` for your chart-of-accounts names
and the recurring-wire amount. Copy the template and edit it before the first
run; it is gitignored:

```bash
cp config.example.toml config.toml
$EDITOR config.toml
```

Account names must match QuickBooks exactly — see the apostrophe gotcha in §4.
These scripts need Python 3.11+ for `tomllib`.

## 2. The pipeline, step by step

### Step 1 — Export current QuickBooks state

```bash
python scripts/export_csv.py
```

Writes `exports/accounts.csv`, `transactions.csv`, `journal_entries.csv`,
`journal_entry_lines.csv`, `trial_balance.csv`. Date range defaults to
2017-01-01..today; override with `QBCTL_FROM` / `QBCTL_TO`.

**Always rerun this before building an upload** — these files are what
`build_upload.py` dedups against. A stale export means duplicates get
posted.

### Step 2 — Build the staging file

Drop fresh institution exports into `InstitutionData/`:

| Input | What it is |
|---|---|
| `InstitutionData/transactions/Mercury*.csv` | Mercury bank export. "Source Account" values match QB account names exactly. Rows with Status ≠ Sent (Pending/Failed) are skipped. |
| `InstitutionData/transactions/Schwab*.csv` | Schwab bank export. The "as of" date is used when present. |
| `InstitutionData/payroll/*.xlsx` | ADP "GL package" workbooks — one sheet per pay run, each a ready-made balanced JE using QB account names. |
| `imports/rules.csv` | payee → expense account rules (mine these from your own history). Longest match wins. |

```bash
python scripts/build_upload.py            # → staging/upload.csv
```

Every output row gets a `status`:

| status | meaning |
|---|---|
| `ready` | Safe to post as-is. |
| `review` | Needs a human: fill in `account`, flip status to `ready`. |
| `skip_duplicate` | Already in QuickBooks or already in `imports/posted.csv`. Left in the file for audit; never posted. |
| `skip_transfer` | The receiving side of a transfer that is booked from the sending side (Schwab wire → Mercury deposit; checking → card payment). Never posted. |

How rows map to QuickBooks transactions:

- Mercury bank account: negative → `Check`, positive → `Deposit`.
- Mercury Credit (card): negative → `CreditCardCharge`, positive →
  `CreditCardCredit`.
- Schwab rows become `JournalEntry`, using the account names from
  `config.toml`: Bank Interest debits `brokerage_cash` / credits
  `interest_income`; Wire Sent debits `operating_checking` / credits
  `brokerage_cash` (the matching deposit row on the bank side gets
  `skip_transfer`); Wire Received debits `brokerage_cash` / credits
  `client_income`.

  That last one matters: if client fees land in the brokerage account and are
  then wired on to the operating account, skipping the receiving side leaves
  the income unrecorded and drives the brokerage balance negative. Only a wire
  matching `wires.recognized_amount` is marked `ready`; every other incoming
  wire is `review` with `client_income` prefilled — confirm it isn't a loan or
  a capital contribution first. Leave `recognized_amount = ""` to review all
  of them.
- ADP sheets become `JournalEntryLine` rows, one per debit/credit line,
  grouped by source sheet into a single balanced JE at post time.

Dedup logic: bank/card rows match on (date, unsigned amount, account)
against `exports/transactions.csv` + `imports/posted.csv`. Journal entries
match on (date, total debits) against `exports/journal_entries.csv` —
whole-entry, not per-line, so regrouped lines still dedup.

### Step 3 — Review

Open `staging/upload.csv`. For each `status=review` row, set the `account`
column and flip `status` to `ready`. The `note` column says why the row
needs attention. Rows left in `review` are simply not posted — no need to
delete them.

### Step 4 — Dry run, then post

```bash
python scripts/post_upload.py staging/upload.csv           # dry run: previews qbXML, posts nothing
python scripts/post_upload.py staging/upload.csv --post    # actually writes
```

What `--post` does, in order:

1. Filters to `status=ready` rows whose key
   (`source|source_ref|date|amount|txn_type`) is **not** already in
   `imports/posted.csv` — reruns never double-post.
2. Groups `JournalEntryLine` rows by source sheet and refuses to run if any
   group's debits ≠ credits.
3. Queries Vendors/Customers/Employees/OtherNames and auto-creates missing
   payees as **Vendors** (error 3100 "already exists" is tolerated).
4. Posts each transaction as one qbXML `*AddRq` via `POST /query`,
   printing `ok <label> -> <TxnID>` or `FAIL <label> -> <code> <msg>`.
5. Appends every successful row to `imports/posted.csv` with its QuickBooks
   `TxnID` — this is both the idempotency log and the undo map (a bad run
   can be located and deleted in QuickBooks by TxnID).

Failures don't stop the run; failed rows stay unposted and will be picked
up on the next `--post` after you fix the cause.

### Step 5 — Verify

Re-run the export and confirm the new transactions and the trial balance:

```bash
python scripts/export_csv.py
qbctl trial-balance --date $(date +%Y-%m-%d) --url $QBCTL_URL --token $QBCTL_TOKEN
```

## 3. Safety model

Three server modes, each a superset of the last, enforced at **both** the
API layer and the COM layer:

| mode | flag | permits |
|---|---|---|
| read-only | (default) | queries only |
| add-only | `--allow-writes` / `QBCTL_ALLOW_WRITES=1` | queries + `*AddRq` |
| read-write | `--allow-destructive` / `QBCTL_ALLOW_DESTRUCTIVE=1` | everything incl. mod/del/void |

Add-only is the intended mode for this pipeline: the worst possible outcome
of a bad run is duplicate entries (findable by TxnID in
`imports/posted.csv`), never rewritten or deleted history. Don't run the
server with `--allow-destructive` for imports.

## 4. Gotchas

- **QB error 3180 / Accountant's Copy**: while an Accountant's Copy is out,
  QuickBooks freezes all transactions on or before the dividing date — any
  `*AddRq` dated on/before it fails with 3180. Wait until the accountant's
  changes are imported, then rerun `post_upload.py --post`; the idempotency
  log means only the blocked rows go through.
- **HTTP 401**: the client's token doesn't match `C:\ProgramData\qbctl\token`
  on the Windows host. Copy the host's value into `~/.config/qbctl/env`.
- **Connection refused / timeout**: check in order — the user signed in on the
  Windows host, the "qbctl API" task running (`Get-ScheduledTask 'qbctl API'`;
  logs in `C:\Users\<user>\quickbooks\logs`), Tailscale up on both ends, the
  "qbctl API (Tailscale)" firewall rule present.
- **HTTP 502 "Timed out opening a QuickBooks session"**: QuickBooks is
  waiting on its Application Certificate dialog, or no company file is open.
  After QuickBooks restarts, the server opens a fresh session on the next
  request by itself. A write that failed during the restart is not retried;
  `post_upload.py` stops and tells you which row to check.
- **qbXML quirks** (relevant if editing the scripts):
  `AccountQueryRq` and the unified `TransactionQueryRq` accept **no**
  iterator attribute (`JournalEntryQueryRq` does); `TransactionQueryRq`
  uses `TransactionDateRangeFilter`, not `TxnDateRangeFilter`; line detail
  requires `IncludeLineItems=true`; there is no `CompanyMod` — company
  info is editable only in the QuickBooks UI.
- **Name lengths**: payee names are truncated to QuickBooks' 41-char limit,
  memos to 200 chars, by `post_upload.py`.
- **Duplicate account names**: the chart of accounts has had near-duplicate
  names differing only by straight vs curly apostrophe. Account references
  in qbXML are by exact `FullName` — a curly-quote mismatch creates a
  reference error or posts to the wrong account.

## 5. File reference

| Path | Role |
|---|---|
| `InstitutionData/` | Raw inputs (bank CSVs, ADP xlsx). Never modified by scripts. |
| `imports/rules.csv` | payee → account mapping. Grow this over time to shrink the review pile. |
| `imports/posted.csv` | Append-only log of everything posted, keyed for idempotency, with TxnIDs. Do not edit or delete — it's the double-post guard. |
| `exports/*.csv` | Snapshot of QuickBooks state. Regenerate before every build. |
| `staging/upload.csv` | The reviewable staging file. Regenerating overwrites it — finish a review/post cycle before rebuilding. |
