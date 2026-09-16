# qbctl

A bridge for talking to **QuickBooks Desktop** (tested against Pro 2021) from
outside Windows. It hosts the Intuit Desktop SDK
(`QBXMLRP2.RequestProcessor` / qbXML) on the Windows machine where QuickBooks
runs, exposes it as a small authenticated HTTP API, and gives you a CLI that
works from any machine on your network.

**Read-only by default.** The server can only create, modify or delete
QuickBooks data when it is explicitly started in a write mode — see
[Writes](#writes).

It also ships an opinionated [bank-import pipeline](docs/POSTING.md): bank and
payroll CSV/XLSX exports in, deduplicated and human-reviewed journal entries
out, posted into QuickBooks with an append-only idempotency log.

```
any machine (Linux/macOS/Windows)
  → private network (this setup uses Tailscale)
    → Windows host running QuickBooks
      → qbctl HTTP API
        → qbXML
          → 32-bit PowerShell COM bridge
            → QBXMLRP2.RequestProcessor
              → QuickBooks Desktop
                → .QBW company file
```

## Why the PowerShell bridge

`QBXMLRP2` is a **32-bit** COM class, so 64-bit Python cannot instantiate it
in-process (it fails with `0x80040154`, class not registered). qbctl therefore:

1. tries `pywin32` in-process (works only from 32-bit x86 Python), then
2. falls back to **32-bit** `SysWOW64` PowerShell hosting the same COM object.

Path 2 is the one normally in use, and it means you can run ordinary 64-bit
Python on the Windows host with no `pywin32` and no x86 interpreter.

## Reference topology

The setup this was built against, as a concrete example — none of it is
required beyond "QuickBooks runs on Windows, and the client can reach it":

| Item | Example |
|---|---|
| Client | Linux box (Ubuntu 26.04), repo at `~/quickbooks` |
| QuickBooks host | Windows 11 Pro x64 KVM guest, hostname `QB-VM` |
| Network | Tailscale; everything on the tailnet interface firewalled except TCP 8765 |
| Windows Python | 64-bit 3.13 (`C:\Program Files\Python313`), venv at `C:\Users\<user>\quickbooks\.venv` |
| QBXMLRP2 | registered **32-bit only**, `C:\Program Files (x86)\Common Files\Intuit\QuickBooks\QBXMLRP2.dll` |

## Install on the Windows host

Copy `pyproject.toml`, `README.md`, `src/` and `scripts/` to the Windows
machine — `C:\Users\<user>\quickbooks` works. Keep it **out of OneDrive**:
placeholder sync has been observed silently dropping `.py` files, and
QuickBooks cannot open a company file from a OneDrive folder (error -6123).

First, check the environment:

```powershell
cd C:\Users\<user>\quickbooks
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\diagnose.ps1

# and again under the 32-bit host, which is the one that matters:
& "$env:SystemRoot\SysWOW64\WindowsPowerShell\v1.0\powershell.exe" `
  -NoProfile -ExecutionPolicy Bypass -File scripts\diagnose.ps1
```

`diagnose.ps1` answers: is QBXMLRP2 registered? can this process (and 32-bit
PowerShell) `New-Object QBXMLRP2.RequestProcessor`? which Python is installed,
and is pywin32 present? is QuickBooks running? is Tailscale up?

Then:

1. Open QuickBooks Desktop and the company file. Leave it open.
2. If `diagnose.ps1` reports COM missing, install the
   [QuickBooks Desktop SDK](https://developer.intuit.com/app/developer/qbdesktop/docs/get-started/get-started-with-quickbooks-desktop-sdk)
   and/or run `QBXMLRP2Installer.exe`. QuickBooks normally registers it itself.
3. Install 64-bit Python 3.13 for all users from python.org.
4. Create the venv and check the bridge:

```powershell
& "C:\Program Files\Python313\python.exe" -m venv .venv
.venv\Scripts\python -m pip install -e ".[api]"
.venv\Scripts\python -m quickbooks diagnose
.venv\Scripts\python -m quickbooks company
```

The first live `BeginSession` pops QuickBooks' **Application Certificate**
dialog. Approve **qbctl**, preferring *Yes, whenever QuickBooks is running*.

## CLI

```text
qbctl diagnose
qbctl host
qbctl company
qbctl accounts
qbctl transactions --from 2026-01-01 --to 2026-12-31
qbctl trial-balance --date 2026-12-31
qbctl profit-loss --from 2026-01-01 --to 2026-12-31
```

`--format json|table`. `--dry-run` prints the qbXML and never touches
QuickBooks, so it works on any OS. `--company-file` is optional; empty means
"whatever is already open in QuickBooks".

Flags go **after** the subcommand: `qbctl company --url ...`.

## HTTP API

The API must run as the Windows user who has QuickBooks open, **in that user's
session** — the SDK cannot reach QuickBooks from `SYSTEM` or another session.
`install-server-task.ps1` sets that up as a scheduled task that starts at
logon. One-time setup, elevated:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\install-server-task.ps1 -User QB-VM\<windows-user>
Start-ScheduledTask -TaskName "qbctl API"
```

That pins a bearer token in `C:\ProgramData\qbctl\token`, allows inbound TCP
8765 from Tailscale addresses only, and registers the task (add-only mode by
default; `-Mode read-only` to change it). `start-server.ps1` restarts the
server whenever it exits and logs to `logs\`. The server binds the Tailscale
IPv4 address, waiting for Tailscale to come up after logon.

Or run it by hand:

```powershell
.venv\Scripts\python -m quickbooks serve --tailscale --port 8765
```

From a client, with the URL and token in `~/.config/qbctl/env` (mode 600):

```bash
pip install -e .
set -a; . ~/.config/qbctl/env; set +a     # QBCTL_URL, QBCTL_TOKEN
qbctl company
qbctl accounts --format table
qbctl transactions --from 2026-01-01 --to 2026-12-31
```

`GET /health` responds without a QuickBooks session and reports which write
mode the server is in.

If QuickBooks restarts or the company file is closed and reopened, the server
opens a fresh session on the next request. Read requests retry once
automatically; **write requests do not**, because QuickBooks may already have
applied them.

### Running unattended

To serve when nobody has QuickBooks open (for example after an automatic
sign-in), pass `-CompanyFile "C:\Users\<user>\Documents\QuickBooks\<file>.QBW"`
and `-FileMode single-user`, and in QuickBooks allow qbctl to log in
automatically: **Edit → Preferences → Integrated Applications → Company
Preferences → qbctl → Properties → Allow this application to login
automatically**. qbctl then opens the company file itself on the first request.

QuickBooks starts its database service (`QuickBooksDB31`) when qbctl opens a
file, and only administrators may start that service — so an unattended open
would otherwise stall on a UAC prompt. `install-server-task.ps1 -CompanyFile ...`
therefore sets that service to start automatically at boot, which lets UAC stay
at its default setting.

Start QuickBooks **normally, never "Run as administrator"**: qbctl runs
unelevated and cannot attach to an elevated QuickBooks.

## Writes

Three server modes, each a superset of the last, enforced at **both** the API
layer and the COM layer:

| mode | flag | env | permits |
|---|---|---|---|
| read-only | (default) | — | queries only |
| add-only | `--allow-writes` | `QBCTL_ALLOW_WRITES=1` | queries + `*AddRq` |
| read-write | `--allow-destructive` | `QBCTL_ALLOW_DESTRUCTIVE=1` | everything, incl. mod/del/void |

Add-only is the intended mode for bank imports: the worst outcome of a bad run
is duplicate entries — findable by `TxnID` in the posted log — never rewritten
or deleted history.

```powershell
.venv\Scripts\python -m quickbooks serve --tailscale --port 8765 --allow-writes
```

Send write qbXML with the raw command (`raw` also needs `--allow-writes` per
invocation when run locally on Windows):

```bash
qbctl raw --file invoice_add.xml
```

## Bank-import pipeline

Export → build a staging CSV → review by hand → post → verify. Parsers ship
for Mercury and Schwab transaction exports and ADP "GL package" payroll
workbooks; the whole flow is documented in **[docs/POSTING.md](docs/POSTING.md)**.

Copy `config.example.toml` to `config.toml` and set your own account names
before running it — `config.toml` is gitignored, because it describes your
books rather than the tool.

```bash
cp config.example.toml config.toml && $EDITOR config.toml
python scripts/export_csv.py                             # QB state → exports/
python scripts/build_upload.py                           # → staging/upload.csv
$EDITOR staging/upload.csv                               # resolve status=review rows
python scripts/post_upload.py staging/upload.csv         # dry run
python scripts/post_upload.py staging/upload.csv --post  # write
```

The pipeline scripts need Python 3.11+ (`tomllib`); the `quickbooks` package
itself supports 3.9+.

### Your data stays out of git

`InstitutionData/`, `exports/`, `imports/`, `staging/` and `config.toml` are
gitignored. They hold real bank exports, a snapshot of your chart of accounts
and ledger, the staging file under review, and the append-only log of what has
been posted. The directories are kept in the repo (via `.gitkeep`) but their
contents never are.

## Layout

```text
src/quickbooks/
  connection.py   COM lifecycle (OpenConnection2 → BeginSession → ProcessRequest → EndSession → CloseConnection)
  qbxml.py        request construction
  parsers.py      response parsing
  reports.py      report grid parsing
  models.py       dataclasses
  service.py      company / accounts / transactions / reports
  cli.py          qbctl entry point
  api.py          FastAPI app
  diagnose.py     environment inspection
  com_bridge.ps1  32-bit PowerShell COM bridge
scripts/
  diagnose.ps1             environment check (run under both 64- and 32-bit PowerShell)
  install-server-task.ps1  token, firewall rule, "qbctl API" logon task (elevated, one-time)
  start-server.ps1         keeps the API running for the logged-in user
  enable-openssh.ps1       optional: OpenSSH server + Tailscale-only firewall rule
  export_csv.py            QuickBooks → exports/*.csv
  build_upload.py          institution exports + rules → staging/upload.csv
  post_upload.py           staging/upload.csv → QuickBooks, with idempotency log
```

## Tests

```bash
pip install -e ".[dev,api]"
pytest
```

The suite is hermetic — it parses fixture qbXML and never contacts
QuickBooks, so it runs on any OS.

## Caveats

- Built and tested against **QuickBooks Desktop Pro 2021** on Windows 11.
  Other editions speak the same qbXML but are untested here.
- qbXML has no `CompanyMod`: company info (name, address, EIN) is editable
  only in the QuickBooks UI, under Company → My Company.
- Account references in qbXML are by exact `FullName`. A chart of accounts
  with near-duplicate names differing only by a straight vs curly apostrophe
  will produce reference errors or silently post to the wrong account.

## License

[MIT](LICENSE).

Not affiliated with or endorsed by Intuit. QuickBooks is a trademark of Intuit Inc.
