#!/usr/bin/env python3
"""
OneNote Liberation

Read-only OneNote HTML exporter.

Current capabilities:
- Microsoft device-code sign-in
- OneNote hierarchy traversal
- Local HTML export
- Nested index.html
- Optional local image download and HTML rewrite
- Per-page metadata JSON
- Sensitive-looking sections/pages skipped by default
- Retry-After aware throttling
- Section filtering and skip-existing reruns
"""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import mimetypes
import os
import pathlib
import re
import sys
import time
from dataclasses import dataclass
from typing import Any

import msal
import requests
from bs4 import BeautifulSoup


VERSION = "0.6.0"
CLIENT_ID = "5e754056-cd85-4272-bea0-ab1696b2f92e"
AUTHORITY = "https://login.microsoftonline.com/common"
SCOPES = ["Notes.Read"]
GRAPH = "https://graph.microsoft.com/v1.0"

TRANSIENT_STATUS_CODES = {408, 429, 500, 502, 503, 504}

SENSITIVE_PATTERNS = [
    "password",
    "passwords",
    "cred",
    "creds",
    "credential",
    "credentials",
    "recovery code",
    "recovery codes",
    "api key",
    "secret",
    "2fa",
    "backup code",
    "backup codes",
]


@dataclass
class ExportPaths:
    root: pathlib.Path

    @property
    def pages(self) -> pathlib.Path:
        return self.root / "pages"

    @property
    def assets(self) -> pathlib.Path:
        return self.root / "assets"

    @property
    def index(self) -> pathlib.Path:
        return self.root / "index.html"

    @property
    def report(self) -> pathlib.Path:
        return self.root / "export_report.json"

    def create(self) -> None:
        self.pages.mkdir(parents=True, exist_ok=True)
        self.assets.mkdir(parents=True, exist_ok=True)


@dataclass
class ExportOptions:
    include_sensitive: bool
    include_images: bool
    skip_existing: bool
    section_filter: str | None
    image_delay: float
    max_retry_after: int


def slugify(value: str, fallback: str = "untitled") -> str:
    value = value.strip().lower()
    value = re.sub(r"[^\w\s.-]", "", value)
    value = re.sub(r"\s+", "-", value)
    value = re.sub(r"-+", "-", value)
    value = value.strip(".-_")
    return value[:100] or fallback


def looks_sensitive(path_parts: list[str]) -> bool:
    text = " / ".join(path_parts).lower()
    return any(pattern in text for pattern in SENSITIVE_PATTERNS)


def ensure_unique_path(path: pathlib.Path) -> pathlib.Path:
    if not path.exists():
        return path

    stem = path.stem
    suffix = path.suffix
    parent = path.parent

    for index in range(2, 10000):
        candidate = parent / f"{stem}-{index}{suffix}"
        if not candidate.exists():
            return candidate

    raise RuntimeError(f"Could not create unique filename for {path}")


def stable_page_path(paths: ExportPaths, path_parts: list[str], title: str) -> pathlib.Path:
    rel_folder = pathlib.Path(*[slugify(part) for part in path_parts])
    folder = paths.pages / rel_folder
    folder.mkdir(parents=True, exist_ok=True)
    return folder / f"{slugify(title)}.html"


def retry_after_seconds(response: requests.Response, fallback: int, max_wait: int) -> int:
    header = response.headers.get("Retry-After")
    if header:
        try:
            return min(max(int(float(header)), 1), max_wait)
        except ValueError:
            pass
    return min(fallback, max_wait)


def graph_get(
    token: str,
    path_or_url: str,
    accept: str = "application/json",
    retries: int = 8,
    max_retry_after: int = 120,
) -> requests.Response:
    url = path_or_url if path_or_url.startswith("https://") else GRAPH + path_or_url
    last_response: requests.Response | None = None

    for attempt in range(1, retries + 1):
        response = requests.get(
            url,
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": accept,
            },
            timeout=120,
        )
        last_response = response

        if response.ok:
            return response

        if response.status_code in TRANSIENT_STATUS_CODES and attempt < retries:
            fallback = min(8 * attempt, 60)
            wait = retry_after_seconds(response, fallback=fallback, max_wait=max_retry_after)
            print(f"Graph returned {response.status_code}; waiting {wait}s before retry {attempt + 1}/{retries}...")
            time.sleep(wait)
            continue

        print("\nGraph request failed")
        print(f"URL: {url}")
        print(f"Status: {response.status_code}")
        try:
            print(json.dumps(response.json(), indent=2))
        except Exception:
            print(response.text[:2000])
        response.raise_for_status()

    assert last_response is not None
    last_response.raise_for_status()
    return last_response


