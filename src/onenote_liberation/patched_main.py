"""Patched exporter entry point.

This keeps the original fast-moving prototype exporter intact while patching:
- asset saving so downloaded resources get correct file extensions based on
  magic-byte detection rather than weak Microsoft Graph Content-Type headers
- section page fetching so large sections are not accidentally capped at 20 pages
"""

from __future__ import annotations

import os
import pathlib
import time
from typing import Any

from bs4 import BeautifulSoup

from . import main as legacy
from .assets import detect_from_bytes


VERSION = "0.13.2"
legacy.VERSION = VERSION


def download_and_rewrite_images(
    token: str,
    soup: BeautifulSoup,
    page_output_path: pathlib.Path,
    page_id: str,
    paths: Any,
    options: Any,
) -> list[dict[str, Any]]:
    downloaded: list[dict[str, Any]] = []
    images = soup.find_all("img")

    if not images:
        return downloaded

    if not options.include_images:
        for index, img in enumerate(images, start=1):
            downloaded.append({"index": index, "status": "not downloaded (--no-images)"})
        return downloaded

    page_asset_dir = paths.assets / legacy.slugify(page_id.replace("!", "-"))
    page_asset_dir.mkdir(parents=True, exist_ok=True)

    for index, img in enumerate(images, start=1):
        if options.image_delay > 0:
            time.sleep(options.image_delay)

        src = img.get("src")
        data_fullres = img.get("data-fullres-src")
        candidate_url = data_fullres or src

        if not candidate_url:
            downloaded.append({"index": index, "status": "missing src"})
            continue

        if candidate_url.startswith("data:"):
            downloaded.append({"index": index, "status": "embedded data uri"})
            continue

        try:
            response = legacy.graph_get(
                token,
                candidate_url,
                accept="*/*",
                max_retry_after=options.max_retry_after,
            )
            declared_content_type = response.headers.get("Content-Type", "")
            asset_info = detect_from_bytes(response.content, declared_mime_type=declared_content_type)
            digest = asset_info.sha256[:12]
            asset_name = f"image-{index}-{digest}{asset_info.extension}"
            asset_path = page_asset_dir / asset_name
            asset_path.write_bytes(response.content)

            img["src"] = os.path.relpath(asset_path, start=page_output_path.parent)
            if img.has_attr("data-fullres-src"):
                del img["data-fullres-src"]

            downloaded.append(
                {
                    "index": index,
                    "status": "downloaded",
                    "asset_type": asset_info.asset_type,
                    "mime_type": asset_info.mime_type,
                    "declared_content_type": declared_content_type,
                    "extension": asset_info.extension,
                    "sha256": asset_info.sha256,
                    "asset": str(asset_path.relative_to(paths.root)),
                }
            )

        except Exception as exc:
            downloaded.append(
                {
                    "index": index,
                    "status": f"failed: {exc}",
                    "source": candidate_url,
                }
            )

    return downloaded


def export_section(
    token: str,
    section: dict[str, Any],
    path_parts: list[str],
    options: Any,
    paths: Any,
) -> dict[str, Any]:
    section_name = section.get("displayName", "Untitled section")
    section_id = section["id"]
    current_path = path_parts + [section_name]

    print(f"    Section: {section_name}")

    result: dict[str, Any] = {
        "type": "section",
        "name": section_name,
        "id": section_id,
        "path": current_path,
        "pages": [],
        "error": None,
    }

    if not legacy.section_matches_filter(current_path, options):
        print("      SKIP section filter")
        result["error"] = "Skipped by section filter"
        return result

    if legacy.looks_sensitive(current_path) and not options.include_sensitive:
        print("      SKIP sensitive-looking section")
        result["error"] = "Skipped sensitive-looking section"
        return result

    try:
        pages = legacy.get_all_values(
            token,
            f"/me/onenote/sections/{section_id}/pages?$top=100&$select=id,title,createdDateTime,lastModifiedDateTime",
            options,
            label="pages",
        )

        for page in pages:
            try:
                result["pages"].append(legacy.export_page(token, page, current_path, options, paths))
            except Exception as exc:
                print(f"      ERROR exporting page {page.get('title')}: {exc}")
                result["pages"].append(
                    {
                        "title": page.get("title"),
                        "id": page.get("id"),
                        "path": None,
                        "metadata_path": None,
                        "skipped": False,
                        "reason": f"Export error: {exc}",
                        "images": [],
                    }
                )

    except Exception as exc:
        print(f"      ERROR reading section: {exc}")
        result["error"] = str(exc)

    return result


legacy.download_and_rewrite_images = download_and_rewrite_images
legacy.export_section = export_section
main = legacy.main
