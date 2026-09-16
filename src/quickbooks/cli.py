"""qbctl — read-only QuickBooks Desktop CLI."""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urljoin
from urllib.request import Request, urlopen

from . import qbxml
from .connection import APP_NAME_DEFAULT, FILE_MODES, QuickBooksConnection
from .diagnose import format_diagnose, run_diagnose
from .errors import QBError
from .models import Report, ReportColumn, ReportRow, to_dict
from .service import QuickBooksService


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except QBError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        return 130


def _common_flags() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--url", default=os.environ.get("QBCTL_URL"), help="Remote bridge API (http://qb-vm:8765)")
    common.add_argument("--token", default=os.environ.get("QBCTL_TOKEN"), help="Bearer token for the remote API")
    common.add_argument("--app-name", default=os.environ.get("QBCTL_APP_NAME", APP_NAME_DEFAULT))
    common.add_argument("--company-file", default=os.environ.get("QBCTL_COMPANY_FILE", ""), help="Path to .QBW; empty = currently open file")
    common.add_argument(
        "--file-mode",
        choices=tuple(FILE_MODES),
        default=os.environ.get("QBCTL_FILE_MODE", "do-not-care"),
        help="How QuickBooks opens --company-file when qbctl has to open it",
    )
    common.add_argument("--qbxml-version", default=os.environ.get("QBCTL_QBXML_VERSION", qbxml.DEFAULT_QBXML_VERSION))
    common.add_argument("--format", choices=("json", "table", "xml"), default="json")
    common.add_argument("--dry-run", action="store_true", help="Print qbXML and exit (no COM / no HTTP)")
    return common


def _build_parser() -> argparse.ArgumentParser:
    common = _common_flags()
    parser = argparse.ArgumentParser(
        prog="qbctl",
        description="Read-only QuickBooks Desktop 2021 bridge (qbXML / QBXMLRP2).",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("diagnose", parents=[common], help="Inspect SDK / COM / Python / QuickBooks process")
    p.set_defaults(func=cmd_diagnose, xml_builder=None)

    p = sub.add_parser("host", parents=[common], help="HostQuery — QuickBooks product and supported qbXML versions")
    p.set_defaults(func=cmd_host, xml_builder=lambda a: qbxml.host_query(version=a.qbxml_version))

    p = sub.add_parser("company", parents=[common], help="CompanyQuery — currently open company file")
    p.set_defaults(func=cmd_company, xml_builder=lambda a: qbxml.company_query(version=a.qbxml_version))

    p = sub.add_parser("accounts", parents=[common], help="AccountQuery — chart of accounts")
    p.add_argument("--active-status", default="All", choices=("All", "ActiveOnly", "InactiveOnly"))
    p.set_defaults(
        func=cmd_accounts,
        xml_builder=lambda a: qbxml.account_query(
            active_status=a.active_status, iterator=None, version=a.qbxml_version
        ),
    )

    p = sub.add_parser("transactions", parents=[common], help="TransactionQuery — postings in a date range")
    p.add_argument("--from", dest="from_date", required=True, metavar="YYYY-MM-DD")
    p.add_argument("--to", dest="to_date", required=True, metavar="YYYY-MM-DD")
    p.set_defaults(
        func=cmd_transactions,
        xml_builder=lambda a: qbxml.transaction_query(
            from_date=a.from_date, to_date=a.to_date, iterator=None, version=a.qbxml_version
        ),
    )

    p = sub.add_parser("trial-balance", parents=[common], help="TrialBalance general-summary report")
    p.add_argument("--date", required=True, metavar="YYYY-MM-DD")
    p.add_argument("--basis", choices=("Accrual", "Cash"), default=None)
    p.set_defaults(
        func=cmd_trial_balance,
        xml_builder=lambda a: qbxml.trial_balance_query(
            as_of=a.date, report_basis=a.basis, version=a.qbxml_version
        ),
    )

    p = sub.add_parser("profit-loss", parents=[common], help="ProfitAndLossStandard general-summary report")
    p.add_argument("--from", dest="from_date", required=True, metavar="YYYY-MM-DD")
    p.add_argument("--to", dest="to_date", required=True, metavar="YYYY-MM-DD")
    p.add_argument("--basis", choices=("Accrual", "Cash"), default=None)
    p.set_defaults(
        func=cmd_profit_loss,
        xml_builder=lambda a: qbxml.profit_loss_query(
            from_date=a.from_date, to_date=a.to_date, report_basis=a.basis, version=a.qbxml_version
        ),
    )

    p = sub.add_parser("raw", parents=[common], help="Send a qbXML file (writes need --allow-writes)")
    p.add_argument("--file", required=True)
    p.add_argument("--allow-writes", action="store_true", help="Permit add qbXML")
    p.add_argument("--allow-destructive", action="store_true", help="Also permit mod/del/void qbXML")
    p.set_defaults(func=cmd_raw, xml_builder=None)

    p = sub.add_parser("serve", parents=[common], help="Start the local HTTP API (Windows)")
    p.add_argument("--host", default=os.environ.get("QBCTL_HOST", "127.0.0.1"))
    p.add_argument("--port", type=int, default=int(os.environ.get("QBCTL_PORT", "8765")))
    p.add_argument("--tailscale", action="store_true", help="Bind to this machine's Tailscale IPv4 address")
    p.add_argument(
        "--allow-writes",
        action="store_true",
        default=os.environ.get("QBCTL_ALLOW_WRITES", "").lower() in ("1", "true", "yes"),
        help="Permit add qbXML through the API (default: read-only)",
    )
    p.add_argument(
        "--allow-destructive",
        action="store_true",
        default=os.environ.get("QBCTL_ALLOW_DESTRUCTIVE", "").lower() in ("1", "true", "yes"),
        help="Also permit mod/del/void qbXML (implies --allow-writes)",
    )
    p.set_defaults(func=cmd_serve, xml_builder=None)

    return parser


def cmd_diagnose(args: argparse.Namespace) -> int:
    if args.url:
        _emit(args, _remote(args, "GET", "/diagnose"))
        return 0
    result = run_diagnose()
    if args.format == "json":
        _print_json(result)
    else:
        print(format_diagnose(result))
    return 0 if result.get("ready_for_session") or not result.get("windows") else 1


def cmd_host(args: argparse.Namespace) -> int:
    return _run_local_or_remote(args, "/host", lambda svc: svc.host())


def cmd_company(args: argparse.Namespace) -> int:
    return _run_local_or_remote(args, "/company", lambda svc: svc.company())


def cmd_accounts(args: argparse.Namespace) -> int:
    return _run_local_or_remote(
        args,
        "/accounts",
        lambda svc: svc.accounts(active_status=args.active_status),
        query={"active_status": args.active_status},
    )


def cmd_transactions(args: argparse.Namespace) -> int:
    return _run_local_or_remote(
        args,
        "/transactions",
        lambda svc: svc.transactions(from_date=args.from_date, to_date=args.to_date),
        query={"from": args.from_date, "to": args.to_date},
    )


def cmd_trial_balance(args: argparse.Namespace) -> int:
    return _run_local_or_remote(
        args,
        "/reports/trial-balance",
        lambda svc: svc.trial_balance(as_of=args.date, report_basis=args.basis),
        query={"date": args.date, "basis": args.basis or ""},
    )


def cmd_profit_loss(args: argparse.Namespace) -> int:
    return _run_local_or_remote(
        args,
        "/reports/profit-loss",
        lambda svc: svc.profit_loss(from_date=args.from_date, to_date=args.to_date, report_basis=args.basis),
        query={"from": args.from_date, "to": args.to_date, "basis": args.basis or ""},
    )


def cmd_raw(args: argparse.Namespace) -> int:
    document = Path_read(args.file)
    if args.dry_run:
        print(document)
        return 0
    if args.url:
        _emit(args, _remote(args, "POST", "/query", body=document.encode("utf-8"), content_type="application/xml"))
        return 0
    allow_writes = args.allow_writes or args.allow_destructive
    with _connection(args, read_only=not allow_writes, allow_destructive=args.allow_destructive) as conn:
        response = conn.process(document)
    if args.format == "xml":
        print(response)
    else:
        _print_json({"response_xml": response})
    return 0


def cmd_serve(args: argparse.Namespace) -> int:
    host = args.host
    if args.tailscale:
        host = _tailscale_ipv4()
    try:
        from .api import run_server
    except ImportError:
        print("error: pip install fastapi uvicorn", file=sys.stderr)
        return 1
    run_server(
        host=host,
        port=args.port,
        token=args.token,
        app_name=args.app_name,
        company_file=args.company_file,
        qbxml_version=args.qbxml_version,
        allow_writes=args.allow_writes or args.allow_destructive,
        allow_destructive=args.allow_destructive,
        file_mode=FILE_MODES[args.file_mode],
    )
    return 0


def _run_local_or_remote(args, path, local_call, query=None) -> int:
    if args.dry_run:
        builder = getattr(args, "xml_builder", None)
        if builder is None:
            print("error: --dry-run is not available for this command", file=sys.stderr)
            return 2
        print(builder(args))
        return 0
    if args.url:
        _emit(args, _remote(args, "GET", path, query=query))
        return 0
    with _connection(args) as conn:
        service = QuickBooksService(conn, qbxml_version=args.qbxml_version)
        result = local_call(service)
    _emit(args, result)
    return 0


def _connection(
    args: argparse.Namespace, read_only: bool = True, allow_destructive: bool = False
) -> QuickBooksConnection:
    return QuickBooksConnection(
        app_name=args.app_name,
        company_file=args.company_file,
        file_mode=FILE_MODES[args.file_mode],
        read_only=read_only,
        allow_destructive=allow_destructive,
    )


def _emit(args: argparse.Namespace, payload: Any) -> None:
    if args.format == "xml" and isinstance(payload, str):
        print(payload)
        return
    if args.format == "table":
        print(_format_table(payload))
        return
    _print_json(to_dict(payload))


def _print_json(payload: Any) -> None:
    print(json.dumps(payload, indent=2, default=str))


def _format_table(payload: Any) -> str:
    if isinstance(payload, Report):
        return _format_report(payload)
    data = to_dict(payload)
    # Remote API responses arrive as plain JSON; rebuild reports so they render the same.
    if isinstance(data, dict) and "columns" in data and "rows" in data:
        return _format_report(_report_from_dict(data))
    if isinstance(data, list) and data and isinstance(data[0], dict):
        keys = list(data[0].keys())
        # Flatten one level of refs for a readable table.
        rows = []
        for item in data:
            row = {}
            for k, v in item.items():
                if isinstance(v, dict) and "full_name" in v:
                    row[k] = v.get("full_name") or v.get("list_id")
                else:
                    row[k] = "" if v is None else str(v)
            rows.append(row)
        return _pad_table(keys, rows)
    return json.dumps(data, indent=2, default=str)


def _format_report(report: Report) -> str:
    lines = [report.title or "Report"]
    if report.subtitle:
        lines.append(report.subtitle)
    if report.basis:
        lines.append(f"Basis: {report.basis}")
    lines.append("")
    col_ids = [c.col_id for c in report.columns] or sorted(
        {cid for row in report.rows for cid in row.cells}
    )
    headers = []
    for col in report.columns:
        title = " / ".join(t for t in col.titles if t) or col.col_type or str(col.col_id)
        headers.append(title)
    if not headers:
        headers = [str(cid) for cid in col_ids]
    body = []
    for row in report.rows:
        # Only the label column falls back to row_value; empty amount cells stay blank.
        body.append(
            {
                headers[i]: row.cells.get(col_ids[i], (row.row_value or "") if i == 0 else "")
                for i in range(len(headers))
            }
        )
    return "\n".join(lines + [_pad_table(headers, body)])


def _report_from_dict(data: dict[str, Any]) -> Report:
    return Report(
        title=data.get("title"),
        subtitle=data.get("subtitle"),
        basis=data.get("basis"),
        num_rows=data.get("num_rows"),
        num_columns=data.get("num_columns"),
        columns=[
            ReportColumn(
                col_id=int(c["col_id"]),
                col_type=c.get("col_type"),
                data_type=c.get("data_type"),
                titles=list(c.get("titles") or []),
            )
            for c in data.get("columns") or []
        ],
        rows=[
            ReportRow(
                kind=r.get("kind", "data"),
                row_type=r.get("row_type"),
                row_value=r.get("row_value"),
                row_number=r.get("row_number"),
                cells={int(k): v for k, v in (r.get("cells") or {}).items()},
            )
            for r in data.get("rows") or []
        ],
    )


def _pad_table(keys: list[str], rows: list[dict[str, str]]) -> str:
    widths = {k: len(k) for k in keys}
    for row in rows:
        for k in keys:
            widths[k] = max(widths[k], len(row.get(k, "")))
    header = "  ".join(k.ljust(widths[k]) for k in keys)
    rule = "  ".join("-" * widths[k] for k in keys)
    lines = [header, rule]
    for row in rows:
        lines.append("  ".join(row.get(k, "").ljust(widths[k]) for k in keys))
    return "\n".join(lines)


def _remote(args, method: str, path: str, query=None, body: bytes | None = None, content_type: str | None = None) -> Any:
    if not args.url:
        raise QBError("No --url / QBCTL_URL")
    url = urljoin(args.url.rstrip("/") + "/", path.lstrip("/"))
    if query:
        filtered = {k: v for k, v in query.items() if v not in (None, "")}
        if filtered:
            url += "?" + urlencode(filtered)
    headers = {"Accept": "application/json"}
    if args.token:
        headers["Authorization"] = f"Bearer {args.token}"
    if content_type:
        headers["Content-Type"] = content_type
    req = Request(url, data=body, method=method, headers=headers)
    try:
        with urlopen(req, timeout=120) as resp:
            raw = resp.read().decode("utf-8")
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise QBError(f"HTTP {exc.code} from {url}: {detail}") from exc
    except URLError as exc:
        raise QBError(f"Could not reach {url}: {exc.reason}") from exc
    if not raw:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return raw


def _tailscale_ipv4(wait_seconds: float = 120.0) -> str:
    """This machine's Tailscale IPv4, waiting for Tailscale to come up (e.g. right after logon)."""
    import shutil
    import subprocess
    import time

    ts = shutil.which("tailscale")
    if not ts:
        raise QBError("tailscale not on PATH")
    deadline = time.monotonic() + wait_seconds
    while True:
        completed = subprocess.run([ts, "ip", "-4"], capture_output=True, text=True, check=False)
        lines = (completed.stdout or "").strip().splitlines()
        if completed.returncode == 0 and lines and lines[0].strip():
            return lines[0].strip()
        if time.monotonic() >= deadline:
            detail = (completed.stderr or completed.stdout or "").strip()
            raise QBError(f"Could not determine Tailscale IPv4 address: {detail}")
        time.sleep(5)


def Path_read(path: str) -> str:
    from pathlib import Path

    return Path(path).read_text(encoding="utf-8")


if __name__ == "__main__":
    sys.exit(main())
