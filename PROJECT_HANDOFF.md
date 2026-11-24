# Label Maker Project Handoff

Last updated: 24 November 2026  
Active branch: `grid`  
Current Windows product name: `Label Maker v2`  
Current executable: `LabelMaker_v2.exe`

## Purpose and current direction

This repository contains two generations of a Hebrew coffee-label application for a roasting company.

The original application generated the entire label, including colored backgrounds, textures, circles, pills, specialty styling, and variable content. That version remains in `webapp.py` and `pdf_chat.py`.

The current application is the grid version in `layout_dev_app.py`. The printing company now supplies preprinted color labels. The application therefore loads one of six fixed designs from a master PDF and prints only the variable text. It also includes a developer mode for positioning fields and maintaining reusable label presets.

The current production priority is the grid application. The old application should remain runnable independently because it still represents a different label workflow.

## Current repository status

At the time of this handoff, the working tree has uncommitted changes:

- `layout_dev_app.py` contains the new roast-date font migration and a `16pt` default.
- `grid/layout_presets.json` was migrated locally so all saved `roast_date` representations use `16pt`.
- `grid/backups/layout_presets-20261124-082132.json` is a migration backup and is currently untracked.
- `__pycache__/layout_dev_app.cpython-313.pyc` changed during local verification and should normally not be committed.

Before the next release, review and commit the source change intentionally. Decide separately whether the bundled preset database should be committed. Do not blindly commit the local backup or `__pycache__` file.

The installer file still declares version `1.1.0` even though the executable and product are named v2. Update `MyAppVersion` in `installer/LabelMaker.iss` before issuing a formal new release.

## Main files

| File | Responsibility |
| --- | --- |
| `layout_dev_app.py` | Current Flask application, browser UI, preset database API, PDF preview rendering, text-only PDF generation, full-label PDF export, and most grid logic. |
| `label_maker_desktop.py` | Windows desktop launcher, single-instance lock, server startup, and browser opening. |
| `grid/final_stickers.con_16.8.26 (1).pdf` | Production background PDF. Six approved pages are used by the UI. |
| `grid/Master_stickers_guide.pdf` | Design/master reference used while matching typography and positioning. |
| `grid/layout_presets.json` | Bundled preset database for first launch and local development. Installed machines keep their own copy. |
| `grid/layout_dev_config.json` | Last technical/dev layout export. It is not the main named-preset database. |
| `grid/backgrounds/` | Rendered/reference backgrounds retained for compatibility and inspection. The current app should use the master PDF as source. |
| `label_maker.spec` | PyInstaller definition for `LabelMaker_v2.exe` and bundled assets. |
| `installer/LabelMaker.iss` | Inno Setup installer definition. |
| `.github/workflows/windows-build.yml` | GitHub Actions Windows build and installer workflow. |
| `WINDOWS_BUILD.md` | Existing concise Windows build notes. |
| `webapp.py` | Legacy v1 web UI and CSV blend database workflow. |
| `pdf_chat.py` | Legacy v1 complete-label PDF renderer. |
| `blends.csv` and `textures/` | Legacy v1 blend database and uploaded/background texture files. |

## Running locally

