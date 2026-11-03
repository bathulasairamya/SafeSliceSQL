"""A self-contained, printable report with simple charts and record evidence."""

import html
from pathlib import Path

from safeslicesql.saved_results import load_summary, read_evidence


def escape(value):
    return html.escape(str(value), quote=True)


def chart_data(summary, evidence=None):
    tables = evidence["tables"] if evidence else summary["tables"]
    original = sum(t["source_rows"] for t in tables)
    copied = (
        sum(t["copy_rows"] for t in tables)
        if all(t["copy_rows"] is not None for t in tables)
        else summary.get("copy_rows")
    )
    relations = [r for t in tables for r in t.get("relationships", [])] if evidence else []
    missing = (
        sum(r["missing_parents"] for r in relations)
        if relations and all(r["missing_parents"] is not None for r in relations)
        else None
    )
    if not evidence and summary.get("validation"):
        missing = summary["validation"].get("fk_violations")
    return {
        "tables": tables,
        "original": original,
        "copied": copied,
        "missing": missing,
        "references": sum(r["checked"] for r in relations) if relations else None,
    }


def bar_svg(items, title):
    maximum = max([value for _, value, _ in items if value is not None] + [1])
    height = 65 + 42 * len(items)
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 900 {height}" role="img" aria-label="{escape(title)}">',
        f'<text x="16" y="26" font-size="20" font-weight="bold">{escape(title)}</text>',
    ]
    for index, (label, value, color) in enumerate(items):
        y = 55 + index * 42
        parts.append(f'<text x="16" y="{y + 18}" font-size="15">{escape(label)}</text>')
        if value is not None:
            width = max(0, value / maximum * 480)
            parts.append(
                f'<rect x="290" y="{y}" width="{width}" height="25" rx="4" fill="{color}"/>'
            )
        parts.append(
            f'<text x="785" y="{y + 18}" font-size="15">{value:,}</text>'
            if value is not None
            else f'<text x="785" y="{y + 18}">Not recorded</text>'
        )
    return "".join(parts) + "</svg>"


