#!/usr/bin/env python3
"""Inspect a OneNote Liberation export.

This is a local diagnostics tool. It does not call Microsoft Graph and does
not write to Apple Notes. It reads exported HTML, metadata JSON, and assets.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys
from collections import Counter
from typing import Any

from bs4 import BeautifulSoup

from .assets import detect_file


def metadata_files_from_input(input_path: pathlib.Path) -> list[pathlib.Path]:
    if input_path.is_file():
        if input_path.name.endswith(".metadata.json"):
            return [input_path]
        raise ValueError("Input file must be a .metadata.json file")

    if not input_path.is_dir():
        raise FileNotFoundError(input_path)

    return sorted(input_path.rglob("*.metadata.json"))


def find_export_root(path: pathlib.Path) -> pathlib.Path:
    current = path if path.is_dir() else path.parent
    for candidate in [current, *current.parents]:
        if (candidate / "index.html").exists() or (candidate / "export_report.json").exists():
            return candidate
    raise FileNotFoundError("Could not find export root containing index.html or export_report.json")


def read_metadata(path: pathlib.Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def html_path_from_metadata(metadata_path: pathlib.Path, metadata: dict[str, Any]) -> pathlib.Path:
    html_rel = metadata.get("files", {}).get("html")
    if not html_rel:
        raise ValueError(f"{metadata_path} does not contain files.html")
    return find_export_root(metadata_path) / html_rel


def asset_paths_from_metadata(metadata_path: pathlib.Path, metadata: dict[str, Any]) -> list[pathlib.Path]:
    root = find_export_root(metadata_path)
    paths: list[pathlib.Path] = []
    for key in ("assets", "images"):
        for asset_info in metadata.get(key, []) or []:
            if asset_info.get("status") != "downloaded":
                continue
            asset = asset_info.get("asset") or asset_info.get("path")
            if not asset:
                continue
            candidate = (root / asset).resolve()
            if candidate.exists() and candidate not in paths:
                paths.append(candidate)
    return paths


def inspect_html(html_path: pathlib.Path) -> dict[str, Any]:
    soup = BeautifulSoup(html_path.read_text(encoding="utf-8"), "html.parser")
    tags = Counter(tag.name for tag in soup.find_all())
    links = soup.find_all("a")
    imgs = soup.find_all("img")
    objects = soup.find_all("object")
    embeds = soup.find_all("embed")
    iframes = soup.find_all("iframe")

    return {
        "tag_counts": dict(tags),
        "link_count": len(links),
        "image_tag_count": len(imgs),
        "object_tag_count": len(objects),
        "embed_tag_count": len(embeds),
        "iframe_tag_count": len(iframes),
        "image_srcs": [img.get("src") for img in imgs if img.get("src")],
        "object_data": [obj.get("data") for obj in objects if obj.get("data")],
        "embed_srcs": [embed.get("src") for embed in embeds if embed.get("src")],
    }


def inspect_metadata_file(metadata_path: pathlib.Path) -> dict[str, Any]:
    metadata = read_metadata(metadata_path)
    html_path = html_path_from_metadata(metadata_path, metadata)
    assets = asset_paths_from_metadata(metadata_path, metadata)

    asset_summaries = []
    asset_type_counts: Counter[str] = Counter()
    for asset in assets:
        try:
            info = detect_file(asset)
            asset_type_counts[info.asset_type] += 1
            asset_summaries.append(
                {
                    "path": str(asset),
                    "asset_type": info.asset_type,
                    "mime_type": info.mime_type,
                    "extension": info.extension,
                    "sha256": info.sha256,
                    "size_bytes": asset.stat().st_size,
                }
            )
        except Exception as exc:
            asset_type_counts["error"] += 1
            asset_summaries.append({"path": str(asset), "error": str(exc)})

    html_summary = inspect_html(html_path) if html_path.exists() else {"error": "HTML missing"}

    return {
        "title": metadata.get("title"),
        "metadata_path": str(metadata_path),
        "html_path": str(html_path),
        "hierarchy": metadata.get("hierarchy", {}),
        "html": html_summary,
        "assets": asset_summaries,
        "asset_type_counts": dict(asset_type_counts),
    }


def print_single_report(report: dict[str, Any], verbose: bool) -> None:
    print("\nPage")
    print("----")
    print(f"Title: {report.get('title')}")
    print(f"HTML: {report.get('html_path')}")

    hierarchy = report.get("hierarchy", {})
    path_parts = hierarchy.get("path_parts") or []
    if path_parts:
        print(f"Path: {' / '.join(path_parts)}")

    html_summary = report.get("html", {})
    print("\nHTML contents")
    print("-------------")
    print(f"Links:   {html_summary.get('link_count', 0)}")
    print(f"Images:  {html_summary.get('image_tag_count', 0)}")
    print(f"Objects: {html_summary.get('object_tag_count', 0)}")
    print(f"Embeds:  {html_summary.get('embed_tag_count', 0)}")
    print(f"Iframes: {html_summary.get('iframe_tag_count', 0)}")

    print("\nExported assets")
    print("---------------")
    counts = report.get("asset_type_counts", {})
    if not counts:
        print("None")
    else:
        for asset_type, count in sorted(counts.items()):
            print(f"{asset_type}: {count}")

    if verbose:
        print("\nAsset details")
        print("-------------")
        for asset in report.get("assets", []):
            print(json.dumps(asset, indent=2))

        print("\nImage srcs")
        print("----------")
        for src in html_summary.get("image_srcs", []):
            print(src)


def print_aggregate(reports: list[dict[str, Any]]) -> None:
    print("\nAggregate")
    print("---------")
    print(f"Pages inspected: {len(reports)}")

    html_counts = Counter()
    asset_counts = Counter()
    for report in reports:
        html_summary = report.get("html", {})
        html_counts["links"] += html_summary.get("link_count", 0)
        html_counts["img"] += html_summary.get("image_tag_count", 0)
        html_counts["object"] += html_summary.get("object_tag_count", 0)
        html_counts["embed"] += html_summary.get("embed_tag_count", 0)
        html_counts["iframe"] += html_summary.get("iframe_tag_count", 0)
        asset_counts.update(report.get("asset_type_counts", {}))

    print("\nHTML totals")
    for key, value in html_counts.items():
        print(f"{key}: {value}")

    print("\nAsset totals")
    if not asset_counts:
        print("None")
    else:
        for key, value in sorted(asset_counts.items()):
            print(f"{key}: {value}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Inspect a OneNote Liberation export.")
    parser.add_argument("input", help="Path to a .metadata.json file or export directory.")
    parser.add_argument("--limit", type=int, default=None, help="Inspect only first N metadata files.")
    parser.add_argument("--verbose", action="store_true", help="Print asset details and image sources.")
    parser.add_argument("--json", action="store_true", help="Output JSON instead of text.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    input_path = pathlib.Path(args.input).expanduser().resolve()
    files = metadata_files_from_input(input_path)
    if args.limit is not None:
        files = files[: args.limit]

    reports = [inspect_metadata_file(path) for path in files]

    if args.json:
        print(json.dumps(reports, indent=2))
        return

    if len(reports) == 1:
        print_single_report(reports[0], verbose=args.verbose)
    else:
        print_aggregate(reports)
        if args.verbose:
            for report in reports:
                print_single_report(report, verbose=True)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)
