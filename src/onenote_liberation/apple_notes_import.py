#!/usr/bin/env python3
"""
Apple Notes proof-of-concept importer.

This imports one exported OneNote Liberation page, identified by its
.metadata.json file, into a single Apple Notes folder.

This is deliberately narrow:
- one note only
- one destination folder only
- no hierarchy recreation yet
- no bulk import yet

Run:
    python -m onenote_liberation.apple_notes_import path/to/page.metadata.json
"""

from __future__ import annotations

import argparse
import json
import pathlib
import subprocess
import sys
from typing import Any


DEFAULT_FOLDER = "OneNote Liberation Test"


def read_metadata(path: pathlib.Path) -> dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(path)
    return json.loads(path.read_text(encoding="utf-8"))


def html_path_from_metadata(metadata_path: pathlib.Path, metadata: dict[str, Any]) -> pathlib.Path:
    html_rel = metadata.get("files", {}).get("html")
    if not html_rel:
        raise ValueError("Metadata does not contain files.html")

    # Metadata paths are relative to the export root. The metadata file is normally
    # pages/<notebook>/<group>/<section>/<page>.metadata.json, so walk upward to
    # find the export root by looking for index.html or export_report.json.
    current = metadata_path.parent
    for candidate in [current, *current.parents]:
        if (candidate / "index.html").exists() or (candidate / "export_report.json").exists():
            return candidate / html_rel

    # Fallback: use parent of parent-style relative resolution.
    return metadata_path.parent / pathlib.Path(html_rel).name


def import_to_apple_notes(metadata_path: pathlib.Path, folder_name: str) -> None:
    metadata = read_metadata(metadata_path)
    title = metadata.get("title") or "Untitled"
    html_path = html_path_from_metadata(metadata_path, metadata)

    if not html_path.exists():
        raise FileNotFoundError(f"HTML file not found: {html_path}")

    script = r'''
on run argv
    set noteTitle to item 1 of argv
    set htmlPath to item 2 of argv
    set folderName to item 3 of argv

    set noteBody to read POSIX file htmlPath as «class utf8»

    tell application "Notes"
        activate

        set targetAccount to first account

        if not (exists folder folderName of targetAccount) then
            make new folder at targetAccount with properties {name:folderName}
        end if

        set targetFolder to folder folderName of targetAccount
        make new note at targetFolder with properties {name:noteTitle, body:noteBody}
    end tell
end run
'''

    completed = subprocess.run(
        ["osascript", "-e", script, title, str(html_path), folder_name],
        text=True,
        capture_output=True,
        check=False,
    )

    if completed.returncode != 0:
        raise RuntimeError(
            "Apple Notes import failed.\n"
            f"stdout:\n{completed.stdout}\n\n"
            f"stderr:\n{completed.stderr}"
        )

    print("Imported one note into Apple Notes.")
    print(f"Folder: {folder_name}")
    print(f"Title: {title}")
    print(f"Source HTML: {html_path}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Import one exported page into Apple Notes.")
    parser.add_argument("metadata", help="Path to a .metadata.json file from a OneNote Liberation export.")
    parser.add_argument(
        "--folder",
        default=DEFAULT_FOLDER,
        help=f"Apple Notes destination folder. Default: {DEFAULT_FOLDER}",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    import_to_apple_notes(pathlib.Path(args.metadata).expanduser().resolve(), args.folder)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)
