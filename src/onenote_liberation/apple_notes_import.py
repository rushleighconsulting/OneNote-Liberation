#!/usr/bin/env python3
"""
Apple Notes importer.

Current scope:
- import one exported page from a .metadata.json file
- import every page in an export directory
- optional dry run
- optional limit for safe testing
- optional folder-per-section import
- experimental asset attachment import
- attachment filename normalisation using magic-byte detection
"""

from __future__ import annotations

import argparse
import html
import json
import os
import pathlib
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

from bs4 import BeautifulSoup

from .assets import detect_file, normalised_attachment_copy


DEFAULT_FOLDER = "OneNote Liberation Test"


@dataclass
class ImportItem:
    metadata_path: pathlib.Path
    metadata: dict[str, Any]
    html_path: pathlib.Path
    asset_paths: list[pathlib.Path]
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


def asset_paths_from_metadata(metadata_path: pathlib.Path, metadata: dict[str, Any]) -> list[pathlib.Path]:
    """Return downloaded asset paths from current and future metadata shapes.

    Older exports used an `images` list even when Graph returned a non-image
    resource. Future exports may use `assets`. Support both.
    """
    root = find_export_root(metadata_path)
    paths: list[pathlib.Path] = []

    candidate_lists = []
    candidate_lists.extend(metadata.get("assets", []) or [])
    candidate_lists.extend(metadata.get("images", []) or [])

    for asset_info in candidate_lists:
        if asset_info.get("status") != "downloaded":
            continue
        asset = asset_info.get("asset") or asset_info.get("path")
        if not asset:
            continue
        candidate = (root / asset).resolve()
        if candidate.exists() and candidate not in paths:
            paths.append(candidate)
    return paths


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


def _rewrite_relative_attr(html_path: pathlib.Path, soup: BeautifulSoup, tag_name: str, attr_name: str) -> None:
    for tag in soup.find_all(tag_name):
        value = tag.get(attr_name)
        if not value or value.startswith("data:") or value.startswith("file://"):
            continue

        parsed = urlparse(value)
        if parsed.scheme in {"http", "https", "mailto", "tel"}:
            continue

        absolute = (html_path.parent / value).resolve()
        if absolute.exists():
            tag[attr_name] = absolute.as_uri()


def prepare_html_for_apple_notes(html_path: pathlib.Path) -> pathlib.Path:
    """Rewrite local asset references to absolute file:// URIs for Apple Notes.

    Apple Notes imports HTML into an internal applewebdata:// context. Relative
    href/src values then point at that private context rather than the export
    folder, so local images and attachment links must be absolute file URIs.
    """
    soup = BeautifulSoup(html_path.read_text(encoding="utf-8"), "html.parser")

    _rewrite_relative_attr(html_path, soup, "img", "src")
    _rewrite_relative_attr(html_path, soup, "a", "href")

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
                asset_paths=asset_paths_from_metadata(metadata_path, metadata),
                title=title,
                destination_folder=destination_folder,
            )
        )

    if limit is not None:
        items = items[:limit]

    return items


def run_osascript(script: str, args: list[str]) -> None:
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


def import_one_item(item: ImportItem, attach_assets: bool) -> None:
    if not item.html_path.exists():
        raise FileNotFoundError(f"HTML file not found: {item.html_path}")

    prepared_html = prepare_html_for_apple_notes(item.html_path)
    normalised_paths: list[pathlib.Path] = []

    if attach_assets:
        normalised_paths = [normalised_attachment_copy(path) for path in item.asset_paths]

    attachment_args = [str(path) for path in normalised_paths]

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
        set newNote to make new note at targetFolder with properties {name:noteTitle, body:noteBody}

        repeat with i from 4 to count of argv
            set attachmentPath to item i of argv
            try
                make new attachment at newNote with data (POSIX file attachmentPath as alias)
            on error errMsg number errNum
                set body of newNote to ((body of newNote) & "<p><strong>Asset attachment failed:</strong> " & attachmentPath & " (" & errMsg & ")</p>")
            end try
        end repeat
    end tell
end run
'''

    try:
        run_osascript(script, [item.title, str(prepared_html), item.destination_folder, *attachment_args])
    finally:
        try:
            prepared_html.unlink(missing_ok=True)
        except Exception:
            pass


def asset_summary(paths: list[pathlib.Path]) -> str:
    if not paths:
        return "0 asset(s)"

    counts: dict[str, int] = {}
    for path in paths:
        info = detect_file(path)
        counts[info.asset_type] = counts.get(info.asset_type, 0) + 1

    bits = [f"{sum(counts.values())} asset(s)"]
    bits.extend(f"{count} {kind}" for kind, count in sorted(counts.items()))
    return ": ".join(bits)


def print_plan(items: list[ImportItem], attach_assets: bool) -> None:
    print(f"Notes selected: {len(items)}")
    for item in items:
        asset_text = f", {asset_summary(item.asset_paths)}" if attach_assets else ""
        print(f"- {item.title} -> {item.destination_folder}{asset_text}")
        print(f"  {item.metadata_path}")


def import_items(items: list[ImportItem], delay: float, attach_assets: bool) -> None:
    for index, item in enumerate(items, start=1):
        print(f"[{index}/{len(items)}] Importing: {item.title} -> {item.destination_folder}")
        import_one_item(item, attach_assets=attach_assets)
        if delay > 0 and index < len(items):
            time.sleep(delay)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Import OneNote Liberation HTML export into Apple Notes.")
    parser.add_argument("input", help="Path to either a .metadata.json file or an export directory.")
    parser.add_argument("--folder", default=DEFAULT_FOLDER, help="Apple Notes destination folder/root prefix.")
    parser.add_argument(
        "--folder-mode",
        choices=["root", "section", "path"],
        default="root",
        help="Folder strategy for directory imports. Default: root.",
    )
    parser.add_argument("--limit", type=int, default=None, help="Import at most N notes.")
    parser.add_argument("--delay", type=float, default=0.2, help="Delay between note imports. Default: 0.2s.")
    parser.add_argument("--no-attach-assets", action="store_true", help="Do not attach downloaded assets after creating notes.")
    parser.add_argument("--dry-run", action="store_true", help="Show what would be imported without writing to Apple Notes.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    input_path = pathlib.Path(args.input).expanduser().resolve()
    attach_assets = not args.no_attach_assets

    items = build_import_items(input_path, args.folder, args.folder_mode, args.limit)
    if not items:
        print("No metadata files found.")
        return

    print_plan(items, attach_assets=attach_assets)
    if args.dry_run:
        print("Dry run only. Nothing was written to Apple Notes.")
        return

    import_items(items, delay=args.delay, attach_assets=attach_assets)
    print(f"Imported {len(items)} note(s) into Apple Notes.")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"ERROR: {html.escape(str(exc))}", file=sys.stderr)
        sys.exit(1)
