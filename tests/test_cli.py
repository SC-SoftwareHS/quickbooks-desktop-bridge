from quickbooks.cli import _format_table, main

REMOTE_TRIAL_BALANCE = {
    "title": "Trial Balance",
    "subtitle": "As of September 15, 2026",
    "basis": "Accrual",
    "columns": [
        {"col_id": 1, "col_type": "Label", "titles": ["", ""]},
        {"col_id": 2, "col_type": "Amount", "titles": ["Sep 15, 26", "Debit"]},
        {"col_id": 3, "col_type": "Amount", "titles": ["Sep 15, 26", "Credit"]},
    ],
    "rows": [
        {"kind": "data", "row_value": "Checking", "cells": {"1": "Checking", "2": "48.00"}},
        {"kind": "data", "row_value": "Capital Stock", "cells": {"1": "Capital Stock", "3": "48.00"}},
        {"kind": "total", "cells": {"1": "TOTAL", "2": "48.00", "3": "48.00"}},
    ],
}


def test_table_format_renders_remote_report_json():
    out = _format_table(REMOTE_TRIAL_BALANCE)
    assert not out.lstrip().startswith("{")
    assert "Sep 15, 26 / Debit" in out
    lines = out.splitlines()
    checking = next(line for line in lines if line.startswith("Checking"))
    # Empty Credit cell must stay blank, not repeat the account name.
    assert checking.count("Checking") == 1
    assert "TOTAL" in out


def test_company_dry_run(capsys):
    assert main(["company", "--dry-run"]) == 0
    out = capsys.readouterr().out
    assert "<CompanyQueryRq" in out
    assert "<?qbxml version=" in out


def test_transactions_dry_run(capsys):
    assert main(["transactions", "--from", "2026-01-01", "--to", "2026-12-31", "--dry-run"]) == 0
    out = capsys.readouterr().out
    assert "<FromTxnDate>2026-01-01</FromTxnDate>" in out


def test_diagnose_on_macos_does_not_crash(capsys):
    rc = main(["diagnose"])
    assert rc in (0, 1)
    out = capsys.readouterr().out
    assert "python" in out.lower() or "platform" in out.lower() or "checks" in out.lower()
