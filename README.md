# OneNote Liberation

A read-only exporter for liberating Microsoft OneNote notebooks into a local HTML archive.

## Current status

Prototype. Currently supports:

- Microsoft device-code sign-in
- OneNote notebook / section group / section / page traversal
- Local HTML export
- Nested `index.html`
- Local image download and HTML rewriting
- Sensitive-looking sections/pages skipped by default

## Install

```bash
python3 -m pip install -r requirements.txt
```

## Run

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

## Sensitive notes

By default, sections or page titles that look like passwords, credentials, recovery codes, API keys, or 2FA notes are skipped.

To deliberately include them:

```bash
python3 -m onenote_liberation --include-sensitive
```

## Safety

This tool uses Microsoft Graph with `Notes.Read`. It does not write to OneNote or Apple Notes.
