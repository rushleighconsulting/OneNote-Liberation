#!/usr/bin/env python3
"""Fidelity cleanup for exported HTML.

Currently converts HTML checkbox inputs into plain text markers that survive
Apple Notes import:
- checked checkboxes become [x]
- unchecked checkboxes become [ ]
"""

from __future__ import annotations

import argparse
import html
import json
import pathlib
import sys
from typing import Any

from bs4 import BeautifulSoup


def find_export_root(path: pathlib.Path) -> pathlib.Path:
    path = path.expanduser().resolve()
    if path.is_file():
        path = path.parent
    for candidate in [path, *path.parents]:
        if (candidate / "index.html").exists() or (candidate / "export_report.json").exists():
            return candidate
    raise FileNotFoundError("Could not find export root containing index.html or export_report.json")


def read_json(path: pathlib.Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def metadata_files(root: pathlib.Path) -> list[pathlib.Path]:
    pages = root / "pages"
    return sorted(pages.rglob("*.metadata.json")) if pages.exists() else []


def convert_checkboxes(html_path: pathlib.Path) -> int:
    soup = BeautifulSoup(html_path.read_text(encoding="utf-8"), "html.parser")
    count = 0
    for checkbox in list(soup.find_all("input")):
        if str(checkbox.get("type", "")).lower() != "checkbox":
            continue
        checked = checkbox.has_attr("checked") or str(checkbox.get("aria-checked", "")).lower() == "true"
        checkbox.replace_with(soup.new_string("[x] " if checked else "[ ] "))
        count += 1
    if count:
        html_path.write_text(str(soup), encoding="utf-8")
    return count


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Apply extra fidelity cleanup to a OneNote Liberation export.")
    parser.add_argument("export", help="Export folder")
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    root = find_export_root(pathlib.Path(args.export))
    files = metadata_files(root)
    pages_changed = 0
    checkboxes = 0

    for metadata_path in files:
        metadata = read_json(metadata_path)
        html_rel = metadata.get("files", {}).get("html")
        if not html_rel:
            continue
        html_path = root / html_rel
        if not html_path.exists():
            continue
        if args.dry_run:
            soup = BeautifulSoup(html_path.read_text(encoding="utf-8"), "html.parser")
            count = len(soup.find_all("input", {"type": "checkbox"}))
        else:
            count = convert_checkboxes(html_path)
        if count:
            pages_changed += 1
            checkboxes += count

    print(f"Export: {root}")
    print(f"Pages inspected: {len(files)}")
    print(f"Pages with checkboxes: {pages_changed}")
    print(f"Checkboxes converted: {checkboxes}")
    if args.dry_run:
        print("Dry run only. Nothing was changed.")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"ERROR: {html.escape(str(exc))}", file=sys.stderr)
        sys.exit(1)