def graph_get_json(token: str, path: str, options: ExportOptions) -> dict[str, Any]:
    return graph_get(token, path, max_retry_after=options.max_retry_after).json()


def get_all_values(token: str, path: str, options: ExportOptions, label: str = "items") -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    next_path: str | None = path

    while next_path:
        raw_path = next_path.replace(GRAPH, "") if next_path.startswith(GRAPH) else next_path
        data = graph_get_json(token, raw_path, options)
        batch = data.get("value", [])
        items.extend(batch)

        if batch:
            print(f"  fetched {len(batch)} {label} (total {len(items)})")

        next_path = data.get("@odata.nextLink")

    return items


def sign_in() -> str:
    app = msal.PublicClientApplication(CLIENT_ID, authority=AUTHORITY)
    flow = app.initiate_device_flow(scopes=SCOPES)

    if "user_code" not in flow:
        print("\nCould not create device login flow. Microsoft returned:")
        print(json.dumps(flow, indent=2))
        raise RuntimeError("Could not create device login flow.")

    print("\nMicrosoft sign-in required:\n")
    print(flow["message"])
    print("\nSign in using the Microsoft account that owns the OneNote notebook.\n")

    result = app.acquire_token_by_device_flow(flow)

    if "access_token" not in result:
        print("\nLogin failed. Microsoft returned:")
        print(json.dumps(result, indent=2))
        raise RuntimeError("Login failed.")

    return result["access_token"]


def guess_extension(content_type: str, fallback: str = ".bin") -> str:
    if not content_type:
        return fallback
    content_type = content_type.split(";")[0].strip().lower()
    ext = mimetypes.guess_extension(content_type)
    if ext == ".jpe":
        return ".jpg"
    return ext or fallback


