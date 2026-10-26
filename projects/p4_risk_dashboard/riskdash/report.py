"""Export the board pack as a self-contained HTML file (no external assets)."""

from __future__ import annotations

import html
import re

CSS = """
body{font-family:-apple-system,Segoe UI,Helvetica,Arial,sans-serif;color:#1b2430;max-width:900px;margin:32px auto;
padding:0 16px;line-height:1.5;background:#fff}
h1{color:#0b2545;font-size:26px;margin-bottom:4px}h2{color:#13315c;border-bottom:2px solid #e6e9ee;padding-bottom:4px}
h3{margin-bottom:4px}table{border-collapse:collapse;width:100%;font-size:14px}
th,td{border:1px solid #d9dee5;padding:6px 8px;text-align:left;vertical-align:top}th{background:#f3f6fa}
.meta{color:#5b6673;font-size:14px}.stamp{border:2px solid #0ca30c;color:#0a7a0a;padding:8px 12px;border-radius:8px;
display:inline-block;margin:8px 0}.draft{border-color:#fab219;color:#7a5200}em{color:#5b6673}
"""


def _inline(text: str) -> str:
    text = html.escape(text)
    text = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", text)
    text = re.sub(r"(?<![\w*])_(.+?)_(?!\w)", r"<em>\1</em>", text)
    return text


def markdown_to_html(md: str, title: str, stamp: str | None = None) -> str:
    out, in_list, in_table = [], False, False
    for line in md.splitlines():
        if in_list and not line.startswith("- "):
            out.append("</ul>")
            in_list = False
        if in_table and not line.startswith("|"):
            out.append("</table>")
            in_table = False
        if line.startswith("### "):
            out.append(f"<h3>{_inline(line[4:])}</h3>")
        elif line.startswith("## "):
            out.append(f"<h2>{_inline(line[3:])}</h2>")
        elif line.startswith("# "):
            out.append(f"<h1>{_inline(line[2:])}</h1>")
            if stamp:
                cls = "stamp" if stamp.startswith("APPROVED") else "stamp draft"
                out.append(f'<div class="{cls}">{html.escape(stamp)}</div>')
        elif line.startswith("- "):
            if not in_list:
                out.append("<ul>")
                in_list = True
            out.append(f"<li>{_inline(line[2:])}</li>")
        elif line.startswith("|"):
            cells = [c.strip() for c in line.strip("|").split("|")]
            if all(re.fullmatch(r"-+", c) for c in cells):
                continue
            tag = "th" if not in_table else "td"
            if not in_table:
                out.append("<table>")
                in_table = True
            out.append("<tr>" + "".join(f"<{tag}>{_inline(c)}</{tag}>" for c in cells) + "</tr>")
        elif line.strip():
            out.append(f'<p class="meta">{_inline(line)}</p>' if line.startswith("Reporting date")
                       else f"<p>{_inline(line)}</p>")
    if in_list:
        out.append("</ul>")
    if in_table:
        out.append("</table>")
    return (f"<!doctype html><html lang='en'><head><meta charset='utf-8'><meta name='viewport' "
            f"content='width=device-width,initial-scale=1'><title>{html.escape(title)}</title><style>{CSS}</style>"
            f"</head><body>{''.join(out)}</body></html>")
