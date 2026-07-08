#!/usr/bin/env python3
"""Hierarchy-preserving Apple Notes importer using flattened path folders.

Apple Notes supports nested folders in the UI, but its AppleScript interface is
not reliable when creating/addressing child folders. This importer therefore
preserves the full OneNote hierarchy in the destination folder name:

    Root - Section Group - Section

Example:
    OneNote Migration - Rushleigh - Tipsy Fox

This keeps the proven import behaviour and preserves context without relying on
fragile nested-folder scripting.
"""

from __future__ import annotations

import argparse
import html
import pathlib
import sys
import time

from . import apple_notes_import as flat


DEFAULT_FOLDER = "OneNote Migration Candidate Tree"


def safe_part(value: str) -> str:
    value = str(value).strip().replace(":", " -")
    return value or "Untitled"


def folder_path_for_item(item: flat.ImportItem) -> list[str]:
    hierarchy = item.metadata.get("hierarchy", {})
    path_parts = hierarchy.get("path_parts") or []
    # path_parts normally: [Notebook, Section Group..., Section]
    return [safe_part(part) for part in path_parts[1:] if str(part).strip()]


def flattened_folder_for_item(item: flat.ImportItem, root_folder: str) -> str:
    parts = folder_path_for_item(item)
    if not parts:
        return root_folder
    return safe_part(f"{root_folder} - {' - '.join(parts)}")


def remap_items_to_flattened_folders(items: list[flat.ImportItem], root_folder: str) -> list[flat.ImportItem]:
    for item in items:
        item.destination_folder = flattened_folder_for_item(item, root_folder)
    return items


def print_plan(items: list[flat.ImportItem], root_folder: str, attach_assets: bool) -> None:
    print(f"Notes selected: {len(items)}")
    for item in items:
        folder = flattened_folder_for_item(item, root_folder)
        asset_text = f", {flat.asset_summary(item.asset_paths)}" if attach_assets else ""
        print(f"- {item.title} -> {folder}{asset_text}")
        print(f"  {item.metadata_path}")


def import_items(items: list[flat.ImportItem], root_folder: str, delay: float, attach_assets: bool) -> None:
    remap_items_to_flattened_folders(items, root_folder)
    for index, item in enumerate(items, start=1):
        print(f"[{index}/{len(items)}] Importing: {item.title} -> {item.destination_folder}")
        flat.import_one_item(item, attach_assets=attach_assets)
        if delay > 0 and index < len(items):
            time.sleep(delay)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Import OneNote Liberation export into Apple Notes path folders.")
    parser.add_argument("input", help="Path to either a .metadata.json file or an export directory.")
    parser.add_argument("--folder", default=DEFAULT_FOLDER, help="Apple Notes root folder prefix.")
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
