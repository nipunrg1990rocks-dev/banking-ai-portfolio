"""The oracle must be independent of the production code, and production must not read oracle outputs."""

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def imports(folder: Path) -> dict[str, set[str]]:
    out = {}
    for f in folder.rglob("*.py"):
        mods = set()
        for node in ast.walk(ast.parse(f.read_text())):
            if isinstance(node, ast.Import):
                mods |= {a.name.split(".")[0] for a in node.names}
            elif isinstance(node, ast.ImportFrom) and node.module:
                mods.add(node.module.split(".")[0])
        out[str(f.relative_to(ROOT))] = mods
    return out


def test_oracle_does_not_import_production():
    bad = {f: m & {"cf360"} for f, m in imports(ROOT / "testkit" / "oracle").items() if m & {"cf360"}}
    assert not bad, bad


def test_production_does_not_import_oracle_or_generator():
    bad = {f: m & {"testkit", "datagen"} for f, m in imports(ROOT / "backend" / "cf360").items() if m & {"testkit", "datagen"}}
    assert not bad, bad


def test_production_never_reads_oracle_files():
    hits = [str(f) for f in (ROOT / "backend" / "cf360").rglob("*.py") if "oracle" in f.read_text().lower()
            and "data/oracle" in f.read_text()]
    assert not hits, hits
