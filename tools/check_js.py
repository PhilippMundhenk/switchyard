"""Syntax-check every JavaScript file of the web UI (needs node)."""

import subprocess
import sys
from pathlib import Path

root = Path(__file__).resolve().parent.parent / "src/switchyard/web/static"
failed = 0
for path in sorted(root.rglob("*.js")):
    result = subprocess.run(
        ["node", "--input-type=module", "--check"],
        input=path.read_text(),
        capture_output=True,
        text=True,
    )
    if result.returncode:
        failed += 1
        print(f"{path.relative_to(root)}:\n{result.stderr}")
print("JavaScript OK" if not failed else f"{failed} file(s) with syntax errors")
sys.exit(1 if failed else 0)
