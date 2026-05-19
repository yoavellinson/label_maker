# Windows Build

This project can be packaged as a Windows desktop app with PyInstaller. The app runs a local Flask server and opens the label editor in the default browser.

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
.label_state.json
```

The bundled `blends.csv` and `textures\` are copied there on first launch if they do not already exist. This avoids Windows permission issues under `Program Files`.

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
