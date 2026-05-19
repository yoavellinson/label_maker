@echo off
setlocal

cd /d "%~dp0"

if not exist .venv (
    py -3 -m venv .venv
)

call .venv\Scripts\activate.bat
python -m pip install --upgrade pip
python -m pip install -r requirements.txt -r requirements-build.txt
python -m PyInstaller --clean --noconfirm label_maker.spec

echo.
echo Built app folder:
echo %cd%\dist\LabelMaker
echo.
echo Run:
echo %cd%\dist\LabelMaker\LabelMaker.exe

endlocal
