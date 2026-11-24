"""Send label PDFs straight to a named Windows printer, at actual size.

Each label type can be assigned its own printer (thermal for the preprinted
labels, colour label printer for the narrow ones, office printer for A4).
Pages are rasterised with pdfium at the printer's resolution and placed at
their true physical size, compensating for the printer's unprintable margin,
so there is no "fit to page" scaling. The printer's paper size itself comes
from its Windows driver settings.

On non-Windows systems (development) direct printing is unavailable and the
app falls back to the browser's print dialog.
"""

from __future__ import annotations

import os

try:  # pywin32 is only installed on Windows
    import win32gui
    import win32print
    import win32ui
    from PIL import ImageWin
except ImportError:  # pragma: no cover - exercised on non-Windows
    win32gui = None
    win32print = None
    win32ui = None
    ImageWin = None

# GetDeviceCaps indexes
LOGPIXELSX, LOGPIXELSY = 88, 90
PHYSICALOFFSETX, PHYSICALOFFSETY = 112, 113
MAX_RENDER_DPI = 400  # enough for any label printer; larger is stretched by the driver
# DEVMODE fields
DM_ORIENTATION, DM_PAPERSIZE, DM_PAPERLENGTH, DM_PAPERWIDTH, DM_SCALE = 0x1, 0x2, 0x4, 0x8, 0x10
DMPAPER_A4, DMPAPER_USER = 9, 256
DMORIENT_PORTRAIT = 1


class PrintError(RuntimeError):
    pass


def available() -> bool:
    return os.name == "nt" and win32print is not None


def list_printers() -> dict:
    if not available():
        return {"available": False, "printers": [], "default": ""}
    flags = win32print.PRINTER_ENUM_LOCAL | win32print.PRINTER_ENUM_CONNECTIONS
    names = sorted({entry[2] for entry in win32print.EnumPrinters(flags)}, key=str.lower)
    try:
        default = win32print.GetDefaultPrinter()
    except Exception:  # noqa: BLE001 - no default printer configured
        default = ""
    return {"available": True, "printers": names, "default": default}


def job_devmode(printer: str, width_mm: float, height_mm: float):
    """The printer's default settings with 100% scale and paper = the label size."""
    handle = win32print.OpenPrinter(printer)
    try:
        devmode = win32print.GetPrinter(handle, 2)["pDevMode"]
    finally:
        win32print.ClosePrinter(handle)
    if devmode is None:
        return None
    devmode.Scale = 100
    devmode.Orientation = DMORIENT_PORTRAIT
    fields = DM_SCALE | DM_ORIENTATION | DM_PAPERSIZE
    if abs(width_mm - 210) < 1 and abs(height_mm - 297) < 1:
        devmode.PaperSize = DMPAPER_A4
    else:
        devmode.PaperSize = DMPAPER_USER
        devmode.PaperWidth = round(width_mm * 10)    # tenths of a millimetre
        devmode.PaperLength = round(height_mm * 10)
        fields |= DM_PAPERWIDTH | DM_PAPERLENGTH
    devmode.Fields = devmode.Fields | fields
    return devmode


def printer_dc(printer: str, width_mm: float, height_mm: float):
    """A device context for the job; falls back to the driver's own settings if it
    rejects the requested paper size."""
    try:
        devmode = job_devmode(printer, width_mm, height_mm)
        if devmode is not None:
            return win32ui.CreateDCFromHandle(win32gui.CreateDC("WINSPOOL", printer, devmode))
    except Exception:  # noqa: BLE001 - some drivers refuse custom sizes
        pass
    dc = win32ui.CreateDC()
    dc.CreatePrinterDC(printer)
    return dc


def print_pdf(pdf_bytes: bytes, printer: str, job_name: str = "Label") -> int:
    """Print every page of the PDF on `printer` at 100% size. Returns pages printed."""
    if not available():
        raise PrintError("הדפסה ישירה זמינה רק במחשב Windows.")
    if printer not in list_printers()["printers"]:
        raise PrintError(f"המדפסת \"{printer}\" לא נמצאה במחשב.")
    import pypdfium2 as pdfium

    document = pdfium.PdfDocument(pdf_bytes)
    first = document[0]
    width_mm, height_mm = (v / 72 * 25.4 for v in first.get_size())
    first.close()
    dc = printer_dc(printer, width_mm, height_mm)
    try:
        dpi_x = dc.GetDeviceCaps(LOGPIXELSX)
        dpi_y = dc.GetDeviceCaps(LOGPIXELSY)
        offset_x = dc.GetDeviceCaps(PHYSICALOFFSETX)
        offset_y = dc.GetDeviceCaps(PHYSICALOFFSETY)
        render_scale = min(max(dpi_x, dpi_y), MAX_RENDER_DPI) / 72
        dc.StartDoc(job_name)
        pages = 0
        for index in range(len(document)):
            page = document[index]
            try:
                width_pt, height_pt = page.get_size()
                image = page.render(scale=render_scale).to_pil().convert("RGB")
            finally:
                page.close()
            # True physical size in device pixels, measured from the paper's edge.
            width_px = round(width_pt / 72 * dpi_x)
            height_px = round(height_pt / 72 * dpi_y)
            dc.StartPage()
            ImageWin.Dib(image).draw(dc.GetHandleOutput(), (-offset_x, -offset_y, width_px - offset_x, height_px - offset_y))
            dc.EndPage()
            pages += 1
        dc.EndDoc()
        return pages
    except PrintError:
        raise
    except Exception as exc:  # noqa: BLE001 - driver errors go back to the worker
        try:
            dc.AbortDoc()
        except Exception:  # noqa: BLE001
            pass
        raise PrintError(f"ההדפסה נכשלה: {exc}") from exc
    finally:
        try:
            dc.DeleteDC()
        finally:
            document.close()
