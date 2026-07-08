# OneNote Liberation

A read-only exporter for liberating Microsoft OneNote notebooks into a local HTML archive, with an experimental single-note Apple Notes importer.

## Current status

Prototype. Currently supports:

- Microsoft device-code sign-in
- OneNote notebook / section group / section / page traversal
- Local HTML export
- Nested `index.html`
- Local image download and HTML rewriting
- Per-page metadata JSON files
- Sensitive-looking sections/pages skipped by default
- Graph throttling controls
- Experimental single-note Apple Notes import

## Install

```bash
python3 -m pip install -r requirements.txt
python3 -m pip install -e .
```

## Export

```bash
python3 -m onenote_liberation
```

The default output folder is:

```text
onenote_liberation_export
```

Open:

```text
onenote_liberation_export/index.html
```

## Useful export options

Export one section only:

```bash
python3 -m onenote_liberation --section Recipes --output test_recipes
```

Export without images:

```bash
python3 -m onenote_liberation --section Recipes --no-images --output test_recipes_no_images
```

Slow image downloads to reduce Microsoft Graph throttling:

```bash
python3 -m onenote_liberation --section "Take A Risk" --image-delay 3 --output test_take_a_risk
```

Skip pages already exported:

```bash
python3 -m onenote_liberation --skip-existing
```

## Sensitive notes

By default, sections or page titles that look like passwords, credentials, recovery codes, API keys, or 2FA notes are skipped.

To deliberately include them:

```bash
python3 -m onenote_liberation --include-sensitive
```

## Experimental Apple Notes import

First export a small section, then import one page using its `.metadata.json` file:

```bash
python3 -m onenote_liberation.apple_notes_import path/to/page.metadata.json
```

By default, the note is imported into an Apple Notes folder called:

```text
OneNote Liberation Test
```

You can choose a different destination folder:

```bash
python3 -m onenote_liberation.apple_notes_import path/to/page.metadata.json --folder "OneNote Import Test"
```

This importer is proof-of-concept only. Bulk import and full hierarchy recreation are not implemented yet.

## Safety

The exporter uses Microsoft Graph with `Notes.Read`. It does not write to OneNote.

The Apple Notes importer writes only to Apple Notes, and currently imports one explicitly selected exported note at a time.
