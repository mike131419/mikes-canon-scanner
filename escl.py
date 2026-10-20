"""
eSCL (AirScan / Mopria scan) client - talks to network scanners such as the
Canon imageCLASS MF635Cx directly over HTTP(S). No vendor driver needed.
"""
import concurrent.futures
import io
import ipaddress
import socket
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

NS = {
    "scan": "http://schemas.hp.com/imaging/escl/2011/05/03",
    "pwg": "http://www.pwg.org/schemas/2010/12/sm",
}

# Page sizes in 1/300 inch (eSCL "ThreeHundredthsOfInches")
PAGE_SIZES = {
    "A4": (2480, 3508),
    "Letter": (2550, 3300),
    "Legal": (2550, 4200),
    "A5": (1748, 2480),
    "Max (whole glass)": None,
}

COLOR_MODES = {
    "Color": "RGB24",
    "Grayscale": "Grayscale8",
    # B&W is scanned as grayscale and converted locally - more reliable with JPEG
    "Black & White": "Grayscale8",
}


class EsclError(Exception):
    pass


def _ssl_ctx():
    # Printers use self-signed certificates on the local network.
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx


class Capabilities:
    def __init__(self, xml_bytes):
        root = ET.fromstring(xml_bytes)
        self.make_model = _text(root, "pwg:MakeAndModel") or "Unknown scanner"
        self.serial = _text(root, "pwg:SerialNumber") or ""
        self.sources = {}  # "Platen"/"Feeder" -> dict
        for tag, name in (("scan:Platen", "Platen"), ("scan:Adf", "Feeder")):
            el = root.find(tag, NS)
            if el is None:
                continue
            inp = el.find("scan:PlatenInputCaps", NS)
            if inp is None:
                inp = el.find("scan:AdfSimplexInputCaps", NS)
            if inp is None:
                continue
            info = {
                "max_w": _int(inp, "scan:MaxWidth", 2550),
                "max_h": _int(inp, "scan:MaxHeight", 3508),
                "resolutions": sorted({
                    int(r.text) for r in inp.iter("{%s}XResolution" % NS["scan"])
                    if r.text and r.text.isdigit()
                }) or [150, 300, 600],
                "color_modes": [c.text for c in inp.iter("{%s}ColorMode" % NS["scan"]) if c.text],
                "formats": [f.text for f in inp.iter("{%s}DocumentFormat" % NS["pwg"]) if f.text],
                "duplex": False,
            }
            if name == "Feeder":
                info["duplex"] = el.find("scan:AdfDuplexInputCaps", NS) is not None
            self.sources[name] = info

    def __repr__(self):
        return f"<Capabilities {self.make_model} sources={list(self.sources)}>"


def _text(el, path):
    f = el.find(path, NS)
    return f.text.strip() if f is not None and f.text else None


def _int(el, path, default):
    t = _text(el, path)
    try:
        return int(t)
    except (TypeError, ValueError):
        return default


