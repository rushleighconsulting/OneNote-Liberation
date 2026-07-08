#!/usr/bin/env python3
"""One-level Apple Notes importer using literal AppleScript folder names.

Apple Notes' AppleScript support behaves differently when folder names are
passed via argv. This importer generates the root and child folder names as
quoted AppleScript literals, matching the pattern proven to work locally:

    Root > "Section Group - Section" > Note
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


DEFAULT_FOLDER = "OneNote One-Level Import"


def safe_part(value: str) -> str:
    value = str(value).strip().replace(":", " -")
    return value or "Untitled"


def folder_path_for_item(item: flat.ImportItem) -> list[str]:
    hierarchy = item.metadata.get("hierarchy", {})
    path_parts = hierarchy.get("path_parts") or []
    return [safe_part(part) for part in path_parts[1:] if str(part).strip()]


def one_level_folder_for_item(item: flat.ImportItem) -> str:
    parts = folder_path_for_item(item)
    if not parts:
        return "Unfiled"
    return safe_part(" - ".join(parts))


def applescript_string(value: str) -> str:
    # AppleScript string literal. Backslash must be escaped before quotes.
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def run_osascript(script: str, args: list[str]) -> None:
    completed = subprocess.run(
        ["osascript", "-e", script, *args],
        text=True,
        capture_output=True,
        check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError(
            "Apple Notes one-level import failed.\n"
            f"stdout:\n{completed.stdout}\n\n"
            f"stderr:\n{completed.stderr}"
        )


def import_one_item(item: flat.ImportItem, root_folder: str, attach_assets: bool) -> None:
    if not item.html_path.exists():
        raise FileNotFoundError(f"HTML file not found: {item.html_path}")

    prepared_html = flat.prepare_html_for_apple_notes(item.html_path)
    child_folder = one_level_folder_for_item(item)

    normalised_paths: list[pathlib.Path] = []
    if attach_assets:
        normalised_paths = [normalised_attachment_copy(path) for path in item.asset_paths]

    root_literal = applescript_string(root_folder)
    child_literal = applescript_string(child_folder)

    script = f'''
on run argv
    set noteTitle to item 1 of argv
    set htmlPath to item 2 of argv
    set noteBody to read POSIX file htmlPath as «class utf8»

    tell application "Notes"
        activate
        set a to first account

        if not (exists folder {root_literal} of a) then
            make new folder at a with properties {{name:{root_literal}}}
        end if

        set r to folder {root_literal} of a

        if not (exists folder {child_literal} of r) then
            make new folder at r with properties {{name:{child_literal}}}
        end if

        set targetFolder to folder {child_literal} of r
        set newNote to make new note at targetFolder with properties {{name:noteTitle, body:noteBody}}

        if (count of argv) ≥ 3 then
            repeat with i from 3 to count of argv
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

    args = [item.title, str(prepared_html), *[str(path) for path in normalised_paths]]

    try:
        run_osascript(script, args)
    finally:
        try:
            prepared_html.unlink(missing_ok=True)
        except Exception:
            pass


def destination_for_item(item: flat.ImportItem, root_folder: str) -> str:
    return " / ".join([root_folder, one_level_folder_for_item(item)])


def print_plan(items: list[flat.ImportItem], root_folder: str, attach_assets: bool) -> None:
    print(f"Notes selected: {len(items)}")
    print("Hierarchy mode: one-level-literal")
    for item in items:
        asset_text = f", {flat.asset_summary(item.asset_paths)}" if attach_assets else ""
        print(f"- {item.title} -> {destination_for_item(item, root_folder)}{asset_text}")
        print(f"  {item.metadata_path}")


def import_items(items: list[flat.ImportItem], root_folder: str, delay: float, attach_assets: bool) -> None:
    for index, item in enumerate(items, start=1):
        print(f"[{index}/{len(items)}] Importing: {item.title} -> {destination_for_item(item, root_folder)}")
        import_one_item(item, root_folder=root_folder, attach_assets=attach_assets)
        if delay > 0 and index < len(items):
            time.sleep(delay)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Import OneNote Liberation export into one-level Apple Notes folders.")
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
