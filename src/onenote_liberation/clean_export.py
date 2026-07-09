#!/usr/bin/env python3
"""Clean exported HTML before importing into Apple Notes.

Local-only command. It edits an export folder in place:
- removes duplicate first heading matching the note title
- removes the exporter banner near the top of the exported page
- removes leading empty paragraphs/blocks
- converts HTML checkbox inputs into portable Unicode checkbox marks
- converts OneNote data-tag to-do spans into portable Unicode checkbox marks
- appends provenance at the bottom by default
"""

from __future__ import annotations

import argparse
import html
import json
import pathlib
import re
import sys
from typing import Any

from bs4 import BeautifulSoup, Tag

PROVENANCE_TEXT = "Imported from Microsoft OneNote by OneNote Liberation"


def normalise_text(value: str) -> str:
    return re.sub(r"\s+", " ", value or "").strip().lower()


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


def is_empty_tag(tag: Tag) -> bool:
    if tag.find(["img", "table", "object", "embed", "iframe"]):
        return False
    return not tag.get_text(strip=True)


def remove_leading_empty_blocks(container: Tag) -> int:
    count = 0
    while True:
        first = next((child for child in container.children if isinstance(child, Tag)), None)
        if first is None or not is_empty_tag(first):
            return count
        first.decompose()
        count += 1


def remove_duplicate_title(container: Tag, title: str) -> int:
    wanted = normalise_text(title)
    for child in list(container.children):
        if not isinstance(child, Tag):
            continue
        if is_empty_tag(child):
            child.decompose()
            continue
        if child.name in {"h1", "h2"} and normalise_text(child.get_text(" ", strip=True)) == wanted:
            child.decompose()
            return 1
        return 0
    return 0


def remove_exporter_provenance(container: Tag) -> int:
    count = 0
    for tag in list(container.find_all(True)):
        text = normalise_text(tag.get_text(" ", strip=True))
        if "exported from onenote by onenote liberation" in text:
            tag.decompose()
            count += 1
    return count


def remove_old_bottom_provenance(container: Tag) -> int:
    count = 0
    for tag in list(container.find_all(True)):
        text = normalise_text(tag.get_text(" ", strip=True))
        if "imported from microsoft onenote by onenote liberation" in text:
            tag.decompose()
            count += 1
    return count


def convert_checkboxes(soup: BeautifulSoup, container: Tag) -> int:
    """Convert HTML checkbox inputs to portable Unicode marks.

    Apple Notes does not preserve arbitrary HTML input controls through the
    AppleScript import path. Unicode checkbox glyphs are stable and searchable.
    """
    count = 0
    for checkbox in list(container.find_all("input")):
        input_type = normalise_text(str(checkbox.get("type", "")))
        if input_type != "checkbox":
            continue
        checked = checkbox.has_attr("checked") or str(checkbox.get("aria-checked", "")).lower() == "true"
        mark = "☑ " if checked else "☐ "
        checkbox.replace_with(soup.new_string(mark))
        count += 1
    return count


def convert_onenote_todo_tags(soup: BeautifulSoup, container: Tag) -> int:
    """Convert OneNote HTML data-tag to-do spans into Unicode checkboxes.

    OneNote exports checkbox/tag items as spans such as:
        <span data-tag="to-do">Task</span>
        <span data-tag="to-do:completed">Task</span>

    Apple Notes does not understand those OneNote-specific data tags, so convert
    them to stable text markers while preserving the rest of the span contents.
    """
    count = 0
    for tag in list(container.find_all(attrs={"data-tag": True})):
        data_tag = normalise_text(str(tag.get("data-tag", "")))
        if data_tag == "to-do:completed":
            mark = "☑ "
        elif data_tag == "to-do":
            mark = "☐ "
        else:
            continue

        tag.insert(0, soup.new_string(mark))
        del tag["data-tag"]
        count += 1
    return count


def append_provenance(soup: BeautifulSoup, container: Tag, metadata: dict[str, Any], mode: str) -> int:
    if mode == "none":
        return 0

    tool_version = metadata.get("tool_version") or "unknown version"
    created = metadata.get("created")
    modified = metadata.get("modified")

    bits = [f"{PROVENANCE_TEXT} {tool_version}"]
    if created:
        bits.append(f"created {created}")
    if modified:
        bits.append(f"modified {modified}")

    p = soup.new_tag("p")
    p["style"] = "color:#777; font-size:0.85em; margin-top:2em;"
    p.string = " • ".join(bits)

    if mode == "top":
        container.insert(0, p)
    else:
        container.append(p)
    return 1


def clean_one(root: pathlib.Path, metadata_path: pathlib.Path, provenance: str) -> dict[str, Any]:
    metadata = read_json(metadata_path)
    title = metadata.get("title") or "Untitled"
    html_rel = metadata.get("files", {}).get("html")
    if not html_rel:
        return {"metadata": str(metadata_path), "changed": False, "reason": "missing files.html"}

    html_path = root / html_rel
    if not html_path.exists():
        return {"metadata": str(metadata_path), "changed": False, "reason": "html missing"}

    soup = BeautifulSoup(html_path.read_text(encoding="utf-8"), "html.parser")
    container = soup.body if soup.body else soup

    changes = 0
    changes += remove_exporter_provenance(container)
    changes += remove_old_bottom_provenance(container)
    changes += remove_duplicate_title(container, title)
    changes += remove_leading_empty_blocks(container)
    changes += convert_checkboxes(soup, container)
    changes += convert_onenote_todo_tags(soup, container)
    changes += append_provenance(soup, container, metadata, provenance)

    if changes:
        html_path.write_text(str(soup), encoding="utf-8")

    return {
        "title": title,
        "html": str(html_path.relative_to(root)),
        "changed": bool(changes),
        "changes": changes,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Clean a OneNote Liberation export before Apple Notes import.")
    parser.add_argument("export", help="Export folder")
    parser.add_argument("--provenance", choices=["bottom", "top", "none"], default="bottom")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    root = find_export_root(pathlib.Path(args.export))
    files = metadata_files(root)
    if args.limit is not None:
        files = files[: args.limit]

    results = []
    for metadata_path in files:
        if args.dry_run:
            metadata = read_json(metadata_path)
            results.append({"title": metadata.get("title"), "metadata": str(metadata_path.relative_to(root))})
        else:
            results.append(clean_one(root, metadata_path, args.provenance))

    changed = sum(1 for item in results if item.get("changed"))
    print(f"Export: {root}")
    print(f"Pages inspected: {len(results)}")
    if args.dry_run:
        print("Dry run only. Nothing was changed.")
    else:
        print(f"Pages changed: {changed}")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"ERROR: {html.escape(str(exc))}", file=sys.stderr)
        sys.exit(1)
