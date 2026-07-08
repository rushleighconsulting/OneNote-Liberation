# OneNote Liberation

A read-only exporter for liberating Microsoft OneNote notebooks into a local HTML archive and, optionally, importing that archive into Apple Notes.

## Current status

Working migration candidate. Currently supports:

- Microsoft device-code sign-in
- OneNote notebook / section group / section / page traversal
- Local HTML export
- Nested `index.html`
- Local image download and HTML rewriting
- Correct image extensions using content detection rather than weak Graph headers
- Per-page metadata JSON files
- Sensitive-looking sections/pages skipped by default
- Optional inclusion of sensitive-looking notes
- Microsoft Graph throttling controls for long exports
- Manifest creation and verification
- Export HTML cleanup before import
- Bulk Apple Notes import
- One-level Apple Notes hierarchy import using iCloud account targeting

## Safety model

The exporter uses Microsoft Graph with `Notes.Read`.

It does **not** write to OneNote.

The Apple Notes importer writes only to Apple Notes.

Sensitive-looking OneNote sections/pages are skipped by default. To include them, you must pass `--include-sensitive` explicitly.

## Install

```bash
cd ~/Desktop/OneNote-Liberation
python3 -m pip install -r requirements.txt
python3 -m pip install -e .
```

## Recommended full migration workflow

### 1. Export the notebook

For a normal full export:

```bash
python3 -m onenote_liberation --image-delay 8 --max-retry-after 600 --skip-existing --output migration-full-notebook
```

For a final export that deliberately includes sensitive-looking pages and sections:

```bash
python3 -m onenote_liberation --include-sensitive --image-delay 8 --max-retry-after 600 --skip-existing --output migration-full-notebook-sensitive
```

The exporter is read-only against OneNote. `--include-sensitive` only changes what is copied into the local export.

### 2. Clean exported HTML before Apple Notes import

```bash
python3 -m onenote_liberation.clean_export migration-full-notebook-sensitive --provenance bottom
```

This removes duplicate top headings, removes the exporter banner from the top of pages, trims leading empty blocks, and appends provenance at the bottom of each note.

### 3. Create and verify the migration manifest

```bash
python3 -m onenote_liberation.manifest create migration-full-notebook-sensitive
python3 -m onenote_liberation.manifest verify migration-full-notebook-sensitive
```

### 4. List Apple Notes accounts

Do this before importing. Apple Notes may expose Gmail/IMAP Notes before iCloud, even when iCloud is the UI default.

```bash
osascript -e 'tell application "Notes" to get name of every account'
```

Use the iCloud account name in the import command.

### 5. Import into Apple Notes

Recommended importer:

```bash
python3 -m onenote_liberation.apple_notes_import_onelevel migration-full-notebook-sensitive --folder "OneNote Final iCloud" --account "YOUR_ICLOUD_ACCOUNT_NAME" --delay 0.5
```

Example:

```bash
python3 -m onenote_liberation.apple_notes_import_onelevel migration-full-notebook-sensitive --folder "OneNote Final iCloud" --account "jeremysedgley@icloud.com" --delay 0.5
```

The resulting Apple Notes structure is:

```text
iCloud account
└── OneNote Final iCloud
    ├── Personal - Recipes
    ├── Rushleigh - Tipsy Fox
    ├── Pub ents - Music rounds
    └── ...
```

This one-level hierarchy is the supported Apple Notes import mode. Full deep nesting is not currently reliable through Apple Notes AppleScript.

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

Treat exports created with `--include-sensitive` as confidential files. They may contain passwords, recovery codes, API keys, account details, or private personal information.

## Apple Notes import notes

### Use iCloud, not Gmail/IMAP Notes

AppleScript may select a Gmail/IMAP Notes account if the importer uses `first account`. This can cause errors involving `IMAPFolder` or `IMAPNote` object IDs.

The one-level importer therefore requires an explicit Apple Notes account:

```bash
python3 -m onenote_liberation.apple_notes_import_onelevel migration-full-notebook-sensitive --folder "OneNote Final iCloud" --account "YOUR_ICLOUD_ACCOUNT_NAME"
```

### Why one-level hierarchy?

Apple Notes supports nested folders in the UI, but deep nested folder creation/addressing is not reliable through AppleScript on all account types and macOS configurations.

The supported import strategy is therefore:

```text
Root folder
└── Section Group - Section
    └── Note
```

This preserves the meaningful OneNote hierarchy while staying reliable in Apple Notes.

### Diagnostic probe

If Apple Notes scripting behaves unexpectedly, run:

```bash
python3 -m onenote_liberation.notes_script_probe
```

This writes and runs a generated `.applescript` file to test Notes folder creation.

## Older flat Apple Notes importer

The original flat importer is still available:

```bash
python3 -m onenote_liberation.apple_notes_import path/to/export --folder "OneNote Flat Import" --folder-mode section
```

For real migrations, prefer:

```bash
python3 -m onenote_liberation.apple_notes_import_onelevel ...
```
