#!/usr/bin/env python3
"""
Apple Notes importer.

Current scope:
- import one exported page from a .metadata.json file
- import every page in an export directory
- optional dry run
- optional limit for safe testing
- optional folder-per-section import
- experimental image attachment support

Run one note:
    python -m onenote_liberation.apple_notes_import path/to/page.metadata.json

Run directory dry run:
    python -m onenote_liberation.apple_notes_import path/to/export --dry-run

Run first 5 notes:
    python -m onenote_liberation.apple_notes_import path/to/export --limit 5
"""

from __future__ import annotations

import argparse
import html
import json
import pathlib
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

from bs4 import BeautifulSoup


DEFAULT_FOLDER = "OneNote Liberation Test"
VERSION = "0.9.0"


@dataclass
class ImportItem:
    metadata_path: pathlib.Path
    metadata: dict[str, Any]
    html_path: pathlib.Path
    title: str
    destination_folder: str


def read_metadata(path: pathlib.Path) -> dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(path)
    return json.loads(path.read_text(encoding="utf-8"))


def find_export_root(path: pathlib.Path) -> pathlib.Path:
    current = path if path.is_dir() else path.parent
    for candidate in [current, *current.parents]:
        if (candidate / "index.html").exists() or (candidate / "export_report.json").exists():
            return candidate
    raise FileNotFoundError("Could not find export root containing index.html or export_report.json")


def html_path_from_metadata(metadata_path: pathlib.Path, metadata: dict[str, Any]) -> pathlib.Path:
    html_rel = metadata.get("files", {}).get("html")
    if not html_rel:
        raise ValueError("Metadata does not contain files.html")
    return find_export_root(metadata_path) / html_rel


def metadata_files_from_input(input_path: pathlib.Path) -> list[pathlib.Path]:
    if input_path.is_file():
        if input_path.name.endswith(".metadata.json"):
            return [input_path]
        raise ValueError("Input file must be a .metadata.json file")

    if not input_path.is_dir():
        raise FileNotFoundError(input_path)

    return sorted(input_path.rglob("*.metadata.json"))


def safe_folder_name(value: str) -> str:
    value = value.strip()
    value = value.replace(":", " -")
    return value or "Untitled"


def folder_for_metadata(metadata: dict[str, Any], root_folder: str, folder_mode: str) -> str:
    if folder_mode == "root":
        return root_folder

    hierarchy = metadata.get("hierarchy", {})
    section = hierarchy.get("section") or "Untitled section"

    if folder_mode == "section":
        return safe_folder_name(f"{root_folder} - {section}")

    path_parts = hierarchy.get("path_parts") or []
    if folder_mode == "path":
        suffix = " - ".join(safe_folder_name(str(part)) for part in path_parts[1:])
        return safe_folder_name(f"{root_folder} - {suffix}" if suffix else root_folder)

    raise ValueError(f"Unknown folder mode: {folder_mode}")


def asset_paths_from_metadata(metadata_path: pathlib.Path, metadata: dict[str, Any]) -> list[pathlib.Path]:
    export_root = find_export_root(metadata_path)
    assets: list[pathlib.Path] = []

    for image in metadata.get("images", []) or []:
        if image.get("status") != "downloaded":
            continue
        rel = image.get("asset")
        if not rel:
            continue
        path = (export_root / rel).resolve()
        if path.exists():
            assets.append(path)

    return assets


def prepare_html_for_apple_notes(html_path: pathlib.Path) -> pathlib.Path:
    soup = BeautifulSoup(html_path.read_text(encoding="utf-8"), "html.parser")

    for img in soup.find_all("img"):
        src = img.get("src")
        if not src or src.startswith("data:") or src.startswith("file://"):
            continue

        parsed = urlparse(src)
        if parsed.scheme in {"http", "https"}:
            continue

        absolute = (html_path.parent / src).resolve()
        if absolute.exists():
            img["src"] = absolute.as_uri()

    temp = tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        suffix=".html",
        prefix="onenote_liberation_import_",
        delete=False,
    )
    with temp:
        temp.write(str(soup))

    return pathlib.Path(temp.name)


def build_import_items(
    input_path: pathlib.Path,
    root_folder: str,
    folder_mode: str,
    limit: int | None,
) -> list[ImportItem]:
    items: list[ImportItem] = []

    for metadata_path in metadata_files_from_input(input_path):
        metadata = read_metadata(metadata_path)
        html_path = html_path_from_metadata(metadata_path, metadata)
        title = metadata.get("title") or "Untitled"
        destination_folder = folder_for_metadata(metadata, root_folder, folder_mode)
        items.append(
            ImportItem(
                metadata_path=metadata_path,
                metadata=metadata,
                html_path=html_path,
                title=title,
                destination_folder=destination_folder,
            )
        )

    if limit is not None:
        items = items[:limit]

    return items


