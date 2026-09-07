# Windows Build

This project can be packaged as a Windows desktop app with PyInstaller. The app runs one local Flask server and opens the label editor in the default browser.

Launching the app again while it is already running will not start another server. It will only open the browser at the existing app address.

## Build The EXE

On a Windows machine with Python installed:

```bat
build_windows.bat
```

The app folder will be created at:

```text
dist\LabelMaker
```

Run:

```text
dist\LabelMaker\LabelMaker.exe
```

## Build With GitHub Actions

Push to the `grid` branch, or run the `Windows Build` workflow manually from GitHub Actions.

The workflow uploads two artifacts:

```text
LabelMaker-windows-folder
LabelMaker-windows-installer
```

## Build An Installer

Install Inno Setup, then open:

```text
installer\LabelMaker.iss
```

Compile it after running `build_windows.bat`.

The installer will be created at:

```text
dist\installer\LabelMakerSetup.exe
```

## Editable Data

In the installed Windows app, editable files live in:

```text
%LOCALAPPDATA%\LabelMaker
```

Files:

```text
blends.csv
textures\
grid\
.label_state.json
```

The bundled `blends.csv`, `textures\`, and `grid\` files are copied there on first launch if they do not already exist. This avoids Windows permission issues under `Program Files`.

Grid sticker files included in the build:

```text
grid\final_stickers.con_16.8.26 (1).pdf
grid\Master_stickers_guide.pdf
grid\backgrounds\
grid\layout_presets.json
grid\layout_dev_config.json
```

Saved sticker layouts are editable after install at:

```text
%LOCALAPPDATA%\LabelMaker\grid\layout_presets.json
```

For a portable build, you can override the data folder:

```bat
set LABEL_MAKER_DATA_DIR=%cd%
LabelMaker.exe
```

## Password

The default CSV-edit password is:

```text
coffee
```

To change it when launching from a terminal:

```bat
set LABEL_ADMIN_PASSWORD=my-password
LabelMaker.exe
```
