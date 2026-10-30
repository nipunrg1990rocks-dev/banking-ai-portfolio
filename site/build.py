"""Build the unified portfolio website into a static folder for GitHub Pages.

Reads ``site/portfolio.json`` (every link and status lives there), fills
``site/template.html`` and copies each project's showcase page and PDF pack when
the file exists in the repo. Projects whose files haven't reached main yet show
a "coming soon" button instead of a broken link, so the site can be rebuilt at
any time and fills itself in as projects merge.

    python site/build.py               # writes _site/
    python site/build.py --out DIR     # somewhere else
    python site/build.py --fragment    # page without <html>/<head>, for an Artifact preview
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
from datetime import date
from html import escape
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SITE = ROOT / "site"
GENESIS = "0" * 64
BODY_START = re.compile(r"<(?!/|!|title\b|meta\b|link\b|style\b)[a-zA-Z]")

HEAD = (
    '<!doctype html>\n<html lang="en">\n<head>\n<meta charset="utf-8">\n'
    '<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">\n'
)

BACK_LINK = (
    '<nav style="padding:10px max(16px, env(safe-area-inset-left, 0px));font:500 14px/1.4 \'IBM Plex Sans\','
    'system-ui,sans-serif;background:#0b1715"><a href="../index.html#{anchor}" style="color:#5fd0b3;'
    'text-decoration:none">← All five projects</a></nav>\n'
)


def load(path: Path = SITE / "portfolio.json") -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def chain(projects: list[dict]) -> list[tuple[str, str]]:
    """(prev_hash, hash) per project. The page's script recomputes the same strings."""
    out, prev = [], GENESIS
    for p in projects:
        h = hashlib.sha256(f"{prev}|{p['number']}|{p['name']}|{p['status_label']}".encode()).hexdigest()
        out.append((prev, h))
        prev = h
    return out


def short(h: str) -> str:
    return f"{h[:6]}…{h[-4:]}"


def resolve_links(p: dict, repo_url: str, root: Path) -> dict:
    """Which links exist right now. Paths are relative to the built site."""
    links = {"live": p.get("live_url", ""), "showcase": "", "pdf": "", "code": "", "code_label": "Code on GitHub"}
    if p.get("showcase") and (root / p["showcase"]).is_file():
        links["showcase"] = f"projects/{Path(p['showcase']).name}"
    if p.get("pdf") and (root / p["pdf"]).is_file():
        links["pdf"] = f"packs/{Path(p['pdf']).name}"
    if p.get("code_path") and (root / p["code_path"]).is_dir():
        links["code"] = f"{repo_url}/tree/main/{p['code_path']}"
    elif p.get("pr_url"):
        links["code"], links["code_label"] = p["pr_url"], "Code (in review)"
    return links


def button(href: str, label: str, solid: bool = False, missing: str = "") -> str:
    if not href:
        return f'<span class="btn" aria-disabled="true">{escape(missing)}</span>' if missing else ""
    cls = "btn solid" if solid else "btn"
    arrow = " ↗" if href.startswith("http") else ""
    return f'<a class="{cls}" href="{escape(href)}">{escape(label)}{arrow}</a>'


def render_chain(projects: list[dict], seals: list[tuple[str, str]]) -> str:
    rows = []
    for p, (prev, h) in zip(projects, seals, strict=True):
        rows.append(
            f'      <li style="display:contents"><a class="block" href="#{p["id"]}" data-n="{p["number"]}" '
            f'data-hash="{h}" data-status="{escape(p["status"])}">\n'
            f'        <span class="idx"><span>BLOCK {p["number"]:02d}</span><span class="seal">SEALED</span></span>\n'
            f'        <span class="nm">{escape(p["name"])}</span>\n'
            f'        <span class="st">{escape(p["status_label"])}</span>\n'
            f'        <span class="hashes"><span>prev <b>{short(prev)}</b></span>'
            f"<span>hash <b>{short(h)}</b></span></span>\n"
            "      </a></li>"
        )
    return "\n".join(rows)


def render_lifecycle(projects: list[dict]) -> str:
    order = ["Data arrives", "Risk is monitored", "Exceptions are worked", "Staff ask about policy", "The rules change"]
    by_stage = {p["lifecycle"]: p for p in projects}
    rows = []
    for stage in order:
        p = by_stage.get(stage)
        if p:
            rows.append(
                f'        <li><span>{escape(stage)}</span><a href="#{p["id"]}">{escape(p["name"])}</a>'
                f'<span>Project {p["number"]} · {escape(p["kind"])}</span></li>'
            )
    return "\n".join(rows)


def render_project(p: dict, links: dict) -> str:
    stats = "\n".join(
        f'          <div class="stat"><b>{escape(s["value"])}</b><span>{escape(s["label"])}</span></div>'
        for s in p["stats"]
    )
    controls = "".join(f"<li>{escape(c)}</li>" for c in p["controls"])
    stack = "".join(f'<span class="tag">{escape(t)}</span>' for t in p["stack"])
    actions = " ".join(
        b
        for b in (
            button(links["live"], "Open the live demo", solid=True, missing="Live demo coming soon"),
            button(links["showcase"], "Project page"),
            button(links["pdf"], "Interview pack (PDF)"),
            button(links["code"], links["code_label"]),
        )
        if b
    )
    chip = "live" if p["status"] == "live" else "built"
    return f"""        <article class="dossier" id="{p['id']}">
          <div class="side">
            <span class="num">{p['number']:02d}</span>
            <span class="kind">{escape(p['kind'])}</span>
            <span class="chip {chip}">{escape(p['status_label'])}</span>
          </div>
          <div class="body">
            <h3>{escape(p['name'])}</h3>
            <p class="full">{escape(p['full_name'])}</p>
            <p class="summary">{escape(p['summary'])}</p>
            <div class="pa">
              <div><p class="eyebrow">The banking problem</p><p>{escape(p['problem'])}</p></div>
              <div><p class="eyebrow">What the AI does</p><p>{escape(p['ai'])}</p></div>
            </div>
            <div class="stats">
{stats}
            </div>
            <p class="note">{escape(p['stats_note'])}</p>
            <ul class="controls">{controls}</ul>
            <div class="stack">{stack}</div>
            <div class="actions">{actions}</div>
          </div>
        </article>"""