class EsclScanner:
    def __init__(self, host, timeout=8):
        self.host = host.strip()
        self.timeout = timeout
        self.base = None  # resolved base URL, e.g. http://192.168.0.82/eSCL
        self.caps = None

    # ---------- low level ----------
    def _request(self, url, method="GET", data=None, headers=None, timeout=None):
        req = urllib.request.Request(url, data=data, method=method, headers=headers or {})
        ctx = _ssl_ctx() if url.startswith("https") else None
        return urllib.request.urlopen(req, timeout=timeout or self.timeout, context=ctx)

    def _url(self, path):
        if path.startswith("http"):
            return path
        if path.startswith("/"):
            p = urllib.parse.urlparse(self.base)
            return f"{p.scheme}://{p.netloc}{path}"
        return f"{self.base}/{path}"

    # ---------- public ----------
    def connect(self):
        """Find the eSCL endpoint and read capabilities."""
        errors = []
        for scheme in ("http", "https"):
            for root in ("eSCL", "escl"):
                base = f"{scheme}://{self.host}/{root}"
                try:
                    with self._request(base + "/ScannerCapabilities") as r:
                        body = r.read()
                    self.caps = Capabilities(body)
                    self.base = base
                    return self.caps
                except Exception as e:  # noqa: BLE001
                    errors.append(f"{base}: {e}")
        raise EsclError("Scanner did not answer on eSCL.\n" + "\n".join(errors[:2]))

    def status(self):
        with self._request(self._url("ScannerStatus")) as r:
            root = ET.fromstring(r.read())
        state = _text(root, "pwg:State") or "Unknown"
        adf = _text(root, "scan:AdfState") or ""
        return state, adf

    def scan(self, source="Platen", color="Color", dpi=300, page="A4",
             duplex=False, progress=None, cancel=None):
        """Run a scan job and return a list of PIL Images."""
        from PIL import Image, ImageOps

        if self.caps is None:
            self.connect()
        if source not in self.caps.sources:
            raise EsclError(f"This scanner has no '{source}' input.")
        src = self.caps.sources[source]

        if dpi not in src["resolutions"]:
            dpi = min(src["resolutions"], key=lambda r: abs(r - dpi))
        w, h = PAGE_SIZES.get(page) or (src["max_w"], src["max_h"])
        w, h = min(w, src["max_w"]), min(h, src["max_h"])
        mode = COLOR_MODES[color]
        fmt = "image/jpeg" if not src["formats"] or "image/jpeg" in src["formats"] else src["formats"][0]
        use_duplex = bool(duplex and source == "Feeder" and src["duplex"])

        settings = f"""<?xml version="1.0" encoding="UTF-8"?>
<scan:ScanSettings xmlns:scan="{NS['scan']}" xmlns:pwg="{NS['pwg']}">
  <pwg:Version>2.0</pwg:Version>
  <scan:Intent>{'Photo' if color == 'Color' and source == 'Platen' else 'Document'}</scan:Intent>
  <pwg:ScanRegions>
    <pwg:ScanRegion>
      <pwg:Height>{h}</pwg:Height>
      <pwg:ContentRegionUnits>escl:ThreeHundredthsOfInches</pwg:ContentRegionUnits>
      <pwg:Width>{w}</pwg:Width>
      <pwg:XOffset>0</pwg:XOffset>
      <pwg:YOffset>0</pwg:YOffset>
    </pwg:ScanRegion>
  </pwg:ScanRegions>
  <pwg:InputSource>{source}</pwg:InputSource>
  {'<scan:Duplex>true</scan:Duplex>' if use_duplex else ''}
  <scan:ColorMode>{mode}</scan:ColorMode>
  <scan:XResolution>{dpi}</scan:XResolution>
  <scan:YResolution>{dpi}</scan:YResolution>
  <pwg:DocumentFormat>{fmt}</pwg:DocumentFormat>
  <scan:DocumentFormatExt>{fmt}</scan:DocumentFormatExt>
</scan:ScanSettings>""".encode()

        _p = progress or (lambda msg: None)
        _p("Starting scan job…")
        job = None
        for attempt in range(10):
            try:
                with self._request(self._url("ScanJobs"), "POST", settings,
                                   {"Content-Type": "text/xml"}) as r:
                    job = r.headers.get("Location")
                break
            except urllib.error.HTTPError as e:
                if e.code == 503 and attempt < 9:  # busy / warming up
                    _p("Scanner busy, waiting…")
                    time.sleep(2)
                    continue
                if e.code == 409:
                    raise EsclError("Scanner refused the job (409). Is the feeder empty, or a "
                                    "setting not supported?") from e
                raise EsclError(f"Scanner refused the job: HTTP {e.code}") from e
        if not job:
            raise EsclError("Scanner did not return a job address.")
        job_url = self._url(job).rstrip("/")

        pages = []
        while True:
            if cancel and cancel():
                self._delete(job_url)
                break
            _p(f"Receiving page {len(pages) + 1}…")
            try:
                with self._request(job_url + "/NextDocument", timeout=120) as r:
                    data = r.read()
            except urllib.error.HTTPError as e:
                if e.code == 404:  # no more pages
                    break
                if e.code == 503:
                    time.sleep(1.5)
                    continue
                if pages:
                    break
                raise EsclError(f"Failed to receive page: HTTP {e.code}") from e
            except (socket.timeout, TimeoutError) as e:
                raise EsclError("Timed out waiting for the scanner.") from e
            if not data:
                break
            pages.extend(_decode(data, fmt))
            if source == "Platen":
                # Flatbed gives one page; drain politely then stop.
                try:
                    with self._request(job_url + "/NextDocument", timeout=5) as r:
                        r.read()
                except Exception:  # noqa: BLE001
                    pass
                break

        if color == "Black & White":
            pages = [ImageOps.grayscale(p).point(lambda v: 255 if v > 160 else 0, "1") for p in pages]
        _p(f"Done: {len(pages)} page(s).")
        return pages

    def _delete(self, job_url):
        try:
            with self._request(job_url, "DELETE"):
                pass
        except Exception:  # noqa: BLE001
            pass


def _decode(data, fmt):
    from PIL import Image
    if fmt == "application/pdf" or data[:4] == b"%PDF":
        raise EsclError("Scanner sent PDF instead of images; not supported in this mode.")
    img = Image.open(io.BytesIO(data))
    img.load()
    return [img.convert("RGB") if img.mode not in ("RGB", "L", "1") else img]


# ---------- discovery ----------
def _probe(host, timeout=1.2):
    try:
        with socket.create_connection((host, 80), timeout=timeout):
            pass
    except OSError:
        return None
    try:
        s = EsclScanner(host, timeout=3)
        caps = s.connect()
        return host, caps.make_model
    except Exception:  # noqa: BLE001
        return None


def local_subnets():
    nets = set()
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("10.255.255.255", 1))
        ip = s.getsockname()[0]
        s.close()
        nets.add(ipaddress.ip_network(ip + "/24", strict=False))
    except OSError:
        pass
    return list(nets)


def discover(progress=None):
    """Scan the local /24 for eSCL scanners. Returns [(ip, model)]."""
    found = []
    for net in local_subnets():
        if progress:
            progress(f"Searching {net}…")
        hosts = [str(h) for h in net.hosts()]
        with concurrent.futures.ThreadPoolExecutor(64) as ex:
            for res in ex.map(_probe, hosts):
                if res:
                    found.append(res)
    return found
