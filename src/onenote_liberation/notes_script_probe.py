#!/usr/bin/env python3
"""Write and run a generated AppleScript file for Notes folder diagnostics."""

from __future__ import annotations

import argparse
import pathlib
import subprocess
import sys
import tempfile
from datetime import datetime


def applescript_string(value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def build_script(root: str, child: str) -> str:
    root_lit = applescript_string(root)
    child_lit = applescript_string(child)
    return f'''
tell application "Notes"
    activate
    set a to first account

    if not (exists folder {root_lit} of a) then
        make new folder at a with properties {{name:{root_lit}}}
        delay 1
    end if

    set r to folder {root_lit} of a
    delay 1

    if not (exists folder {child_lit} of r) then
        make new folder at r with properties {{name:{child_lit}}}
        delay 1
    end if

    delay 1
    make new note at folder {child_lit} of r with properties {{name:"Generated script test", body:"<p>Generated AppleScript OK</p>"}}
end tell
'''


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Probe generated AppleScript Notes folder behaviour.")
    parser.add_argument("--root", default=None)
    parser.add_argument("--child", default="B and B - Airbnb")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    stamp = datetime.now().strftime("%H%M%S")
    root = args.root or f"OneNote Generated Probe {stamp}"
    child = args.child

    script = build_script(root, child)
    path = pathlib.Path(tempfile.gettempdir()) / f"onenote_liberation_probe_{stamp}.applescript"
    path.write_text(script, encoding="utf-8")

    print(f"Script written: {path}")
    print(f"Root: {root}")
    print(f"Child: {child}")
    print("Running generated AppleScript file...")

    completed = subprocess.run(["osascript", str(path)], text=True, capture_output=True, check=False)

    print(f"Return code: {completed.returncode}")
    if completed.stdout:
        print("stdout:")
        print(completed.stdout)
    if completed.stderr:
        print("stderr:")
        print(completed.stderr)

    if completed.returncode != 0:
        print("Generated script failed. Inspect the script path above.")
        raise SystemExit(completed.returncode)

    print("Generated script succeeded.")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("Interrupted", file=sys.stderr)
        sys.exit(130)
