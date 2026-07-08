#!/usr/bin/env python3
"""Create and verify a migration manifest for a OneNote Liberation export.

This is deliberately local-only. It does not call Microsoft Graph and does not
write to Apple Notes. It provides an audit baseline before deleting OneNote.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import sys
from collections import Counter
from datetime import datetime, timezone
from typing import Any

from bs4 import BeautifulSoup

from . import __version__
from .assets import detect_file

MANIFEST_NAME = "migration_manifest.json"


def sha256_file(path: pathlib.Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


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
    return sorted((root / "pages").rglob("*.metadata.json")) if (root / "pages").exists() else []


def exported_page_files(root: pathlib.Path) -> list[pathlib.Path]:
    files = []
    for metadata in metadata_files(root):
        try:
            data = read_json(metadata)
            rel_html = data.get("files", {}).get("html")
            if rel_html:
                html_path = root / rel_html
                if html_path.exists():
                    files.append(html_path)
        except Exception:
            continue
    return sorted(set(files))


def asset_files(root: pathlib.Path) -> list[pathlib.Path]:
    assets = root / "assets"
    return sorted(path for path in assets.rglob("*") if path.is_file()) if assets.exists() else []


def html_counts(path: pathlib.Path) -> Counter[str]:
    soup = BeautifulSoup(path.read_text(encoding="utf-8", errors="replace"), "html.parser")
    return Counter(
        {
            "links": len(soup.find_all("a")),
            "images": len(soup.find_all("img")),
            "objects": len(soup.find_all("object")),
            "embeds": len(soup.find_all("embed")),
            "iframes": len(soup.find_all("iframe")),
            "tables": len(soup.find_all("table")),
            "checkbox_inputs": len(soup.find_all("input", {"type": "checkbox"})),
        }
    )


def build_manifest(root: pathlib.Path) -> dict[str, Any]:
    root = find_export_root(root)
    page_metadata = metadata_files(root)
    page_html = exported_page_files(root)
    assets = asset_files(root)

    asset_type_counts: Counter[str] = Counter()
    asset_records = []
    for path in assets:
        try:
            info = detect_file(path)
            asset_type_counts[info.asset_type] += 1
            asset_records.append(
                {
                    "path": str(path.relative_to(root)),
                    "size_bytes": path.stat().st_size,
                    "sha256": info.sha256,
                    "asset_type": info.asset_type,
                    "mime_type": info.mime_type,
                    "extension": info.extension,
                }
            )
        except Exception as exc:
            asset_type_counts["error"] += 1
            asset_records.append(
                {
                    "path": str(path.relative_to(root)),
                    "size_bytes": path.stat().st_size,
                    "sha256": sha256_file(path),
                    "asset_type": "error",
                    "error": str(exc),
                }
            )

    html_totals: Counter[str] = Counter()
    page_records = []
    for metadata_path in page_metadata:
        metadata = read_json(metadata_path)
        html_path = root / metadata.get("files", {}).get("html", "")
        counts = html_counts(html_path) if html_path.exists() else Counter()
        html_totals.update(counts)
        page_records.append(
            {
                "title": metadata.get("title"),
                "metadata": str(metadata_path.relative_to(root)),
                "html": str(html_path.relative_to(root)) if html_path.exists() else None,
                "metadata_sha256": sha256_file(metadata_path),
                "html_sha256": sha256_file(html_path) if html_path.exists() else None,
                "hierarchy": metadata.get("hierarchy", {}),
                "html_counts": dict(counts),
            }
        )

    return {
        "manifest_version": 1,
        "tool": "OneNote Liberation",
        "tool_version": __version__,
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "export_root": str(root),
        "summary": {
            "pages_metadata": len(page_metadata),
            "pages_html": len(page_html),
            "assets": len(assets),
            "asset_type_counts": dict(asset_type_counts),
            "html_totals": dict(html_totals),
        },
        "pages": page_records,
        "assets": asset_records,
        "files": {
            "index_html_sha256": sha256_file(root / "index.html") if (root / "index.html").exists() else None,
            "export_report_sha256": sha256_file(root / "export_report.json") if (root / "export_report.json").exists() else None,
        },
    }


def save_manifest(root: pathlib.Path) -> pathlib.Path:
    root = find_export_root(root)
    manifest = build_manifest(root)
    path = root / MANIFEST_NAME
    path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return path


def print_summary(manifest: dict[str, Any]) -> None:
    summary = manifest.get("summary", {})
    print("Migration manifest")
    print("------------------")
    print(f"Tool version:    {manifest.get('tool_version')}")
    print(f"Export root:     {manifest.get('export_root')}")
    print(f"Pages metadata:  {summary.get('pages_metadata', 0)}")
    print(f"Pages HTML:      {summary.get('pages_html', 0)}")
    print(f"Assets:          {summary.get('assets', 0)}")

    print("\nAsset types")
    print("-----------")
    asset_counts = summary.get("asset_type_counts", {})
    if not asset_counts:
        print("None")
    else:
        for key, value in sorted(asset_counts.items()):
            print(f"{key}: {value}")

    print("\nHTML totals")
    print("-----------")
    html_totals = summary.get("html_totals", {})
    if not html_totals:
        print("None")
    else:
        for key, value in sorted(html_totals.items()):
            print(f"{key}: {value}")


def verify_manifest(root: pathlib.Path) -> int:
    root = find_export_root(root)
    manifest_path = root / MANIFEST_NAME
    if not manifest_path.exists():
        print(f"FAIL: missing {MANIFEST_NAME}")
        return 1

    manifest = read_json(manifest_path)
    failures = []

    for page in manifest.get("pages", []):
        metadata_rel = page.get("metadata")
        html_rel = page.get("html")
        if metadata_rel:
            metadata_path = root / metadata_rel
            if not metadata_path.exists():
                failures.append(f"missing metadata: {metadata_rel}")
            elif sha256_file(metadata_path) != page.get("metadata_sha256"):
                failures.append(f"metadata changed: {metadata_rel}")
        if html_rel:
            html_path = root / html_rel
            if not html_path.exists():
                failures.append(f"missing html: {html_rel}")
            elif sha256_file(html_path) != page.get("html_sha256"):
                failures.append(f"html changed: {html_rel}")

    for asset in manifest.get("assets", []):
        asset_rel = asset.get("path")
        if not asset_rel:
            continue
        asset_path = root / asset_rel
        if not asset_path.exists():
            failures.append(f"missing asset: {asset_rel}")
        elif sha256_file(asset_path) != asset.get("sha256"):
            failures.append(f"asset changed: {asset_rel}")

    print("Manifest verification")
    print("---------------------")
    if failures:
        print(f"FAIL: {len(failures)} issue(s)")
        for failure in failures[:50]:
            print(f"- {failure}")
        if len(failures) > 50:
            print(f"... {len(failures) - 50} more")
        return 1

    print("PASS: manifest matches export files")
    print_summary(manifest)
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Create or verify a OneNote Liberation migration manifest.")
    sub = parser.add_subparsers(dest="command", required=True)

    create = sub.add_parser("create", help="Create migration_manifest.json in an export folder.")
    create.add_argument("export", help="Export folder")

    verify = sub.add_parser("verify", help="Verify export files against migration_manifest.json.")
    verify.add_argument("export", help="Export folder")

    show = sub.add_parser("show", help="Show summary from migration_manifest.json.")
    show.add_argument("export", help="Export folder")

    return parser.parse_args()


def main() -> None:
    args = parse_args()
    root = pathlib.Path(args.export).expanduser().resolve()

    if args.command == "create":
        path = save_manifest(root)
        manifest = read_json(path)
        print_summary(manifest)
        print(f"\nWrote: {path}")
    elif args.command == "verify":
        raise SystemExit(verify_manifest(root))
    elif args.command == "show":
        manifest_path = find_export_root(root) / MANIFEST_NAME
        print_summary(read_json(manifest_path))


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)
