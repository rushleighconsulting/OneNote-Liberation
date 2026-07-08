#!/usr/bin/env python3
"""Hierarchy-aware Apple Notes importer.

Imports a OneNote Liberation export into Apple Notes using the stored
OneNote hierarchy:

    Root folder > section group(s) > section > note

This is intentionally separate from apple_notes_import.py so the proven flat
importer remains available.
"""

from __future__ import annotations

import argparse
import html
import pathlib
import subprocess
import sys
import time

from . import apple_notes_import as flat
from .assets import normalised_attachment_copy


DEFAULT_FOLDER = "OneNote Migration Candidate Tree"


def folder_path_for_item(item: flat.ImportItem) -> list[str]:
    hierarchy = item.metadata.get("hierarchy", {})
    path_parts = hierarchy.get("path_parts") or []

    # path_parts is normally: [Notebook, Section Group..., Section]
    # Apple Notes root folder supplied by the user stands in for the notebook.
    folder_parts = [str(part).strip() for part in path_parts[1:] if str(part).strip()]
    return folder_parts or [item.destination_folder]


def run_osascript(script: str, args: list[str]) -> None:
    completed = subprocess.run(
        ["osascript", "-e", script, *args],
        text=True,
        capture_output=True,
        check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError(
            "Apple Notes hierarchy import failed.\n"
            f"stdout:\n{completed.stdout}\n\n"
            f"stderr:\n{completed.stderr}"
        )


def import_one_item_tree(item: flat.ImportItem, root_folder: str, attach_assets: bool) -> None:
    if not item.html_path.exists():
        raise FileNotFoundError(f"HTML file not found: {item.html_path}")

    prepared_html = flat.prepare_html_for_apple_notes(item.html_path)
    folder_parts = folder_path_for_item(item)

    normalised_paths: list[pathlib.Path] = []
    if attach_assets:
        normalised_paths = [normalised_attachment_copy(path) for path in item.asset_paths]

    script = r'''
on run argv
    set noteTitle to item 1 of argv
    set htmlPath to item 2 of argv
    set rootFolderName to item 3 of argv
    set folderCount to (item 4 of argv) as integer

    set noteBody to read POSIX file htmlPath as «class utf8»

    tell application "Notes"
        activate
        set targetAccount to first account

        if not (exists folder rootFolderName of targetAccount) then
            make new folder at targetAccount with properties {name:rootFolderName}
        end if

        set currentContainer to folder rootFolderName of targetAccount

        repeat with i from 1 to folderCount
            set childName to item (4 + i) of argv
            if not (exists folder childName of currentContainer) then
                make new folder at currentContainer with properties {name:childName}
            end if
            set currentContainer to folder childName of currentContainer
        end repeat

        set attachmentStart to 5 + folderCount
        set newNote to make new note at currentContainer with properties {name:noteTitle, body:noteBody}

        if (count of argv) ≥ attachmentStart then
            repeat with i from attachmentStart to count of argv
                set attachmentPath to item i of argv
                try
                    make new attachment at newNote with data (POSIX file attachmentPath as alias)
                on error errMsg number errNum
                    set body of newNote to ((body of newNote) & "<p><strong>Asset attachment failed:</strong> " & attachmentPath & " (" & errMsg & ")</p>")
                end try
            end repeat
        end if
    end tell
end run
'''

    args = [
        item.title,
        str(prepared_html),
        root_folder,
        str(len(folder_parts)),
        *folder_parts,
        *[str(path) for path in normalised_paths],
    ]

    try:
        run_osascript(script, args)
    finally:
        try:
            prepared_html.unlink(missing_ok=True)
        except Exception:
            pass


def print_plan(items: list[flat.ImportItem], root_folder: str, attach_assets: bool) -> None:
    print(f"Notes selected: {len(items)}")
    for item in items:
        folders = " / ".join([root_folder, *folder_path_for_item(item)])
        asset_text = f", {flat.asset_summary(item.asset_paths)}" if attach_assets else ""
        print(f"- {item.title} -> {folders}{asset_text}")
        print(f"  {item.metadata_path}")


def import_items(items: list[flat.ImportItem], root_folder: str, delay: float, attach_assets: bool) -> None:
    for index, item in enumerate(items, start=1):
        folders = " / ".join([root_folder, *folder_path_for_item(item)])
        print(f"[{index}/{len(items)}] Importing: {item.title} -> {folders}")
        import_one_item_tree(item, root_folder=root_folder, attach_assets=attach_assets)
        if delay > 0 and index < len(items):
            time.sleep(delay)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Import OneNote Liberation export into nested Apple Notes folders.")
    parser.add_argument("input", help="Path to either a .metadata.json file or an export directory.")
    parser.add_argument("--folder", default=DEFAULT_FOLDER, help="Apple Notes root folder.")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--delay", type=float, default=0.2)
    parser.add_argument("--no-attach-assets", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    input_path = pathlib.Path(args.input).expanduser().resolve()
    attach_assets = not args.no_attach_assets

    # Use root mode here because tree placement is handled by this importer.
    items = flat.build_import_items(input_path, args.folder, "root", args.limit)
    if not items:
        print("No metadata files found.")
        return

    print_plan(items, root_folder=args.folder, attach_assets=attach_assets)
    if args.dry_run:
        print("Dry run only. Nothing was written to Apple Notes.")
        return

    import_items(items, root_folder=args.folder, delay=args.delay, attach_assets=attach_assets)
    print(f"Imported {len(items)} note(s) into Apple Notes.")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"ERROR: {html.escape(str(exc))}", file=sys.stderr)
        sys.exit(1)
