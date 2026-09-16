"""Parse QuickBooks general-summary report responses."""

from __future__ import annotations

import xml.etree.ElementTree as ET

from .models import Report, ReportColumn, ReportRow


def _attr(el: ET.Element, name: str, default: str | None = None) -> str | None:
    value = el.attrib.get(name)
    if value is None or value == "":
        return default
    return value


def parse_report(report_ret: ET.Element) -> Report:
    columns: list[ReportColumn] = []
    for col in report_ret.findall("ColDesc"):
        col_id = int(_attr(col, "colID") or "0")
        titles = []
        for title in col.findall("ColTitle"):
            titles.append(_attr(title, "value") or "")
        columns.append(
            ReportColumn(
                col_id=col_id,
                col_type=_text_child(col, "ColType"),
                data_type=_attr(col, "dataType"),
                titles=titles,
            )
        )

    rows: list[ReportRow] = []
    data = report_ret.find("ReportData")
    if data is not None:
        for child in list(data):
            rows.append(_parse_row(child))

    return Report(
        title=_text_child(report_ret, "ReportTitle"),
        subtitle=_text_child(report_ret, "ReportSubtitle"),
        basis=_text_child(report_ret, "ReportBasis"),
        num_rows=_int_child(report_ret, "NumRows"),
        num_columns=_int_child(report_ret, "NumColumns"),
        columns=columns,
        rows=rows,
    )


def _text_child(el: ET.Element, tag: str) -> str | None:
    child = el.find(tag)
    if child is None or child.text is None:
        return None
    value = child.text.strip()
    return value or None


def _int_child(el: ET.Element, tag: str) -> int | None:
    value = _text_child(el, tag)
    if value is None:
        return None
    try:
        return int(value)
    except ValueError:
        return None


def _parse_row(el: ET.Element) -> ReportRow:
    kind = {
        "DataRow": "data",
        "SubtotalRow": "subtotal",
        "TotalRow": "total",
        "TextRow": "text",
    }.get(el.tag, el.tag)

    row_data = el.find("RowData")
    cells: dict[int, str] = {}
    for col in el.findall("ColData"):
        col_id = int(_attr(col, "colID") or "0")
        cells[col_id] = _attr(col, "value") or ""

    row_number = None
    raw_number = _attr(el, "rowNumber")
    if raw_number:
        try:
            row_number = int(raw_number)
        except ValueError:
            row_number = None

    return ReportRow(
        kind=kind,
        row_type=_attr(row_data, "rowType") if row_data is not None else None,
        row_value=_attr(row_data, "value") if row_data is not None else (_attr(el, "value") if kind == "text" else None),
        row_number=row_number,
        cells=cells,
    )