def download_and_rewrite_images(
    token: str,
    soup: BeautifulSoup,
    page_output_path: pathlib.Path,
    page_id: str,
    paths: ExportPaths,
    options: ExportOptions,
) -> list[dict[str, Any]]:
    downloaded: list[dict[str, Any]] = []
    images = soup.find_all("img")

    if not images:
        return downloaded

    if not options.include_images:
        for index, img in enumerate(images, start=1):
            downloaded.append({"index": index, "status": "not downloaded (--no-images)"})
        return downloaded

    page_asset_dir = paths.assets / slugify(page_id.replace("!", "-"))
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
            response = graph_get(
                token,
                candidate_url,
                accept="*/*",
                max_retry_after=options.max_retry_after,
            )
            content_type = response.headers.get("Content-Type", "")
            ext = guess_extension(content_type, ".img")
            digest = hashlib.sha256(response.content).hexdigest()[:12]
            asset_name = f"image-{index}-{digest}{ext}"
            asset_path = page_asset_dir / asset_name
            asset_path.write_bytes(response.content)

            img["src"] = os.path.relpath(asset_path, start=page_output_path.parent)
            if img.has_attr("data-fullres-src"):
                del img["data-fullres-src"]

            downloaded.append(
                {
                    "index": index,
                    "status": "downloaded",
                    "content_type": content_type,
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


def clean_onenote_html(
    token: str,
    raw_html: str,
    title: str,
    page_output_path: pathlib.Path,
    page_id: str,
    paths: ExportPaths,
    options: ExportOptions,
) -> tuple[str, list[dict[str, Any]]]:
    soup = BeautifulSoup(raw_html, "html.parser")

    for tag in soup(["script", "noscript"]):
        tag.decompose()

    images = download_and_rewrite_images(token, soup, page_output_path, page_id, paths, options)
    body_content = soup.body.decode_contents() if soup.body else str(soup)
    safe_title = html.escape(title or "Untitled")

    cleaned = f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>{safe_title}</title>
<style>
body {{
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    line-height: 1.45;
    max-width: 900px;
    margin: 2rem auto;
    padding: 0 1rem;
}}
img {{
    max-width: 100%;
    height: auto;
}}
table {{
    border-collapse: collapse;
}}
td, th {{
    border: 1px solid #ccc;
    padding: 0.3rem 0.5rem;
}}
pre, code {{
    white-space: pre-wrap;
}}
.meta {{
    color: #666;
    font-size: 0.9rem;
    margin-bottom: 2rem;
}}
</style>
</head>
<body>
<h1>{safe_title}</h1>
<div class="meta">Exported from OneNote by OneNote Liberation {VERSION}</div>
{body_content}
</body>
</html>
"""
    return cleaned, images


def make_page_metadata(
    page: dict[str, Any],
    title: str,
    page_id: str,
    path_parts: list[str],
    html_path: pathlib.Path,
    paths: ExportPaths,
    images: list[dict[str, Any]],
) -> dict[str, Any]:
    notebook = path_parts[0] if path_parts else None
    section = path_parts[-1] if path_parts else None
    section_groups = path_parts[1:-1] if len(path_parts) > 2 else []

    return {
        "tool": "OneNote Liberation",
        "tool_version": VERSION,
        "title": title,
        "onenote_page_id": page_id,
        "created": page.get("createdDateTime"),
        "modified": page.get("lastModifiedDateTime"),
        "hierarchy": {
            "notebook": notebook,
            "section_groups": section_groups,
            "section": section,
            "path_parts": path_parts,
        },
        "files": {
            "html": str(html_path.relative_to(paths.root)),
            "metadata": str(html_path.with_suffix(".metadata.json").relative_to(paths.root)),
        },
        "images": images,
    }


def export_page(
    token: str,
    page: dict[str, Any],
    path_parts: list[str],
    options: ExportOptions,
    paths: ExportPaths,
) -> dict[str, Any]:
    title = page.get("title") or "Untitled"
    page_id = page["id"]

    if looks_sensitive(path_parts + [title]) and not options.include_sensitive:
        print(f"      SKIP sensitive-looking page: {title}")
        return {
            "title": title,
            "id": page_id,
            "path": None,
            "metadata_path": None,
            "skipped": True,
            "reason": "sensitive-looking path/title",
            "images": [],
        }

    output_path = stable_page_path(paths, path_parts, title)
    metadata_path = output_path.with_suffix(".metadata.json")

    if options.skip_existing and output_path.exists() and metadata_path.exists():
        print(f"      SKIP existing page: {title}")
        return {
            "title": title,
            "id": page_id,
            "path": str(output_path.relative_to(paths.root)),
            "metadata_path": str(metadata_path.relative_to(paths.root)),
            "skipped": False,
            "reason": "already existed",
            "created": page.get("createdDateTime"),
            "modified": page.get("lastModifiedDateTime"),
            "images": [],
        }

    output_path = ensure_unique_path(output_path)
    metadata_path = output_path.with_suffix(".metadata.json")

    print(f"      Exporting page: {title}")

    raw = graph_get(
        token,
        f"/me/onenote/pages/{page_id}/content",
        accept="text/html",
        max_retry_after=options.max_retry_after,
    ).text

    cleaned, images = clean_onenote_html(token, raw, title, output_path, page_id, paths, options)
    output_path.write_text(cleaned, encoding="utf-8")

    metadata = make_page_metadata(page, title, page_id, path_parts, output_path, paths, images)
    metadata_path.write_text(json.dumps(metadata, indent=2), encoding="utf-8")

    if images:
        downloaded_count = sum(1 for item in images if item.get("status") == "downloaded")
        print(f"        images: {downloaded_count}/{len(images)} downloaded")

    return {
        "title": title,
        "id": page_id,
        "path": str(output_path.relative_to(paths.root)),
        "metadata_path": str(metadata_path.relative_to(paths.root)),
        "skipped": False,
        "reason": None,
        "created": page.get("createdDateTime"),
        "modified": page.get("lastModifiedDateTime"),
        "images": images,
    }


def section_matches_filter(current_path: list[str], options: ExportOptions) -> bool:
    if not options.section_filter:
        return True
    haystack = " / ".join(current_path).lower()
    return options.section_filter.lower() in haystack


def export_section(
    token: str,
    section: dict[str, Any],
    path_parts: list[str],
    options: ExportOptions,
    paths: ExportPaths,
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

    if not section_matches_filter(current_path, options):
        print("      SKIP section filter")
        result["error"] = "Skipped by section filter"
        return result

    if looks_sensitive(current_path) and not options.include_sensitive:
        print("      SKIP sensitive-looking section")
        result["error"] = "Skipped sensitive-looking section"
        return result

    try:
        pages = get_all_values(
            token,
            f"/me/onenote/sections/{section_id}/pages?$top=20&$select=id,title,createdDateTime,lastModifiedDateTime",
            options,
            label="pages",
        )

        for page in pages:
            try:
                result["pages"].append(export_page(token, page, current_path, options, paths))
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


def export_section_group(
    token: str,
    group: dict[str, Any],
    path_parts: list[str],
    options: ExportOptions,
    paths: ExportPaths,
) -> dict[str, Any]:
    group_name = group.get("displayName", "Untitled group")
    group_id = group["id"]
    current_path = path_parts + [group_name]

    print(f"  Section group: {group_name}")

    result: dict[str, Any] = {
        "type": "section_group",
        "name": group_name,
        "id": group_id,
        "path": current_path,
        "sections": [],
        "section_groups": [],
        "error": None,
    }

    if looks_sensitive(current_path) and not options.include_sensitive:
        print("    SKIP sensitive-looking section group")
        result["error"] = "Skipped sensitive-looking section group"
        return result

    try:
        sections = get_all_values(
            token,
            f"/me/onenote/sectionGroups/{group_id}/sections?$top=20",
            options,
            label="sections",
        )
        for section in sections:
            result["sections"].append(export_section(token, section, current_path, options, paths))

        child_groups = get_all_values(
            token,
            f"/me/onenote/sectionGroups/{group_id}/sectionGroups?$top=20",
            options,
            label="section groups",
        )
        for child in child_groups:
            result["section_groups"].append(
                export_section_group(token, child, current_path, options, paths)
            )

    except Exception as exc:
        print(f"    ERROR reading section group: {exc}")
        result["error"] = str(exc)

    return result


def export_notebook(
    token: str,
    notebook: dict[str, Any],
    options: ExportOptions,
    paths: ExportPaths,
) -> dict[str, Any]:
    notebook_name = notebook.get("displayName", "Untitled notebook")
    notebook_id = notebook["id"]

    print(f"\nNotebook: {notebook_name}")

    result: dict[str, Any] = {
        "type": "notebook",
        "name": notebook_name,
        "id": notebook_id,
        "sections": [],
        "section_groups": [],
        "error": None,
    }

    try:
        sections = get_all_values(
            token,
            f"/me/onenote/notebooks/{notebook_id}/sections?$top=20",
            options,
            label="sections",
        )
        for section in sections:
            result["sections"].append(export_section(token, section, [notebook_name], options, paths))

        groups = get_all_values(
            token,
            f"/me/onenote/notebooks/{notebook_id}/sectionGroups?$top=20",
            options,
            label="section groups",
        )
        for group in groups:
            result["section_groups"].append(
                export_section_group(token, group, [notebook_name], options, paths)
            )

    except Exception as exc:
        print(f"  ERROR reading notebook: {exc}")
        result["error"] = str(exc)

    return result


def render_section(section: dict[str, Any]) -> str:
    name = html.escape(section.get("name", "Untitled section"))
    output = [f"<li><strong>{name}</strong>"]

    pages = section.get("pages", []) or []
    if pages:
        output.append("<ul>")
        for page in pages:
            title = html.escape(page.get("title") or "Untitled")
            if page.get("skipped"):
                output.append(f"<li>{title} <em>(skipped)</em></li>")
            elif page.get("path"):
                href = html.escape(page["path"])
                image_count = len(page.get("images") or [])
                metadata_path = page.get("metadata_path")
                metadata_link = (
                    f' <a href="{html.escape(metadata_path)}"><small>metadata</small></a>'
                    if metadata_path
                    else ""
                )
                suffix = f" — {image_count} image(s)" if image_count else ""
                reason = f" <em>({html.escape(page.get('reason'))})</em>" if page.get("reason") else ""
                output.append(
                    f'<li><a href="{href}">{title}</a>{html.escape(suffix)}{metadata_link}{reason}</li>'
                )
            else:
                reason = html.escape(page.get("reason") or "not exported")
                output.append(f"<li>{title} <em>({reason})</em></li>")
        output.append("</ul>")

    if section.get("error") and not pages:
        output.append(f" <em>({html.escape(section['error'])})</em>")

    output.append("</li>")
    return "\n".join(output)


def render_node(node: dict[str, Any]) -> str:
    name = html.escape(node.get("name", "Untitled"))
    output = [f"<li><strong>{name}</strong>"]
    children_html: list[str] = []

    for section in node.get("sections", []) or []:
        children_html.append(render_section(section))

    for group in node.get("section_groups", []) or []:
        children_html.append(render_node(group))

    if children_html:
        output.append("<ul>")
        output.extend(children_html)
        output.append("</ul>")

    output.append("</li>")
    return "\n".join(output)


def count_exported_pages(report: dict[str, Any]) -> int:
    count = 0

    def walk(node: dict[str, Any]) -> None:
        nonlocal count
        for section in node.get("sections", []) or []:
            for page in section.get("pages", []) or []:
                if page and not page.get("skipped") and page.get("path"):
                    count += 1
        for group in node.get("section_groups", []) or []:
            walk(group)

    for notebook in report.get("notebooks", []) or []:
        walk(notebook)

    return count


def create_index(report: dict[str, Any], paths: ExportPaths) -> None:
    notebooks_html = "\n".join(render_node(nb) for nb in report.get("notebooks", []))
    exported_count = count_exported_pages(report)

    index = f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>OneNote Liberation Export</title>
<style>
body {{
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    line-height: 1.45;
    max-width: 1100px;
    margin: 2rem auto;
    padding: 0 1rem;
}}
li {{
    margin: 0.25rem 0;
}}
em, small {{
    color: #666;
}}
</style>
</head>
<body>
<h1>OneNote Liberation Export</h1>
<p>Exported pages: {exported_count}</p>
<ul>
{notebooks_html}
</ul>
</body>
</html>
"""

    paths.index.write_text(index, encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Export OneNote pages as local HTML.")
    parser.add_argument(
        "--include-sensitive",
        action="store_true",
        help="Include sensitive-looking sections/pages such as passwords, creds, recovery codes, API keys.",
    )
    parser.add_argument(
        "--no-images",
        action="store_true",
        help="Do not download images. HTML is still exported and image placeholders remain.",
    )
    parser.add_argument(
        "--skip-existing",
        action="store_true",
        help="Skip pages where both the HTML and metadata JSON already exist.",
    )
    parser.add_argument(
        "--section",
        default=None,
        help='Only export sections whose full path contains this text, for example "Recipes".',
    )
    parser.add_argument(
        "--image-delay",
        type=float,
        default=1.0,
        help="Delay in seconds before each image download. Default: 1.0.",
    )
    parser.add_argument(
        "--max-retry-after",
        type=int,
        default=180,
        help="Maximum seconds to wait for a single Retry-After/backoff. Default: 180.",
    )
    parser.add_argument(
        "--output",
        default="onenote_liberation_export",
        help="Output directory. Default: onenote_liberation_export.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    started = time.time()
    paths = ExportPaths(root=pathlib.Path(args.output))
    paths.create()

    options = ExportOptions(
        include_sensitive=args.include_sensitive,
        include_images=not args.no_images,
        skip_existing=args.skip_existing,
        section_filter=args.section,
        image_delay=args.image_delay,
        max_retry_after=args.max_retry_after,
    )

    print(f"OneNote Liberation {VERSION}")
    print("Read-only OneNote HTML exporter")
    print("-----------------------------------")

    print(f"Sensitive-section protection: {'OFF' if options.include_sensitive else 'ON'}")
    print(f"Image downloads: {'ON' if options.include_images else 'OFF'}")
    print(f"Skip existing pages: {'ON' if options.skip_existing else 'OFF'}")
    if options.section_filter:
        print(f"Section filter: {options.section_filter}")

    token = sign_in()

    print("\nFetching notebooks...")
    notebooks = get_all_values(
        token,
        "/me/onenote/notebooks?$top=20",
        options,
        label="notebooks",
    )

    report: dict[str, Any] = {
        "tool": "OneNote Liberation",
        "version": VERSION,
        "purpose": "Read-only OneNote HTML export with hierarchy, images, metadata, and throttle controls",
        "include_sensitive": options.include_sensitive,
        "include_images": options.include_images,
        "skip_existing": options.skip_existing,
        "section_filter": options.section_filter,
        "notebooks": [],
    }

    for notebook in notebooks:
        report["notebooks"].append(export_notebook(token, notebook, options, paths))

    paths.report.write_text(json.dumps(report, indent=2), encoding="utf-8")
    create_index(report, paths)

    elapsed = round(time.time() - started, 2)
    exported_count = count_exported_pages(report)

    print("\nDone.")
    print(f"Exported pages: {exported_count}")
    print(f"Archive folder: {paths.root.resolve()}")
    print(f"Index file: {paths.index.resolve()}")
    print(f"Report file: {paths.report.resolve()}")
    print(f"Elapsed time: {elapsed} seconds")
    print("\nNothing was written to OneNote or Apple Notes.")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nCancelled.")
        sys.exit(130)
