"""Portfolio website: the build must succeed, every local link must resolve and the chain must verify."""

from __future__ import annotations

import hashlib
import importlib.util
import re
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("site_build", ROOT / "site" / "build.py")
build = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(build)


def test_build_links_all_resolve(tmp_path):
    out = build.build(tmp_path / "site")
    html = (out / "index.html").read_text(encoding="utf-8")
    assert html.startswith("<!doctype html>") and "{{" not in html
    local = [h for h in re.findall(r'href="([^"]+)"', html) if not h.startswith(("http", "#"))]
    assert local, "expected links to project pages and PDF packs"
    for href in local:
        assert (out / href).is_file(), href
    for anchor in re.findall(r'href="#([^"]+)"', html):
        assert f'id="{anchor}"' in html, anchor


def test_chain_hashes_match_page_formula():
    data = build.load()
    prev = build.GENESIS
    for p, (seal_prev, seal) in zip(data["projects"], build.chain(data["projects"]), strict=True):
        assert seal_prev == prev
        claim = f"{prev}|{p['number']}|{p['name']}|{p['status_label']}"
        assert seal == hashlib.sha256(claim.encode()).hexdigest()
        prev = seal


def test_missing_files_show_coming_soon_not_broken_links(tmp_path):
    root = tmp_path / "repo"
    shutil.copytree(ROOT / "site", root / "site")
    page, links = build.render_page(build.load(root / "site" / "portfolio.json"), root)
    assert all(not lk["showcase"] and not lk["pdf"] for lk in links)
    assert page.count("Live demo coming soon") == sum(1 for p in build.load()["projects"] if not p["live_url"])
    assert 'href="projects/' not in page and 'href="packs/' not in page


def test_every_project_is_labelled_honestly():
    for p in build.load()["projects"]:
        assert set(p["checks"]) == {"tests", "browser", "databricks", "llm", "hosted"}
        assert all(c["state"] in {"verified", "owner", "pending", "na"} for c in p["checks"].values())
        # A project is only shown as live when it has a live URL, and vice versa.
        assert (p["status"] == "live") == bool(p["live_url"])
