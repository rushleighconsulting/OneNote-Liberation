#!/usr/bin/env python3
"""Audit a OneNote Liberation export for migration fidelity risks."""

from __future__ import annotations

import argparse
import html
import json
import pathlib
import sys
from collections import Counter
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


def audit_one(root: pathlib.Path, metadata_path: pathlib.Path) -> dict[str, Any]:
    metadata = read_json(metadata_path)
    title = metadata.get("title") or "Untitled"
    html_rel = metadata.get("files", {}).get("html")
    item: dict[str, Any] = {
        "title": title,
        "metadata": str(metadata_path.relative_to(root)),
        "images": 0,
        "objects": 0,
        "embeds": 0,
        "iframes": 0,
        "checkboxes": 0,
        "links": 0,
        "tables": 0,
        "assets": len(metadata.get("assets", []) or []),
    }
    if not html_rel:
        item["missing_html"] = True
        return item
    html_path = root / html_rel
    if not html_path.exists():
        item["missing_html"] = True
        return item

    soup = BeautifulSoup(html_path.read_text(encoding="utf-8"), "html.parser")
    item["images"] = len(soup.find_all("img"))
    item["objects"] = len(soup.find_all("object"))
    item["embeds"] = len(soup.find_all("embed"))
    item["iframes"] = len(soup.find_all("iframe"))
    item["checkboxes"] = len(soup.find_all("input", {"type": "checkbox"}))
    item["links"] = len(soup.find_all("a"))
    item["tables"] = len(soup.find_all("table"))
    return item


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Audit a OneNote Liberation export.")
    parser.add_argument("export", help="Export folder")
    parser.add_argument("--show-pages", action="store_true", help="List pages with possible fidelity risks")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    root = find_export_root(pathlib.Path(args.export))
    files = metadata_files(root)
    items = [audit_one(root, path) for path in files]

    totals = Counter()
    for item in items:
        for key in ["images", "objects", "embeds", "iframes", "checkboxes", "links", "tables", "assets"]:
            totals[key] += int(item.get(key, 0))
        if item.get("missing_html"):
            totals["missing_html"] += 1

    print("OneNote Liberation audit")
    print("------------------------")
    print(f"Export root: {root}")
    print(f"Pages:       {len(items)}")
    print(f"Assets:      {totals['assets']}")
    print("")
    print("HTML features")
    print("-------------")
    print(f"Images:      {totals['images']}")
    print(f"Objects:     {totals['objects']}")
    print(f"Embeds:      {totals['embeds']}")
    print(f"Iframes:     {totals['iframes']}")
    print(f"Checkboxes:  {totals['checkboxes']}")
    print(f"Links:       {totals['links']}")
    print(f"Tables:      {totals['tables']}")
    print(f"Missing HTML:{totals['missing_html']}")

    risk_pages = [
        item for item in items
        if item.get("missing_html") or item["objects"] or item["embeds"] or item["iframes"] or item["checkboxes"]
    ]
    print("")
    print("Potential fidelity risks")
    print("------------------------")
    print(f"Pages with objects/embeds/iframes/checkboxes/missing HTML: {len(risk_pages)}")
    if args.show_pages and risk_pages:
        for item in risk_pages:
            flags = []
            for key in ["missing_html", "objects", "embeds", "iframes", "checkboxes"]:
                value = item.get(key)
                if value:
                    flags.append(f"{key}={value}")
            print(f"- {item['title']} ({', '.join(flags)})")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"ERROR: {html.escape(str(exc))}", file=sys.stderr)
        sys.exit(1)
