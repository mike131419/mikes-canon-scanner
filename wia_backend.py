"""
Fallback: scan through Windows Image Acquisition (WIA), using whatever scanner
driver Windows already has for the printer (works with WSD and USB).
Requires: pip install pywin32
"""
import io
import os
import tempfile

WIA_FORMAT_JPEG = "{B96B3CAE-0728-11D3-9D7B-0000F81EF32E}"
# Item properties
COLOR_INTENT = 6146     # 1 = color, 2 = grayscale, 4 = text (B&W)
X_RES, Y_RES = 6147, 6148
X_EXTENT, Y_EXTENT = 6151, 6152
# Device properties
DOC_HANDLING_SELECT = 3088  # 1 = feeder, 2 = flatbed, 4 = duplex
PAGES = 3096

INTENTS = {"Color": 1, "Grayscale": 2, "Black & White": 4}
PAGE_INCHES = {"A4": (8.27, 11.69), "Letter": (8.5, 11.0), "Legal": (8.5, 14.0), "A5": (5.83, 8.27)}


class WiaError(Exception):
    pass


def _client():
    try:
        import win32com.client  # type: ignore
        return win32com.client
    except ImportError as e:
        raise WiaError("Windows scanning needs pywin32: run  pip install pywin32") from e


def list_scanners():
    wc = _client()
    dm = wc.Dispatch("WIA.DeviceManager")
    out = []
    for i in range(1, dm.DeviceInfos.Count + 1):
        info = dm.DeviceInfos.Item(i)
        if info.Type == 1:  # scanner
            out.append((info.DeviceID, info.Properties("Name").Value))
    return out


def _set(props, pid, value):
    for i in range(1, props.Count + 1):
        p = props.Item(i)
        if p.PropertyID == pid:
            try:
                p.Value = value
            except Exception:  # noqa: BLE001 - unsupported value; keep default
                pass
            return


def scan(device_id=None, source="Platen", color="Color", dpi=300, page="A4",
         duplex=False, progress=None, cancel=None):
    from PIL import Image

    _p = progress or (lambda m: None)
    wc = _client()
    dm = wc.Dispatch("WIA.DeviceManager")
    target = None
    for i in range(1, dm.DeviceInfos.Count + 1):
        info = dm.DeviceInfos.Item(i)
        if info.Type != 1:
            continue
        name = info.Properties("Name").Value
        if (device_id and info.DeviceID == device_id) or (not device_id and "canon" in name.lower()):
            target = info
            break
    if target is None:
        raise WiaError("No Canon scanner found in Windows. Check Settings > Printers & scanners.")

    _p(f"Connecting to {target.Properties('Name').Value}…")
    dev = target.Connect()
    if source == "Feeder":
        _set(dev.Properties, DOC_HANDLING_SELECT, 5 if duplex else 1)
        _set(dev.Properties, PAGES, 0)  # all pages
    else:
        _set(dev.Properties, DOC_HANDLING_SELECT, 2)
        _set(dev.Properties, PAGES, 1)

    item = dev.Items(1)
    _set(item.Properties, COLOR_INTENT, INTENTS[color])
    _set(item.Properties, X_RES, dpi)
    _set(item.Properties, Y_RES, dpi)
    if page in PAGE_INCHES:
        wi, hi = PAGE_INCHES[page]
        _set(item.Properties, X_EXTENT, int(wi * dpi))
        _set(item.Properties, Y_EXTENT, int(hi * dpi))

    pages = []
    tmpdir = tempfile.mkdtemp(prefix="canonscan_")
    while True:
        if cancel and cancel():
            break
        _p(f"Scanning page {len(pages) + 1}…")
        try:
            img = item.Transfer(WIA_FORMAT_JPEG)
        except Exception as e:  # noqa: BLE001
            if pages:  # feeder empty -> done
                break
            raise WiaError(f"Scan failed: {e}") from e
        path = os.path.join(tmpdir, f"p{len(pages)}.jpg")
        img.SaveFile(path)
        with open(path, "rb") as f:
            im = Image.open(io.BytesIO(f.read()))
            im.load()
        pages.append(im)
        if source != "Feeder":
            break
    if color == "Black & White":
        pages = [p.convert("L").point(lambda v: 255 if v > 160 else 0, "1") for p in pages]
    _p(f"Done: {len(pages)} page(s).")
    return pages
