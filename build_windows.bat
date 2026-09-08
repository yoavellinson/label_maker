@echo off
setlocal

cd /d "%~dp0"

if not exist .venv (
    py -3 -m venv .venv
)

call .venv\Scripts\activate.bat
python -m pip install --upgrade pip
python -m pip install -r requirements.txt -r requirements-build.txt

if not exist "grid\final_stickers.con_16.8.26 (1).pdf" (
    echo Missing required background PDF: grid\final_stickers.con_16.8.26 (1).pdf
    exit /b 1
)

if not exist "grid\Master_stickers_guide.pdf" (
    echo Missing required master PDF: grid\Master_stickers_guide.pdf
    exit /b 1
)

python -m PyInstaller --clean --noconfirm label_maker.spec

echo.
echo Built app folder:
echo %cd%\dist\LabelMaker
echo.
echo Run:
echo %cd%\dist\LabelMaker\LabelMaker_v2.exe

endlocal