Create the environment and install dependencies if needed:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt -r requirements-build.txt
```

Run the current grid application:

```bash
.venv/bin/python layout_dev_app.py
```

Open:

```text
http://127.0.0.1:5002
```

The development server binds to `0.0.0.0`, so another device on the same network can use the host machine's LAN address. The packaged desktop launcher deliberately binds to `127.0.0.1` because it is a local desktop application.

Run the legacy application with:

```bash
.venv/bin/python webapp.py
```

Do not run both generations on the same configured port. The v2 desktop launcher defaults to port `5002` through `LABEL_MAKER_V2_PORT` or `LABEL_MAKER_PORT`.

## Current grid application architecture

### Runtime data

In source development, data is read from the repository. In a frozen Windows build, editable data lives under:

```text
%LOCALAPPDATA%\LabelMaker_v2
```

The named preset database is:

```text
%LOCALAPPDATA%\LabelMaker_v2\grid\layout_presets.json
```

Database backups are created under:

```text
%LOCALAPPDATA%\LabelMaker_v2\grid\backups
```

The bundled PDF, preset database, dev config, and background directory are copied into the runtime data folder only when missing. Existing user data is not overwritten during a normal reinstall.

The data folder can be overridden for portable/debug use:

```bat
set LABEL_MAKER_V2_DATA_DIR=C:\some\folder
LabelMaker_v2.exe
```

### Background choices

The UI deliberately exposes six buttons rather than a general PDF file picker:

| Page | Button |
| ---: | --- |
| 4 | שוקולדי |
| 5 | פירותי |
| 6 | פורטה |
| 10 | ספיישלטי שוקולדי |
| 11 | ספיישלטי פירותי |
| 12 | ספיישלטי פורטה |

All six use `grid/final_stickers.con_16.8.26 (1).pdf` as the authoritative source. This decision avoids path errors and inconsistent manually selected files on Windows.

### PDF rendering

`pypdfium2` is the primary background renderer. Poppler commands remain as fallback paths for source development, but the packaged Windows app must not depend on an external Poppler installation.

The app exposes two PDF outputs:

- `/text-only.pdf` creates the production text layer for printing onto preprinted labels.
- `/label-with-background.pdf` creates a development/reference PDF containing both background and text.

The preview and print systems both use millimeters converted to PDF points. Text vertical placement uses font ascent/descent so the browser's visual center and ReportLab's baseline model are closer.

### Preset database

`grid/layout_presets.json` has the shape:

```json
{
  "layouts": [
    {
      "id": "stable-id",
      "name": "display name",
      "updated_at": "ISO timestamp",
      "layout": {
        "editor_state": {},
        "parameters": {}
      }
    }
  ]
}
```

Each label stores both an editable representation and derived PDF parameters. Migrations must update both forms. For example, editable text uses `size`, while PDF parameters use `font_size_pt`.

Named preset operations are available at:

- `GET /layouts`
- `GET /layouts/<id>`
- `POST /layouts`
- `DELETE /layouts/<id>`

Every database write first creates a timestamped backup.

### Browser state

The current unsaved editing state is cached in browser `localStorage` under `label-layout-dev-state-v1`. It preserves form content, selected layout, zoom, mode, and compact/full editing state.

Named presets remain authoritative for database content. Loading a named preset restores its geometry and then sets the roast date to today's date.

### Text roles

The important text roles are:

| Role | Meaning |
| --- | --- |
| `origin` | Top category/country text in the colored strip. |
| `title` | Main coffee/blend name. |
| `roast_level` | Roast level on the first detail row. |
| `tastes` | Taste description on the first detail row. |
| `bean_type` | Single-origin or blend description on the second detail row. |
| `process_label` | Processing text, prefixed with `עיבוד:`. |
| `kosher` | Kosher text, currently defaulting to `כשרות חתם סופר`. |
| `separator` | A literal pipe character used between row segments. |
| `weight_value` | Weight number or fraction. |
| `weight_unit` | `ק״ג`. |
| `roast_date` | The date value. |

Single-origin output is:

```text
חד זני - ערביקה
חד זני - רובוסטה
```

Blend output is:

```text
תערובת 30% ערביקה
תערובת 50% ערביקה
תערובת 70% ערביקה
תערובת 100% ערביקה
```

### Auto alignment

The intended layout beneath the headline is:

```text
roast level | tastes
bean type | processing | kosher
```

All content text begins from the left and uses Hebrew RTL shaping. Date, weight, and kg are excluded from auto alignment.

Separator data has historically been inconsistent: some old presets saved `|` with the wrong role. Current role detection treats the literal pipe text as authoritative, ensures that three separators exist, hides extras, and renders separators after other text so they remain visible.

Auto alignment was the most iterative area of the project. Before changing it again, test at least:

- A normal two-item top row.
- A bottom row with processing present.
- A bottom row with processing blank.
- A legacy preset with incorrectly classified pipe items.
- Browser preview and text-only PDF output.

Avoid introducing wide invisible DOM text boxes. That experiment caused wrapping, confusing selection regions, and separators appearing inside text fields.

## Windows packaging and installation

### GitHub Actions

The `Windows Build` workflow runs on pushes to `grid`, tags matching `v*`, or manual dispatch.

It creates:

```text
LabelMaker-v2-windows-folder
LabelMaker-v2-windows-installer
```

The installer artifact contains:

```text
LabelMaker_v2_Setup.exe
```

### Local Windows build

Run:

```bat
build_windows.bat
```

The standalone folder is produced at:

```text
dist\LabelMaker_v2
```

Compile `installer\LabelMaker.iss` with Inno Setup to produce:

```text
dist\installer\LabelMaker_v2_Setup.exe
```

### Single-instance behavior

`label_maker_desktop.py` solves the multiple-server problem:

1. It checks whether `127.0.0.1:5002` is already accepting connections.
2. If the server exists, it opens the browser and exits.
3. Otherwise it acquires `%LOCALAPPDATA%\LabelMaker_v2\label_maker_v2.lock`.
4. It starts Flask with `debug=False` and `use_reloader=False`.
5. It releases the lock on normal shutdown.

This prevents repeated desktop-icon clicks from launching multiple servers. The old and new applications must use distinct ports and distinct lock/data names if both are installed.

### User data during upgrades

Reinstalling the application under `Program Files` does not replace `%LOCALAPPDATA%\LabelMaker_v2`. Always back up the LocalAppData folder before a major release, especially the preset database.

Do not treat the bundled `grid/layout_presets.json` inside the installer as the Windows user's live database. It is only copied on first launch when no runtime database exists.

## Latest roast-date font migration

The current uncommitted source sets roast-date text to `16pt` for new labels and migrates every saved preset on database read.

The migration recursively changes both:

```text
role=roast_date, size -> 16
role=roast_date, font_size_pt -> 16
```

The local test found 26 date-field representations and verified all were converted. A database backup was created before the migration write.

For an already installed machine that must not be rebuilt, the live JSON can be changed with PowerShell. Close the app first:

```powershell
$db = "$env:LOCALAPPDATA\LabelMaker_v2\grid\layout_presets.json"
$backup = "$db.backup-$(Get-Date -Format 'yyyyMMdd-HHmmss')"

