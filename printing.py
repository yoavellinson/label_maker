"""Send label PDFs straight to a named Windows printer, at actual size.

Each label type can be assigned its own printer (thermal for the preprinted
labels, colour label printer for the narrow ones, office printer for A4).
Pages are rasterised with pdfium at the printer's resolution and placed at
their true physical size, centred on the paper and compensating for the
printer's unprintable margin, so there is no "fit to page" scaling. The paper
size and orientation come from the printer's own Windows settings; a portrait
label on landscape media (or the reverse) is turned 90 degrees to fit.

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
PHYSICALWIDTH, PHYSICALHEIGHT = 110, 111
DM_SCALE = 0x10
DM_OUT_BUFFER, DM_IN_PROMPT, DM_IN_BUFFER = 2, 4, 8
IDOK = 1


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


class PrintCancelled(PrintError):
    pass


def job_devmode(printer: str, prompt: bool = False):
    """The printer's own settings (paper size, orientation) with scaling forced to 100%.

    With `prompt`, the printer's own "Printing preferences" window opens first so
    the settings can be checked; Cancel raises PrintCancelled."""
    handle = win32print.OpenPrinter(printer)
    try:
        devmode = win32print.GetPrinter(handle, 2)["pDevMode"]
        if devmode is not None and prompt:
            # Own the window by the foreground window (the browser) so it shows on top.
            parent = win32gui.GetForegroundWindow() or 0
            result = win32print.DocumentProperties(
                parent, handle, printer, devmode, devmode, DM_IN_PROMPT | DM_IN_BUFFER | DM_OUT_BUFFER,
            )
            if result != IDOK:
                raise PrintCancelled("ההדפסה בוטלה.")
    finally:
        win32print.ClosePrinter(handle)
    if devmode is None:
        return None
    devmode.Scale = 100
    devmode.Fields = devmode.Fields | DM_SCALE
    return devmode


def printer_dc(printer: str, prompt: bool = False):
    """A device context for the job; falls back to plain driver defaults on driver errors."""
    try:
        devmode = job_devmode(printer, prompt)
        if devmode is not None:
            return win32ui.CreateDCFromHandle(win32gui.CreateDC("WINSPOOL", printer, devmode))
    except PrintCancelled:
        raise
    except Exception:  # noqa: BLE001
        pass
    dc = win32ui.CreateDC()
    dc.CreatePrinterDC(printer)
    return dc


def fit_to_media(image, width_px: int, height_px: int, media_w: int, media_h: int):
    """Turn a portrait label 90 degrees for landscape media (and vice versa).

    Label rolls are often loaded the other way round (e.g. 50x100 labels on a
    100 mm wide roll). Square labels and matching orientations are left alone."""
    label_landscape = width_px > height_px * 1.05
    label_portrait = height_px > width_px * 1.05
    media_landscape = media_w > media_h * 1.05
    media_portrait = media_h > media_w * 1.05
    if (label_portrait and media_landscape) or (label_landscape and media_portrait):
        return image.rotate(90, expand=True), height_px, width_px, True
    return image, width_px, height_px, False


def print_pdf(pdf_bytes: bytes, printer: str, job_name: str = "Label", prompt: bool = False) -> int:
    """Print every page of the PDF on `printer` at 100% size. Returns pages printed."""
    if not available():
        raise PrintError("הדפסה ישירה זמינה רק במחשב Windows.")
    if printer not in list_printers()["printers"]:
        raise PrintError(f"המדפסת \"{printer}\" לא נמצאה במחשב.")
    import pypdfium2 as pdfium

    document = pdfium.PdfDocument(pdf_bytes)
    dc = printer_dc(printer, prompt)
    try:
        dpi_x = dc.GetDeviceCaps(LOGPIXELSX)
        dpi_y = dc.GetDeviceCaps(LOGPIXELSY)
        offset_x = dc.GetDeviceCaps(PHYSICALOFFSETX)
        offset_y = dc.GetDeviceCaps(PHYSICALOFFSETY)
        media_w = dc.GetDeviceCaps(PHYSICALWIDTH)
        media_h = dc.GetDeviceCaps(PHYSICALHEIGHT)
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
            # True physical size in device pixels.
            width_px = round(width_pt / 72 * dpi_x)
            height_px = round(height_pt / 72 * dpi_y)
            if media_w and media_h:
                image, width_px, height_px, _ = fit_to_media(image, width_px, height_px, media_w, media_h)
                left = (media_w - width_px) // 2   # centred on the paper
                top = (media_h - height_px) // 2
            else:
                left = top = 0
            # Device coordinates start at the printable area, not the paper edge.
            x0, y0 = left - offset_x, top - offset_y
            dc.StartPage()
            ImageWin.Dib(image).draw(dc.GetHandleOutput(), (x0, y0, x0 + width_px, y0 + height_px))
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
