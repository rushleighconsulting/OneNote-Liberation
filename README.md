# OneNote Liberation

A read-only exporter for liberating Microsoft OneNote notebooks into a local HTML archive and, optionally, importing that archive into Apple Notes.

## Current status

Working migration candidate. Currently supports:

- Microsoft device-code sign-in with persistent MSAL token cache
- OneNote notebook / section group / section / page traversal
- Local HTML export
- Nested `index.html`
- Local image download and HTML rewriting
- PDF and Office object download from OneNote `<object>` resources
- Correct asset extensions using content detection rather than weak Graph headers
- Per-page metadata JSON files
- Sensitive-looking sections/pages skipped by default
- Optional inclusion of sensitive-looking notes
- Microsoft Graph throttling controls for long exports
- Manifest creation and verification
- Export audit reporting
- Export HTML cleanup before import
- OneNote checkbox state preserved as Unicode `☐` and `☑`
- Bulk Apple Notes import
- One-level Apple Notes hierarchy import using explicit account targeting

## Safety model

The exporter uses Microsoft Graph with `Notes.Read`.

It does **not** write to OneNote.

The Apple Notes importer writes only to Apple Notes.

Sensitive-looking OneNote sections/pages are skipped by default. To include them, pass `--include-sensitive` explicitly.

The local Microsoft authentication cache is stored outside the repository in the user's home directory:

```text
~/.onenote-liberation/msal_token_cache.json
```

Treat this file as private. It is ignored by the repository `.gitignore`.

## Install

```bash
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

This removes duplicate top headings, removes the exporter banner from the top of pages, trims leading empty blocks, converts OneNote to-do tags into Unicode checkbox markers, and appends provenance at the bottom of each note.

### 3. Create and verify the migration manifest

```bash
python3 -m onenote_liberation.manifest create migration-full-notebook-sensitive
python3 -m onenote_liberation.manifest verify migration-full-notebook-sensitive
```

### 4. Audit the export

```bash
python3 -m onenote_liberation.audit migration-full-notebook-sensitive --show-pages
```

The audit reports HTML features and potential fidelity risks such as objects, embeds, iframes, checkbox inputs, and missing HTML.

### 5. List Apple Notes accounts

Do this before importing. Apple Notes may expose an IMAP/Gmail Notes account before iCloud, even when iCloud is the UI default.

```bash
osascript -e 'tell application "Notes" to get name of every account'
```

Use the desired Apple Notes account name in the import command.

### 6. Import into Apple Notes

Recommended importer:

```bash
python3 -m onenote_liberation.apple_notes_import_onelevel migration-full-notebook-sensitive --folder "Imported OneNote" --account "YOUR_APPLE_NOTES_ACCOUNT" --delay 0.5
```

The resulting Apple Notes structure is:

```text
Apple Notes account
└── Imported OneNote
    ├── Personal - Recipes
    ├── Work - Project Notes
    ├── Events - Music rounds
    └── ...
```

This one-level hierarchy is the supported Apple Notes import mode. Full deep nesting is not currently reliable through Apple Notes AppleScript.

## Command reference

### Exporter: `python3 -m onenote_liberation`

| Switch | Meaning |
|---|---|
| `--output PATH` | Output directory for the local HTML archive. Default: `onenote_liberation_export`. |
| `--section TEXT` | Export only sections whose full path contains this text. Useful for test exports. |
| `--include-sensitive` | Include sensitive-looking sections/pages that are skipped by default. Treat the resulting export as confidential. |
| `--no-images` | Do not download images or resources. HTML is still exported. |
| `--skip-existing` | Skip pages where both HTML and metadata already exist. Use this to resume interrupted exports. |
| `--image-delay SECONDS` | Delay before each image/resource download. Useful for reducing Microsoft Graph throttling. Default: `1.0`. |
| `--max-retry-after SECONDS` | Maximum wait for one Microsoft Graph retry/backoff. Default: `180`; long migrations may use `600`. |

### Cleaner: `python3 -m onenote_liberation.clean_export`

| Switch | Meaning |
|---|---|
| `export` | Export directory to clean in place. |
| `--provenance bottom|top|none` | Where to place OneNote Liberation provenance text. Default: `bottom`. |
| `--limit N` | Clean at most N metadata files. Useful for testing. |
| `--dry-run` | Show selected pages without changing files. |

### Manifest: `python3 -m onenote_liberation.manifest`

| Command | Meaning |
|---|---|
| `create EXPORT_DIR` | Create `migration_manifest.json` for an export. |
| `verify EXPORT_DIR` | Verify the manifest against files on disk. |

### Audit: `python3 -m onenote_liberation.audit`

| Switch | Meaning |
|---|---|
| `export` | Export directory to audit. |
| `--show-pages` | List pages with potential fidelity risks. |

### Apple Notes one-level importer

Command:

```bash
python3 -m onenote_liberation.apple_notes_import_onelevel EXPORT_DIR --folder "Imported OneNote" --account "YOUR_APPLE_NOTES_ACCOUNT"
```

| Switch | Meaning |
|---|---|
| `input` | Export directory or a single `.metadata.json` file. |
| `--folder NAME` | Root folder to create/use in Apple Notes. |
| `--account NAME` | Apple Notes account name. Required. List accounts with AppleScript before importing. |
| `--limit N` | Import at most N notes. Useful for testing. |
| `--delay SECONDS` | Delay between note imports. Default: `0.2`; larger imports may use `0.5`. |
| `--no-attach-assets` | Do not attach downloaded assets after creating notes. |
| `--keep-scripts` | Keep generated temporary AppleScript files for debugging. |
| `--dry-run` | Show import plan without writing to Apple Notes. |

### Older flat Apple Notes importer

The original flat importer is still available:

```bash
python3 -m onenote_liberation.apple_notes_import EXPORT_DIR --folder "OneNote Flat Import" --folder-mode section
```

For real migrations, prefer:

```bash
python3 -m onenote_liberation.apple_notes_import_onelevel ...
```

## Useful export examples

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
python3 -m onenote_liberation --section "Recipes" --image-delay 3 --output test_recipes
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

### Use an explicit Apple Notes account

AppleScript may select an IMAP/Gmail Notes account if an importer uses `first account`. This can cause errors involving IMAP object IDs.

The one-level importer therefore requires an explicit Apple Notes account:

```bash
python3 -m onenote_liberation.apple_notes_import_onelevel migration-full-notebook-sensitive --folder "Imported OneNote" --account "YOUR_APPLE_NOTES_ACCOUNT"
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

## Known limitations

- Native Apple Notes checklists are not recreated. OneNote checkbox state is preserved as Unicode `☐` and `☑`, but the boxes are not interactive.
- OneNote proprietary tags other than to-do checkboxes are not currently interpreted.
- OneNote drawing ink is preserved only if Microsoft Graph exports it as an image.
- Embedded Office documents are migrated as attached files rather than editable embedded objects.
- PDF printouts are preserved as attached PDFs, not necessarily as inline page previews identical to OneNote.
- Internal OneNote links are not rewritten to point to corresponding Apple Notes notes.
- OneNote page versions, edit history, and author history are not migrated.
- OneNote page colours, ruled paper, and arbitrary page canvas layout are not reproduced.
- Complex OneNote layouts may be linearised into standard document flow.
- Microsoft Graph rate limiting may significantly increase export time for large notebooks, although the exporter retries and can resume with `--skip-existing`.

## Diagnostic probe

If Apple Notes scripting behaves unexpectedly, run:

```bash
python3 -m onenote_liberation.notes_script_probe
```

This writes and runs a generated `.applescript` file to test Notes folder creation.
