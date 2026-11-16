"""Rebuild every report from recorded runs: snapshot, four PowerPoint decks and the handbook PDF.

    python reporting/build_reports.py

Needs the platform database (CF360_DATABASE_URL), the run files in testkit/out/runs, Node.js with
`npm ci` run once in reporting/, and Playwright's Chromium for the PDF.
"""

import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
STEPS = [[sys.executable, str(HERE / "build_snapshot.py")], ["node", str(HERE / "decks.js")],
         [sys.executable, str(HERE / "handbook.py")], ["node", str(HERE / "pdf.js")]]

if __name__ == "__main__":
    for cmd in STEPS:
        print("+", " ".join(Path(c).name for c in cmd), flush=True)
        subprocess.run(cmd, check=True, cwd=HERE.parent)