Copy-Item $db $backup
$data = Get-Content $db -Raw -Encoding UTF8 | ConvertFrom-Json

function Update-DateFont($node) {
    if ($null -eq $node) { return }

    if ($node -is [System.Collections.IEnumerable] -and
        $node -isnot [string] -and
        $node -isnot [pscustomobject]) {
        foreach ($item in $node) { Update-DateFont $item }
        return
    }

    if ($node -is [pscustomobject]) {
        if ($node.role -eq "roast_date") {
            if ($node.PSObject.Properties["size"]) { $node.size = 16 }
            if ($node.PSObject.Properties["font_size_pt"]) { $node.font_size_pt = 16 }
        }
        foreach ($property in $node.PSObject.Properties) {
            Update-DateFont $property.Value
        }
    }
}

Update-DateFont $data
$json = $data | ConvertTo-Json -Depth 100
[System.IO.File]::WriteAllText(
    $db,
    $json,
    (New-Object System.Text.UTF8Encoding($false))
)
```

This updates saved labels without reinstalling. A completely blank label created from an older executable's embedded defaults can still begin at the old size; permanent embedded-default changes require a rebuilt executable. New labels created from an updated saved preset retain `16pt`.

## Problem and solution history

### Phase 1: complete-label generator

| Problem | Solution |
| --- | --- |
| Hebrew text and punctuation rendered in the wrong order. | Added `python-bidi`, RTL display conversion, Hebrew fonts, RTL form direction, and field-specific Latin handling. |
| Main circle text overflowed or looked unbalanced. | Added fit-to-width sizing, larger circle geometry, line-specific font sizes/weights, and support for a blank second line. |
| Pills touched the upper section or did not fit multiple words. | Added vertical spacing, content-based widths, fit-to-pill font sizing, and larger pill geometry. |
| Bottom copy had too many editable variants. | Reduced it to roast-date and `הפתעה!` modes with a toggle. Blank roast date remains possible for handwriting. |
| All accents were hard-coded brown. | Added an accent color picker. |
| The top needed custom artwork while the body should remain uncolored. | Added uploadable top textures and kept the lower body transparent/white as required. |
| Description was split into headline/body fields. | Replaced it with one multiline field and automatic fitting. |
| Form submissions redirected to `127.0.0.1` when accessed remotely. | Changed request/form behavior to use relative URLs and bound the development server to `0.0.0.0`. |
| Large uploaded textures caused `Request Entity Too Large`. | Increased request limits and compressed/resized uploaded images before persistence. |
| Date needed today's PC date and manual override. | Added `dd/mm/yyyy`, today as default, blank support, and refresh-on-load behavior. |
| Refreshing reset all values. | Added persisted state storage. |
| Font did not match the brand. | Bundled Heebo and later Karantina, with explicit ReportLab registration and browser `@font-face`. |
| Sticker dimensions were misunderstood. | Corrected the requested orientation to 100 mm high by 50 mm wide for the legacy label. |
| Blend data had to be reusable. | Added `blends.csv`, alphabetic Hebrew selection, CSV schema migration, load/save, texture-path normalization, and password-protected editing. |
| New blends could not be created. | Added write-back to CSV and texture persistence. |
| Saved labels needed deletion. | Added targeted delete behavior with database backup; unrelated presets remain intact. |
| Preview required manual submission. | Added debounced automatic preview refresh. |
| Specialty labels needed a different visual system. | Added dark circle, specialty/coffee copy, cup score, variety/process copy, six two-column pills, and specialty-specific spacing/weights. |
| Cup score star glyphs rendered as squares. | Used supported drawing/icon paths; a custom bean icon was explored, then the visual direction returned to stars. |
| Texture paths mixed `/` and `\`. | Normalized paths to forward slashes before resolving and saving. |
| Windows left server processes running or allowed repeated launches. | Added a dedicated desktop launcher, disabled Flask's reloader, and introduced single-instance/port checks. |

### Phase 2: preprinted grid application

| Problem | Solution |
| --- | --- |
| The new print process needed only variable text over preprinted designs. | Created `layout_dev_app.py` with fixed PDF backgrounds and text-only PDF output. |
| Layout coordinates were unknown. | Built a developer tool with draggable text, background crop/offset/scale, sticker dimensions, zoom, JSON export, and named presets. |
| The master PDF was larger than the physical sticker crop. | Added sticker clipping and independent background position/scale controls. |
| Workers still needed editable daily content. | Added normal-mode fields for title, tastes, roast, bean type, processing, kosher text, date, and weight. |
| Daily use was buried under dev controls. | Added compact mode, kept load/save controls available, and emphasized the print action. |
| Background selection was too technical. | Removed general PDF upload/page selection and replaced it with six named production buttons. |
| Printing needed text only. | Added a dedicated text-only PDF and print workflow. The preview still shows the background for positioning. |
| Developers needed an exact full-label proof. | Added `הורדת PDF עם רקע` and `/label-with-background.pdf`. |
| Print dimensions of 97×97 mm were requested, but text shifted relative to preview. | Separate print rescaling was removed. The PDF now uses the sticker coordinate system directly. Printer scaling should be handled in the print dialog only when deliberately required. |
| Print orientation varied by printer. | Added 0°, 90°, -90°, and 180° rotation. The final default is no rotation. |
| Browser preview and PDF text had different vertical positions. | Converted center coordinates to ReportLab baselines using font ascent and descent. |
| PDF backgrounds were blank in the installed app. | Bundled the master PDF in PyInstaller, added `pypdfium2`, copied runtime assets into LocalAppData, and resolved source paths against both bundled and runtime directories. |
| Startup still required manually selecting the PDF. | Added a fixed master-PDF fallback and mapped legacy rendered PNG source names back to master PDF pages. |
| Changing a background button could not update a loaded preset. | Background selection was decoupled from preset identity so the current label can change its production page. |
| Fractions looked different in browser and PDF. | Several stacked-fraction approaches were tested and rejected. The app returned to supported fraction glyphs and added optional per-layout position/size variants for ½ and ¼. |
| Weight controls were slow for workers. | Added 1 kg, ½ kg, and ¼ kg buttons; 1 kg remains the default. |
| Fine placement was difficult. | Added zoom controls and manual x/y/size/kerning controls in dev mode. |
| Refresh lost current editing state. | Added localStorage persistence for the current draft and UI mode. |
| Saving silently overwrote the active preset. | Added explicit update, save-as-new, reset, dirty-state indication, confirmation before discarding changes, and targeted deletion. |
| Date became stale when loading a saved label. | Loading or resetting a named preset now sets the roast date to today after applying saved geometry. |
| Thin thermal-printer text looked weak. | Retained per-field font weights and advised using printer darkness/quality settings; auto-align must not force font weight. |
| Auto-align initially moved only some roles. | Added explicit role detection, including legacy fields and bean type. |
| Auto-align changed font thickness. | Removed forced weight changes; alignment preserves each field's saved weight. |
| Auto-align caused wrapping and invisible oversized boxes. | Removed auto-created wide DOM boxes and disabled automatic wrapping with `white-space: pre`. |
| Separators moved unpredictably or vanished. | Literal `|` now overrides stale roles, three separators are ensured, extras are hidden, and separators render after other text. |
| Processing text behaved differently because `עיבוד:` is a generated prefix. | Promoted processing to a dedicated role/segment rather than treating it as generic text. |
| Single-origin and blend copy needed structured choices. | Added `bean_type` with single-origin Arabica/Robusta and blends at 30/50/70/100% Arabica. |
| Date font needed to become 16 across an existing database. | Added a recursive migration for both editable and PDF fields, a new-label default, load-time enforcement, and a no-rebuild PowerShell option. |

## Superseded approaches that should stay removed

- Do not restore the general background PDF file picker for worker mode. The six master-PDF buttons are the production contract.
- Do not restore independent 97×97 print-coordinate scaling. It caused preview/print misalignment.
- Do not use browser-only stacked fraction markup for production PDF text. It rendered differently in ReportLab.
- Do not force font weight during auto alignment.
- Do not add wide invisible text boxes merely to represent conceptual row regions.
- Do not use OS-dependent star glyphs or unsupported font symbols without testing the frozen Windows build.
- Do not store editable production data under `Program Files`; keep it in LocalAppData.
- Do not start Flask with the reloader in the packaged desktop application.

## Known risks and remaining work

### Auto alignment still needs visual regression coverage

The current alignment values were tuned through screenshots and manual iteration. There are no automated screenshot tests. Add browser screenshot tests and compare text-only PDF renders for the six backgrounds before making further spacing changes.

The latest intended row structure is fixed, but variable-length Hebrew copy can still collide with separators. A robust next step is to measure rendered text width using the same Karantina metrics used by ReportLab, then place separators at a fixed gap after the previous text and place the next segment after another fixed gap. This should be implemented in one shared geometry function used by preview and PDF export.

### Optional processing

Processing is optional. If it is blank, the bottom row should not display `עיבוד:` by itself, and separator count/placement should adapt to:

```text
bean type | kosher
```

The current content code may still produce the bare prefix `עיבוד:` when the value is blank. This should be tested and tightened.

### Persistent Windows logs are not implemented

The packaged executable is windowless, so failures can be hard to diagnose. Add rotating logs under:

```text
%LOCALAPPDATA%\LabelMaker_v2\logs
```

Log startup, selected data directory, PDF source resolution, renderer errors, port/lock behavior, and unhandled exceptions. Add a UI action that opens the log folder or downloads a diagnostic ZIP.

### Installer versioning

`installer/LabelMaker.iss` still says `1.1.0`. Establish a release version policy and update the version for each installer release. The AppId should remain unchanged so upgrades target the same installation.

### Legacy/current separation

`WINDOWS_BUILD.md` still mentions some legacy files such as `blends.csv`, textures, `.label_state.json`, and the v1 password. The v2 package includes these for compatibility, but the grid app's primary database is `grid/layout_presets.json`. Future documentation should separate v1 and v2 instructions more clearly.

### Automated tests

There is no dedicated test suite. At minimum, add tests for:

- Database migration and backup behavior.
- Preset save/load/delete without unrelated data loss.
- Windows-style and POSIX-style source paths.
- Today-date refresh when loading a preset.
- PDF background resolution in frozen and source modes.
- Browser/PDF coordinate agreement.
- Optional processing and separator count.
- Duplicate or incorrectly classified legacy fields.
- Single-instance launcher behavior.

## Operational checklist

### Before changing code

1. Back up the live Windows data folder.
2. Confirm whether the request applies to v1 or grid v2.
3. Load at least one normal and one specialty preset.
4. Record current preview and text-only PDF output.

### After changing code

1. Run:

   ```bash
   .venv/bin/python -m py_compile layout_dev_app.py
   ```

2. Parse the embedded JavaScript or load the page and check the browser console.
3. Restart the development server; the project owner expects the app to be restarted after each code change.
4. Test preset load, edit, update, save-as-new, reset, delete, and refresh persistence.
5. Test text-only PDF and full-background PDF.
6. Test all six background buttons.
7. Verify date, weight, kg, and separators did not move unintentionally.

### Before a Windows release

1. Review `git status`; exclude runtime backups and `__pycache__`.
2. Update the installer version.
3. Confirm the master PDF and fonts are included by `label_maker.spec`.
4. Push to `grid` or run the Windows Build workflow manually.
5. Download and test both the standalone-folder and installer artifacts.
6. Install over an existing copy and verify LocalAppData presets remain intact.
7. Double-click the desktop shortcut repeatedly and confirm only one server process exists.
8. Test on the actual thermal printer with printer scaling disabled unless deliberately required.

## Recommended next steps

1. Commit the roast-date migration source change after reviewing whether the locally migrated bundled presets should ship.
2. Add persistent Windows logging before the next remote-machine deployment.
3. Replace hand-tuned separator coordinates with measured-content row layout shared by preview and PDF.
4. Add screenshot/PDF regression fixtures for all six production backgrounds.
5. Update installer versioning and split v1/v2 operational notes in `WINDOWS_BUILD.md`.