def run_osascript(script: str, args: list[str]) -> str:
    completed = subprocess.run(
        ["osascript", "-e", script, *args],
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

    return completed.stdout.strip()


def import_one_item(item: ImportItem, attach_images: bool) -> None:
    if not item.html_path.exists():
        raise FileNotFoundError(f"HTML file not found: {item.html_path}")

    prepared_html = prepare_html_for_apple_notes(item.html_path)
    image_paths = asset_paths_from_metadata(item.metadata_path, item.metadata) if attach_images else []
    image_list = "\n".join(str(path) for path in image_paths)

    script = r'''
on run argv
    set noteTitle to item 1 of argv
    set htmlPath to item 2 of argv
    set folderName to item 3 of argv
    set imagePathText to item 4 of argv

    set noteBody to read POSIX file htmlPath as «class utf8»

    tell application "Notes"
        activate

        set targetAccount to first account

        if not (exists folder folderName of targetAccount) then
            make new folder at targetAccount with properties {name:folderName}
        end if

        set targetFolder to folder folderName of targetAccount
        set targetNote to make new note at targetFolder with properties {name:noteTitle, body:noteBody}

        if imagePathText is not "" then
            set AppleScript's text item delimiters to linefeed
            set imagePaths to text items of imagePathText
            set AppleScript's text item delimiters to ""

            repeat with imagePath in imagePaths
                if imagePath is not "" then
                    set imageAlias to POSIX file imagePath as alias
                    try
                        make new attachment at targetNote with data imageAlias
                    on error errMsg number errNo
                        set body of targetNote to (body of targetNote) & "<br><p>[Image attachment failed: " & imagePath & "]</p>"
                    end try
                end if
            end repeat
        end if
    end tell
end run
'''

    try:
        run_osascript(script, [item.title, str(prepared_html), item.destination_folder, image_list])
    finally:
        try:
            prepared_html.unlink(missing_ok=True)
        except Exception:
            pass


def print_plan(items: list[ImportItem], attach_images: bool) -> None:
    print(f"OneNote Liberation Apple Notes importer {VERSION}")
    print(f"Notes selected: {len(items)}")
    print(f"Attach images: {'yes' if attach_images else 'no'}")
    for item in items:
        image_count = len(asset_paths_from_metadata(item.metadata_path, item.metadata))
        print(f"- {item.title} -> {item.destination_folder} ({image_count} image asset(s))")
        print(f"  {item.metadata_path}")


def import_items(items: list[ImportItem], delay: float, attach_images: bool) -> None:
    for index, item in enumerate(items, start=1):
        print(f"[{index}/{len(items)}] Importing: {item.title} -> {item.destination_folder}")
        import_one_item(item, attach_images=attach_images)
        if delay > 0 and index < len(items):
            time.sleep(delay)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Import OneNote Liberation exports into Apple Notes.")
    parser.add_argument("input", help="Path to either a .metadata.json file or an export directory.")
    parser.add_argument(
        "--folder",
        default=DEFAULT_FOLDER,
        help=f"Apple Notes destination root folder. Default: {DEFAULT_FOLDER}",
    )
    parser.add_argument(
        "--folder-mode",
        choices=["root", "section", "path"],
        default="root",
        help=(
            "Destination strategy: root = all notes in one folder; "
            "section = one folder per section; path = flattened full OneNote path. Default: root."
        ),
    )
    parser.add_argument("--limit", type=int, default=None, help="Import only the first N notes.")
    parser.add_argument("--delay", type=float, default=0.2, help="Delay in seconds between imports.")
    parser.add_argument("--dry-run", action="store_true", help="Show plan but do not write to Apple Notes.")
    parser.add_argument(
        "--no-attach-images",
        action="store_true",
        help="Do not attempt to attach exported image assets to Apple Notes.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    input_path = pathlib.Path(args.input).expanduser().resolve()
    attach_images = not args.no_attach_images
    items = build_import_items(input_path, args.folder, args.folder_mode, args.limit)

    if not items:
        print("No metadata files found.")
        return

    print_plan(items, attach_images=attach_images)

    if args.dry_run:
        print("Dry run only. Nothing was written to Apple Notes.")
        return

    import_items(items, args.delay, attach_images=attach_images)
    print(f"Imported {len(items)} note(s) into Apple Notes.")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"ERROR: {html.escape(str(exc))}", file=sys.stderr)
        sys.exit(1)