def render_register(projects: list[dict]) -> str:
    labels = {"verified": "Verified", "owner": "Checked by me", "pending": "Not yet", "na": "—"}
    rows = []
    for p in projects:
        cells = "".join(
            f'<td><span class="cell s-{c["state"]}"><i>{labels[c["state"]]}</i>'
            f'<span>{escape(c["text"])}</span></span></td>'
            for c in (p["checks"][k] for k in ("tests", "browser", "databricks", "llm", "hosted"))
        )
        rows.append(f'            <tr><td class="p"><a href="#{p["id"]}">{escape(p["name"])}</a></td>{cells}</tr>')
    return "\n".join(rows)


def total_tests(projects: list[dict]) -> int:
    return sum(int(p["checks"]["tests"]["text"].split()[0]) for p in projects)


def render_page(data: dict, root: Path = ROOT, today: date | None = None) -> tuple[str, list[dict]]:
    person, site, projects = data["person"], data["site"], data["projects"]
    links = [resolve_links(p, site["repo_url"], root) for p in projects]
    contact = " ".join(
        b
        for b in (
            button(person.get("linkedin_url", ""), "LinkedIn", solid=True),
            button(person.get("github_url", ""), "GitHub"),
            f'<span class="btn mono" aria-disabled="true">{escape(person["email"])}</span>'
            if person.get("email")
            else "",
        )
        if b
    )
    fills = {
        "TITLE": f"{person['name']} · Banking AI Portfolio",
        "DESCRIPTION": f"Five working, AI-assisted banking AI and Databricks projects by {person['name']}.",
        "NAME": person["name"],
        "NAME_UPPER": person["name"].upper(),
        "HEADLINE": person["headline"],
        "SUMMARY": person["summary"],
        "YEARS": str(person["years_banking"]),
        "ROLES": "".join(f"<span>{escape(r)}</span>" for r in person["roles"]),
        "CONTACT": contact,
        "YEAR": str(site["year"]),
        "BANK": site["bank"],
        "REPO_URL": site["repo_url"],
        "REPO_SHORT": site["repo_url"].removeprefix("https://github.com/"),
        "TOTAL_TESTS": str(total_tests(projects)),
        "UPDATED": f"{(today or date.today()).day} {(today or date.today()):%B %Y}",
        "CHAIN": render_chain(projects, chain(projects)),
        "LIFECYCLE": render_lifecycle(projects),
        "PROJECTS": "\n".join(render_project(p, lk) for p, lk in zip(projects, links, strict=True)),
        "REGISTER": render_register(projects),
    }
    page = (SITE / "template.html").read_text(encoding="utf-8")
    for key, value in fills.items():
        # Text fields are escaped here; the rendered blocks above escape their own parts.
        safe = value if key in {"CHAIN", "LIFECYCLE", "PROJECTS", "REGISTER", "ROLES", "CONTACT"} else escape(value)
        page = page.replace("{{" + key + "}}", safe)
    if "{{" in page:
        raise ValueError(f"unfilled placeholder near: {page[page.index('{{'):page.index('{{') + 40]}")
    return page, links


def wrap(fragment: str, extra_body: str = "") -> str:
    """Give an Artifact-style fragment (no doctype) a real document shell for GitHub Pages.

    The leading <title>, <meta>, <link> and <style> tags go into <head>; the rest is the body.
    """
    match = BODY_START.search(fragment)
    cut = match.start() if match else 0
    head, body = fragment[:cut], fragment[cut:]
    return f'{HEAD}{head}</head>\n<body>\n{extra_body}{body}\n</body>\n</html>\n'


def build(out: Path, root: Path = ROOT, fragment: bool = False) -> Path:
    data = load(root / "site" / "portfolio.json") if (root / "site" / "portfolio.json").is_file() else load()
    page, links = render_page(data, root)
    if out.exists():
        shutil.rmtree(out)
    (out / "projects").mkdir(parents=True)
    (out / "packs").mkdir()
    (out / "index.html").write_text(page if fragment else wrap(page), encoding="utf-8")
    (out / ".nojekyll").write_text("", encoding="utf-8")
    for p, lk in zip(data["projects"], links, strict=True):
        if lk["showcase"]:
            src = (root / p["showcase"]).read_text(encoding="utf-8")
            body = src if fragment else wrap(src, BACK_LINK.format(anchor=p["id"]))
            (out / lk["showcase"]).write_text(body, encoding="utf-8")
        if lk["pdf"]:
            shutil.copyfile(root / p["pdf"], out / lk["pdf"])
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", type=Path, default=ROOT / "_site")
    ap.add_argument("--root", type=Path, default=ROOT, help="repo checkout to read projects from")
    ap.add_argument("--fragment", action="store_true", help="omit the document shell (Artifact preview)")
    args = ap.parse_args()
    out = build(args.out, args.root.resolve(), args.fragment)
    print(f"Built {out}: {sorted(str(p.relative_to(out)) for p in out.rglob('*') if p.is_file())}")


if __name__ == "__main__":
    main()