def build_report(summary, evidence=None):
    data = chart_data(summary, evidence)

    def table(headers, rows):
        return (
            "<table><thead><tr>"
            + "".join(f"<th>{escape(h)}</th>" for h in headers)
            + "</tr></thead><tbody>"
            + "".join(
                "<tr>" + "".join(f"<td>{escape(v)}</td>" for v in row) + "</tr>" for row in rows
            )
            + "</tbody></table>"
        )

    parts = [
        '<!doctype html><html lang="en"><meta charset="utf-8"><title>SafeSliceSQL copy report</title>',
        "<style>body{font:16px/1.6 system-ui,sans-serif;color:#18243c;background:#f4f6fa;margin:0}main{max-width:1000px;margin:30px auto;padding:32px;background:white}h1{font-size:32px}h2{margin-top:34px;color:#2456d9}table{border-collapse:collapse;width:100%;margin:16px 0;overflow-wrap:anywhere}th,td{padding:10px;border:1px solid #dbe2ef;text-align:left}th{background:#edf2fc}.note{padding:16px;background:#edf2fc}svg{width:100%;font-family:system-ui,sans-serif} @media print{body{background:white}main{margin:0;padding:0}tr,svg{break-inside:avoid}h2{break-after:avoid}}</style><main>",
        "<h1>Your smaller copy — verification report</h1>",
        f"<p>Original: <b>{escape(summary['source'])}</b><br>Copy: <b>{escape(summary['target'])}</b><br>Copy run recorded: {escape(summary['created_at'])}</p>",
        f"<p>Run result: <b>{'Accepted' if summary['ready'] else 'Needs attention'}</b>. Method: {escape(summary.get('method'))}. Requested size: {escape(summary.get('budget'))}%.</p>",
        '<p class="note">This saved report describes the recorded inspection, not future database contents. Use Verify again for a fresh check. Use your browser’s Print / Save as PDF to share a PDF.</p>',
        "<h2>How much data was kept?</h2>",
        bar_svg(
            [
                ("Original records", data["original"], "#8294b4"),
                ("Copied records", data["copied"], "#2456d9"),
            ],
            "Original and copy size",
        ),
        bar_svg(
            [(t["table"], t["copy_rows"], "#2456d9") for t in data["tables"]],
            "Records kept per table",
        ),
        table(
            ["Table", "Original records", "Copied records"],
            [
                (
                    t["table"],
                    t["source_rows"],
                    t["copy_rows"] if t["copy_rows"] is not None else "Not recorded",
                )
                for t in data["tables"]
            ],
        ),
        "<h2>Why are some records absent?</h2><p>A smaller copy deliberately contains only part of the original. An unselected record is different from a broken connection: a broken connection means a retained record needs a related record that is absent. Individual selection reasons are not recorded. A later missing record could also reflect edits after creation.</p>",
    ]
    if summary.get("error"):
        parts.append(f'<p class="note">Run details: {escape(summary["error"])}</p>')
    validation = summary.get("validation")
    if validation:
        parts += [
            "<h2>Checks at creation</h2>",
            table(
                ["Check", "Recorded result"],
                [
                    ("Unique record IDs", validation.get("pk_unique")),
                    ("Table structure matches", validation.get("schema_compatible")),
                    ("Missing required references", validation.get("fk_violations")),
                    ("Within size limit", validation.get("budget_compliant")),
                    ("Original unchanged (fingerprint)", validation.get("source_unchanged")),
                ],
            ),
        ]
    if evidence:
        parts += [
            f"<h2>Independent record verification</h2><p>Inspected: {escape(evidence['inspected_at'])}. Result: {'All inspected checks passed' if evidence['passed'] else 'Needs attention'}.</p>",
            f"<p>Missing tables: {escape(evidence['missing_tables'])}. Extra tables: {escape(evidence['extra_tables'])}.</p>",
        ]
        if data["missing"] is not None:
            parts.append(
                bar_svg(
                    [("Missing required references", data["missing"], "#c03434")],
                    "Broken connections",
                )
            )
        else:
            parts.append(
                "<p>No declared relationships were checked, or some checks are unavailable. This is not evidence of preserved foreign keys.</p>"
            )
        for t in evidence["tables"]:
            parts += [
                f"<h2>{escape(t['table'])} — evidence</h2>",
                table(
                    ["Check", "Count"],
                    [
                        ("Copied records compared", t["checked_rows"]),
                        ("IDs missing from original", t["missing_originals"]),
                        ("Changed records", t["changed_records"]),
                        ("Duplicate ID groups", t["duplicate_key_groups"]),
                    ],
                ),
            ]
            parts.append(
                f"<p>Record ID columns: {escape(t['source_keys'])} → {escape(t['copy_keys'])}. Structure differences: {escape(t['schema_differences'])}.</p>"
            )
            if not t["copy_rows"]:
                parts.append("<p>Empty copy table: no records to demonstrate.</p>")
            for r in t["relationships"]:
                parts.append(
                    f"<p><b>{escape(t['table'])}.{escape(r['from'])} → {escape(r['parent_table'])}.{escape(', '.join(r['parent_columns']))}</b><br>References checked: {r['checked']:,}; missing parents: {escape(r['missing_parents'] if r['missing_parents'] is not None else 'Not verified')}.</p>"
                )
            parts.append(
                "<p>Examples below are illustrative; counts above cover every copied record checked.</p>"
            )
            for example in t["examples"]:
                original, copy = example["original"], example["copy"]
                parts.append(
                    table(
                        ["Field", "Original value", "Copied value"],
                        [
                            (k, original.get(k) if original is not None else "MISSING", copy.get(k))
                            for k in dict.fromkeys([*(original or {}), *copy])
                        ],
                    )
                )
    else:
        parts.append(
            "<h2>Record evidence</h2><p>No independent verification snapshot saved yet. Use Verify my copy to inspect records and connections, then export again.</p>"
        )
    if summary.get("file_input"):
        parts.append(
            '<p class="note">CSV/Excel inputs use generated record IDs and do not declare original database foreign keys.</p>'
        )
    return "\n".join(parts) + "</main></html>"


def export_report(folder, destination):
    destination = Path(destination)
    destination.write_text(
        build_report(load_summary(folder), read_evidence(folder)), encoding="utf-8"
    )
    return destination
