"""
Mike's Canon Scanner
Desktop scanning app for the Canon imageCLASS MF635Cx (any eSCL / AirScan
network scanner), with a Windows WIA fallback.

Run:  pythonw mikes_canon_scanner.py   (or double-click "Start Mike's Canon Scanner.bat")
"""
import json
import os
import queue
import threading
import time
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, simpledialog, ttk

from PIL import Image, ImageTk

import escl
import updater
from pages import Job, Page, merge_jobs, safe_name, save_job, upscale
from version import __version__

APP_NAME = "Mike's Canon Scanner"


def resource(name):
    """Path to a bundled file, both from source and inside the installed .exe."""
    import sys
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, name)
CONFIG = Path.home() / ".mikes_canon_scanner.json"
OLD_CONFIG = Path.home() / ".canon_scanner.json"
WIA_NATIVE = [150, 200, 300, 600]
ENHANCED = [1200]
MAX_PIXELS = 400_000_000  # guard: refuse scans that would need > ~1.2 GB of memory per page

DEFAULTS = {
    "host": "192.168.0.82", "method": "Network", "source": "Platen", "duplex": False,
    "color": "Color", "dpi": 300, "page": "A4", "fmt": "PDF",
    "folder": str(Path.home() / "Documents" / "Scans"),
}

PAD = 24          # canvas margin
GAP = 18          # gap between pages


def load_cfg():
    cfg = dict(DEFAULTS)
    for p in (OLD_CONFIG, CONFIG):
        try:
            cfg.update(json.loads(p.read_text()))
        except Exception:  # noqa: BLE001
            pass
    return cfg


def save_cfg(cfg):
    try:
        CONFIG.write_text(json.dumps(cfg, indent=2))
    except Exception:  # noqa: BLE001
        pass


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(f"{APP_NAME} {__version__}")
        try:
            self.iconbitmap(default=resource("assets/icon.ico"))
        except tk.TclError:
            pass
        self.update_info = None
        self.geometry("1400x860")
        self.minsize(1100, 680)
        try:
            self.state("zoomed")
        except tk.TclError:
            pass
        self.cfg = load_cfg()
        self.scanner = None
        self.native_dpis = list(WIA_NATIVE)
        self.busy = False
        self.cancel_flag = False
        self.q = queue.Queue()

        self.jobs = []
        self.job = None           # job shown in preview
        self.page = None          # selected page
        self.scan_count = 0

        self.zoom = 0.15
        self.layout = []          # [(page, x, y, w, h)] in canvas coords
        self._imgs = []           # PhotoImage refs
        self._proxy = None        # (page_id, ver, factor, small image) for live angle preview
        self._rot = None          # ((page_id, ver, angle), rotated proxy)
        self._redraw_job = None
        self.crop_mode = tk.BooleanVar(value=False)
        self.crop = None          # (page, l, t, r, b) in page pixels
        self._drag = None

        self._style()
        self._build()
        self._bind_keys()
        self.after(100, self._pump)
        self.after(300, self.connect)
        self.after(3000, lambda: self.check_updates(quiet=True))

    # ================================================================ updates
    def check_updates(self, quiet=False):
        if not quiet:
            self._set_status("Checking for updates…")

        def done(info, err):
            if err or not info:
                if not quiet:
                    messagebox.showwarning(APP_NAME, f"Could not check for updates.\n\n{err}")
                return
            if updater.is_newer(info["version"]):
                self.update_info = info
                self.update_btn.configure(text=f"⬆ Update to {info['version']}")
                self.update_btn.pack(side="right", padx=8)
                self._set_status(f"Version {info['version']} is available – click the Update button.")
                if not quiet:
                    self.install_update()
            elif not quiet:
                self._set_status("You have the latest version.")
                messagebox.showinfo(APP_NAME, f"You have the latest version ({__version__}).")
        self._run(updater.check, done)

    def install_update(self):
        info = self.update_info
        if not info:
            return self.check_updates()
        notes = info["notes"].strip()
        msg = f"Update {APP_NAME} from {__version__} to {info['version']}?"
        if notes:
            msg += "\n\nWhat's new:\n" + (notes[:800] + ("…" if len(notes) > 800 else ""))
        if not updater.is_installed():
            msg += "\n\n(You are running from source, so the download page will open instead.)"
        if not messagebox.askyesno(APP_NAME, msg):
            return
        if not updater.is_installed() or not info["setup_url"]:
            webbrowser_open = updater.open_releases
            webbrowser_open()
            return
        if self.jobs and not messagebox.askyesno(
                APP_NAME, "The app will close to update. Unsaved scans will be lost.\n\nContinue?"):
            return
        self._set_status("Downloading update…")

        def done(path, err):
            if err:
                messagebox.showerror(APP_NAME, f"Update download failed:\n{err}")
                return
            self._set_status("Installing update – the app will restart…")
            try:
                updater.run_installer(path)
            except Exception as e:  # noqa: BLE001
                messagebox.showerror(APP_NAME, f"Could not start the installer:\n{e}")
                return
            self.after(500, self.destroy)
        self._run(lambda: updater.download(info["setup_url"], self._progress), done)

    def _about(self):
        messagebox.showinfo(APP_NAME, f"{APP_NAME}\nVersion {__version__}\n\n"
                            f"Scanner app for the Canon imageCLASS MF635Cx.\n"
                            f"Updates: {updater.RELEASES_PAGE}")

    def _build_menu(self):
        m = tk.Menu(self)
        filem = tk.Menu(m, tearoff=0)
        filem.add_command(label="Save selected job(s)", command=self.save_selected)
        filem.add_command(label="Save all jobs", command=self.save_all)
        filem.add_separator()
        filem.add_command(label="Exit", command=self.destroy)
        m.add_cascade(label="File", menu=filem)
        helpm = tk.Menu(m, tearoff=0)
        helpm.add_command(label="Check for updates…", command=self.check_updates)
        helpm.add_command(label="Release notes / downloads", command=updater.open_releases)
        helpm.add_separator()
        helpm.add_command(label=f"About ({__version__})", command=self._about)
        m.add_cascade(label="Help", menu=helpm)
        self.config(menu=m)

    # ================================================================ UI build
    def _style(self):
        s = ttk.Style(self)
        try:
            s.theme_use("vista")
        except tk.TclError:
            s.theme_use("clam")
        s.configure("Big.TButton", font=("Segoe UI", 11, "bold"), padding=7)
        s.configure("H.TLabel", font=("Segoe UI", 10, "bold"))
        s.configure("Title.TLabel", font=("Segoe UI", 14, "bold"))
        s.configure("Hint.TLabel", foreground="#777", font=("Segoe UI", 8))
        s.configure("Update.TButton", font=("Segoe UI", 10, "bold"), foreground="#b35900")

    def _build(self):
        c = self.cfg
        # ---------- top bar ----------
        top = ttk.Frame(self, padding=(10, 6))
        top.pack(fill="x")
        ttk.Label(top, text=APP_NAME, style="Title.TLabel").pack(side="left", padx=(0, 20))
        ttk.Label(top, text="Scanner IP:").pack(side="left")
        self.host_var = tk.StringVar(value=c["host"])
        ttk.Entry(top, textvariable=self.host_var, width=15).pack(side="left", padx=4)
        ttk.Button(top, text="Connect", command=self.connect).pack(side="left")
        ttk.Button(top, text="Find on network", command=self.find).pack(side="left", padx=4)
        ttk.Label(top, text="  Method:").pack(side="left")
        self.method_var = tk.StringVar(value=c["method"])
        ttk.Radiobutton(top, text="Network (eSCL)", value="Network", variable=self.method_var,
                        command=self.connect).pack(side="left")
        ttk.Radiobutton(top, text="Windows driver (WIA)", value="WIA", variable=self.method_var,
                        command=self.connect).pack(side="left", padx=4)
        self.conn_lbl = ttk.Label(top, text="Not connected", foreground="#a00")
        self.conn_lbl.pack(side="right")
        self.update_btn = ttk.Button(top, text="Update", style="Update.TButton",
                                     command=self.install_update)   # shown when an update exists
        ttk.Separator(self).pack(fill="x")
        self._build_menu()

        self.status = ttk.Label(self, text="Ready", anchor="w", padding=(10, 3), relief="sunken")
        self.status.pack(fill="x", side="bottom")

        body = ttk.Frame(self)
        body.pack(fill="both", expand=True)
        self._build_left(body)
        self._build_jobs(body)
        self._build_edit(body)      # packed right before preview so it stays visible
        self._build_preview(body)
        self._sync()

    def _section(self, parent, text):
        ttk.Label(parent, text=text, style="H.TLabel").pack(anchor="w", pady=(9, 2))

    def _build_left(self, body):
        c = self.cfg
        left = ttk.Frame(body, padding=(12, 4, 8, 8))
        left.pack(side="left", fill="y")

        self._section(left, "Source")
        self.source_var = tk.StringVar(value=c["source"])
        self.rb_platen = ttk.Radiobutton(left, text="Flatbed (glass)", value="Platen",
                                         variable=self.source_var, command=self._sync)
        self.rb_platen.pack(anchor="w")
        self.rb_feeder = ttk.Radiobutton(left, text="Document feeder", value="Feeder",
                                         variable=self.source_var, command=self._sync)
        self.rb_feeder.pack(anchor="w")
        self.duplex_var = tk.BooleanVar(value=c["duplex"])
        self.duplex_cb = ttk.Checkbutton(left, text="Double-sided", variable=self.duplex_var)
        self.duplex_cb.pack(anchor="w", padx=18)

        self._section(left, "Colour")
        self.color_var = tk.StringVar(value=c["color"])
        ttk.Combobox(left, textvariable=self.color_var, state="readonly", width=22,
                     values=list(escl.COLOR_MODES)).pack(anchor="w")

        self._section(left, "Resolution (DPI)")
        self.dpi_var = tk.StringVar()
        self.dpi_cb = ttk.Combobox(left, textvariable=self.dpi_var, state="readonly", width=22)
        self.dpi_cb.pack(anchor="w")
        self.dpi_cb.bind("<<ComboboxSelected>>", lambda e: self._dpi_hint())
        self.dpi_hint = ttk.Label(left, text="", style="Hint.TLabel", wraplength=190)
        self.dpi_hint.pack(anchor="w")
        self._fill_dpis(c["dpi"])

        self._section(left, "Paper size")
        self.page_var = tk.StringVar(value=c["page"])
        ttk.Combobox(left, textvariable=self.page_var, state="readonly", width=22,
                     values=list(escl.PAGE_SIZES)).pack(anchor="w")

        self.addto_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(left, text="Add next scan to the\nselected job (don't start new)",
                        variable=self.addto_var).pack(anchor="w", pady=(12, 0))

        self.scan_btn = ttk.Button(left, text="Scan", style="Big.TButton", command=self.scan)
        self.scan_btn.pack(fill="x", pady=(8, 3))
        self.cancel_btn = ttk.Button(left, text="Cancel scan", command=self._cancel, state="disabled")
        self.cancel_btn.pack(fill="x")

        ttk.Separator(left).pack(fill="x", pady=12)
        ttk.Label(left, text="Save", style="H.TLabel").pack(anchor="w")
        self.fmt_var = tk.StringVar(value=c["fmt"])
        for v, t in (("PDF", "PDF (all pages in one file)"), ("JPG", "JPG (one per page)"),
                     ("PNG", "PNG (one per page)")):
            ttk.Radiobutton(left, text=t, value=v, variable=self.fmt_var).pack(anchor="w")
        ttk.Label(left, text="Folder:").pack(anchor="w", pady=(6, 0))
        ff = ttk.Frame(left)
        ff.pack(anchor="w", fill="x")
        self.folder_var = tk.StringVar(value=c["folder"])
        ttk.Entry(ff, textvariable=self.folder_var, width=20).pack(side="left", fill="x", expand=True)
        ttk.Button(ff, text="…", width=3, command=self._pick_folder).pack(side="left", padx=2)
        ttk.Button(left, text="Save selected job(s)", style="Big.TButton",
                   command=self.save_selected).pack(fill="x", pady=(8, 3))
        ttk.Button(left, text="Save all jobs", command=self.save_all).pack(fill="x")

    def _build_jobs(self, body):
        mid = ttk.Frame(body, padding=(4, 8, 4, 8))
        mid.pack(side="left", fill="y")
        ttk.Label(mid, text="Scan jobs", style="H.TLabel").pack(anchor="w")
        ttk.Label(mid, text="Ctrl+click to select several", style="Hint.TLabel").pack(anchor="w")
        tf = ttk.Frame(mid)
        tf.pack(fill="both", expand=True, pady=4)
        self.tree = ttk.Treeview(tf, show="tree", selectmode="extended", height=20)
        self.tree.column("#0", width=210)
        sb = ttk.Scrollbar(tf, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=sb.set)
        self.tree.pack(side="left", fill="both", expand=True)
        sb.pack(side="left", fill="y")
        self.tree.bind("<<TreeviewSelect>>", self._on_tree)
        self.tree.bind("<Double-1>", lambda e: self.rename_job())
        for txt, cmd in (("Merge selected jobs", self.merge_selected),
                         ("Rename job", self.rename_job),
                         ("Delete selected", self.delete_selected)):
            ttk.Button(mid, text=txt, command=cmd).pack(fill="x", pady=1)

    def _build_preview(self, body):
        pv = ttk.Frame(body, padding=(4, 8, 4, 8))
        pv.pack(side="left", fill="both", expand=True)
        bar = ttk.Frame(pv)
        bar.pack(fill="x", pady=(0, 4))
        ttk.Button(bar, text="−", width=3, command=lambda: self.zoom_by(1 / 1.25)).pack(side="left")
        self.zoom_lbl = ttk.Label(bar, text="", width=7, anchor="center")
        self.zoom_lbl.pack(side="left")
        ttk.Button(bar, text="+", width=3, command=lambda: self.zoom_by(1.25)).pack(side="left")
        ttk.Button(bar, text="Fit width", command=self.fit_width).pack(side="left", padx=(8, 2))
        ttk.Button(bar, text="Fit page", command=self.fit_page).pack(side="left", padx=2)
        ttk.Button(bar, text="100% (actual pixels)", command=lambda: self.set_zoom(1.0)).pack(
            side="left", padx=2)
        ttk.Button(bar, text="200%", command=lambda: self.set_zoom(2.0)).pack(side="left", padx=2)
        ttk.Label(bar, text="Ctrl+wheel: zoom · drag: pan",
                  style="Hint.TLabel").pack(side="right")

        cf = ttk.Frame(pv)
        cf.pack(fill="both", expand=True)
        self.canvas = tk.Canvas(cf, bg="#3a3a3a", highlightthickness=0, xscrollincrement=1,
                                yscrollincrement=1)
        self.vbar = ttk.Scrollbar(cf, orient="vertical", command=self._yview)
        self.hbar = ttk.Scrollbar(cf, orient="horizontal", command=self._xview)
        self.canvas.configure(yscrollcommand=self.vbar.set, xscrollcommand=self.hbar.set)
        self.canvas.grid(row=0, column=0, sticky="nsew")
        self.vbar.grid(row=0, column=1, sticky="ns")
        self.hbar.grid(row=1, column=0, sticky="ew")
        cf.rowconfigure(0, weight=1)
        cf.columnconfigure(0, weight=1)

        cv = self.canvas
        cv.bind("<Configure>", lambda e: self._relayout())
        cv.bind("<MouseWheel>", self._wheel)
        cv.bind("<Shift-MouseWheel>", self._wheel_h)
        cv.bind("<Control-MouseWheel>", self._wheel_zoom)
        cv.bind("<Button-4>", lambda e: self._scroll(-120))   # Linux
        cv.bind("<Button-5>", lambda e: self._scroll(120))
        cv.bind("<ButtonPress-1>", self._press)
        cv.bind("<B1-Motion>", self._motion)
        cv.bind("<ButtonRelease-1>", self._release)

    def _build_edit(self, body):
        ed = ttk.Frame(body, padding=(8, 8, 12, 8))
        ed.pack(side="right", fill="y")
        ttk.Label(ed, text="Edit selected page", style="H.TLabel").pack(anchor="w")
        self.info_lbl = ttk.Label(ed, text="No page selected", wraplength=220, justify="left")
        self.info_lbl.pack(anchor="w", pady=(2, 4))

        self._section(ed, "Rotate")
        r = ttk.Frame(ed)
        r.pack(fill="x")
        ttk.Button(r, text="Left 90°", command=lambda: self.rotate(90)).pack(side="left", expand=True, fill="x")
        ttk.Button(r, text="Right 90°", command=lambda: self.rotate(-90)).pack(side="left", expand=True, fill="x")
        ttk.Button(r, text="180°", command=lambda: self.rotate(180)).pack(side="left", expand=True, fill="x")

        self._section(ed, "Straighten (any angle)")
        self.angle_var = tk.DoubleVar(value=0.0)
        self.angle_scale = tk.Scale(ed, from_=-45, to=45, resolution=0.1, orient="horizontal",
                                    variable=self.angle_var, showvalue=False, length=220,
                                    command=lambda v: self._angle_changed())
        self.angle_scale.pack(fill="x")
        a = ttk.Frame(ed)
        a.pack(fill="x")
        ttk.Label(a, text="Angle °").pack(side="left")
        sp = ttk.Spinbox(a, from_=-180, to=180, increment=0.1, width=7, textvariable=self.angle_var,
                         command=self._angle_changed)
        sp.pack(side="left", padx=4)
        sp.bind("<Return>", lambda e: self._angle_changed())
        sp.bind("<FocusOut>", lambda e: self._angle_changed())
        ttk.Button(a, text="0", width=3, command=lambda: (self.angle_var.set(0), self._angle_changed())
                   ).pack(side="left")
        ttk.Label(ed, text="+ = clockwise. A grid appears while adjusting.",
                  style="Hint.TLabel").pack(anchor="w")
        ttk.Button(ed, text="Apply angle", command=self.apply_angle).pack(fill="x", pady=(3, 0))

        self._section(ed, "Crop")
        ttk.Checkbutton(ed, text="Crop tool (drag a box on the page)", variable=self.crop_mode,
                        command=self._crop_toggled).pack(anchor="w")
        cr = ttk.Frame(ed)
        cr.pack(fill="x", pady=(2, 0))
        self.crop_apply_btn = ttk.Button(cr, text="Apply crop", command=self.apply_crop, state="disabled")
        self.crop_apply_btn.pack(side="left", expand=True, fill="x")
        ttk.Button(cr, text="Clear box", command=self._clear_crop).pack(side="left", expand=True, fill="x")
        self.crop_lbl = ttk.Label(ed, text="", style="Hint.TLabel")
        self.crop_lbl.pack(anchor="w")

        self._section(ed, "Enlarge / shrink")
        s = ttk.Frame(ed)
        s.pack(fill="x")
        self.scale_var = tk.StringVar(value="100")
        ssp = ttk.Spinbox(s, from_=10, to=400, increment=5, width=6, textvariable=self.scale_var,
                          command=self._scale_hint)
        ssp.pack(side="left")
        ssp.bind("<KeyRelease>", lambda e: self._scale_hint())
        ttk.Label(s, text="%").pack(side="left")
        ttk.Button(s, text="Apply", command=self.apply_scale).pack(side="left", padx=6)
        self.scale_lbl = ttk.Label(ed, text="", style="Hint.TLabel", wraplength=220)
        self.scale_lbl.pack(anchor="w")

        self.all_pages_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(ed, text="Apply rotate / angle / resize\nto all pages in this job",
                        variable=self.all_pages_var).pack(anchor="w", pady=(10, 0))

        self._section(ed, "Page")
        o = ttk.Frame(ed)
        o.pack(fill="x")
        ttk.Button(o, text="Move up", command=lambda: self.move_page(-1)).pack(side="left", expand=True, fill="x")
        ttk.Button(o, text="Move down", command=lambda: self.move_page(1)).pack(side="left", expand=True, fill="x")
        ttk.Button(ed, text="Undo last edit   (Ctrl+Z)", command=self.undo).pack(fill="x", pady=(6, 1))
        ttk.Button(ed, text="Reset page to original scan", command=self.reset_page).pack(fill="x")

    def _bind_keys(self):
        self.bind("<Control-z>", lambda e: self.undo())
        self.bind("<Escape>", lambda e: self._clear_crop())
        self.bind("<Control-plus>", lambda e: self.zoom_by(1.25))
        self.bind("<Control-equal>", lambda e: self.zoom_by(1.25))
        self.bind("<Control-minus>", lambda e: self.zoom_by(1 / 1.25))
        self.bind("<Control-0>", lambda e: self.fit_width())

    # ================================================================ settings helpers
    def _fill_dpis(self, want=None):
        nat = sorted(set(self.native_dpis))
        vals = [str(d) for d in nat] + [f"{d} (enhanced)" for d in ENHANCED if d > max(nat)]
        self.dpi_cb["values"] = vals
        want = want or self._dpi()
        match = next((v for v in vals if int(v.split()[0]) == want), None)
        self.dpi_var.set(match or str(min(nat, key=lambda d: abs(d - 300))))
        self._dpi_hint()

    def _dpi(self):
        try:
            return int(self.dpi_var.get().split()[0])
        except (ValueError, IndexError):
            return 300

    def _dpi_hint(self):
        d, mx = self._dpi(), max(self.native_dpis)
        if d > mx:
            self.dpi_hint.configure(
                text=f"Scanner's optical maximum is {mx} DPI. Higher values are enlarged "
                     f"by software (like Canon's driver does) – more pixels, files get big.")
        elif d >= 600:
            self.dpi_hint.configure(text="Highest true optical detail. Large files.")
        else:
            self.dpi_hint.configure(text="")

    def _sync(self):
        feeder_ok = duplex_ok = True
        if self.method_var.get() == "Network" and self.scanner and self.scanner.caps:
            srcs = self.scanner.caps.sources
            feeder_ok = "Feeder" in srcs
            duplex_ok = feeder_ok and srcs["Feeder"]["duplex"]
            src = srcs.get(self.source_var.get()) or next(iter(srcs.values()))
            self.native_dpis = src["resolutions"]
        else:
            self.native_dpis = list(WIA_NATIVE)
        self._fill_dpis()
        self.rb_feeder.configure(state="normal" if feeder_ok else "disabled")
        if not feeder_ok:
            self.source_var.set("Platen")
        self.duplex_cb.configure(state="normal" if (self.source_var.get() == "Feeder" and duplex_ok)
                                 else "disabled")

    def _set_status(self, msg):
        self.status.configure(text=msg)

    def _pick_folder(self):
        d = filedialog.askdirectory(initialdir=self.folder_var.get() or str(Path.home()))
        if d:
            self.folder_var.set(d)

    def _remember(self):
        self.cfg.update(host=self.host_var.get().strip(), method=self.method_var.get(),
                        source=self.source_var.get(), duplex=self.duplex_var.get(),
                        color=self.color_var.get(), dpi=self._dpi(), page=self.page_var.get(),
                        fmt=self.fmt_var.get(), folder=self.folder_var.get())
        save_cfg(self.cfg)

    # ================================================================ threading
    def _run(self, fn, done):
        def worker():
            try:
                self.q.put(("done", done, fn(), None))
            except Exception as e:  # noqa: BLE001
                self.q.put(("done", done, None, e))
        threading.Thread(target=worker, daemon=True).start()

    def _progress(self, msg):
        self.q.put(("status", msg))

    def _pump(self):
        try:
            while True:
                item = self.q.get_nowait()
                if item[0] == "status":
                    self._set_status(item[1])
                else:
                    _, done, res, err = item
                    done(res, err)
        except queue.Empty:
            pass
        self.after(100, self._pump)

    # ================================================================ connection
    def connect(self):
        method = self.method_var.get()
        self.conn_lbl.configure(text="Connecting…", foreground="#666")
        if method == "WIA":
            def fn():
                import wia_backend
                return wia_backend.list_scanners()

            def done(res, err):
                if err:
                    self.conn_lbl.configure(text="Windows scanner not available", foreground="#a00")
                    self._set_status(str(err))
                else:
                    canon = [n for _, n in res if "canon" in n.lower()]
                    if canon:
                        self.conn_lbl.configure(text=f"● {canon[0]} (Windows)", foreground="#080")
                        self._set_status("Ready (using Windows scanner driver).")
                    else:
                        self.conn_lbl.configure(text="No Canon scanner in Windows", foreground="#a00")
                        self._set_status("Found: " + (", ".join(n for _, n in res) or "no scanners"))
                self._sync()
            self._run(fn, done)
            return

        host = self.host_var.get().strip()
        self._set_status(f"Contacting {host}…")

        def fn():
            s = escl.EsclScanner(host)
            s.connect()
            try:
                state, adf = s.status()
            except Exception:  # noqa: BLE001
                state, adf = "Unknown", ""
            return s, state, adf

        def done(res, err):
            if err:
                self.scanner = None
                self.conn_lbl.configure(text="Not connected", foreground="#a00")
                self._set_status(f"Could not reach the scanner at {host}. Wake the printer, try "
                                 "'Find on network', or switch Method to Windows driver.")
                self._sync()
                return
            self.scanner, state, adf = res
            caps = self.scanner.caps
            srcs = ", ".join("Flatbed" if k == "Platen" else
                             ("Feeder (double-sided)" if v["duplex"] else "Feeder")
                             for k, v in caps.sources.items())
            self.conn_lbl.configure(text=f"● {caps.make_model}", foreground="#080")
            self._set_status(f"Connected. Status: {state}. Inputs: {srcs}." + (f" Feeder: {adf}" if adf else ""))
            self._remember()
            self._sync()
        self._run(fn, done)

    def find(self):
        self._set_status("Searching your network for scanners (about 10–30 s)…")

        def done(res, err):
            if err or not res:
                self._set_status("No network scanners found. Make sure the printer is on and on the same Wi-Fi.")
                return
            self.host_var.set(res[0][0])
            self._set_status("Found: " + ", ".join(f"{m} at {i}" for i, m in res))
            self.connect()
        self._run(lambda: escl.discover(self._progress), done)

    # ================================================================ scanning
    def scan(self):
        if self.busy:
            return
        self._remember()
        want = self._dpi()
        nat = sorted(self.native_dpis)
        scan_dpi = want if want in nat else (nat[-1] if want > nat[-1] else min(nat, key=lambda d: abs(d - want)))
        factor = want / scan_dpi
        w_in, h_in = 8.5, 14
        if want * want * w_in * h_in > MAX_PIXELS and factor > 1:
            if not messagebox.askyesno(APP_NAME, f"{want} DPI pages are very large (hundreds of MB each "
                                       "in memory). Continue?"):
                return
        opts = dict(source=self.source_var.get(), color=self.color_var.get(), dpi=scan_dpi,
                    page=self.page_var.get(), duplex=self.duplex_var.get())
        method = self.method_var.get()
        self.busy, self.cancel_flag = True, False
        self.scan_btn.configure(state="disabled")
        self.cancel_btn.configure(state="normal")

        def fn():
            if method == "WIA":
                import wia_backend
                imgs = wia_backend.scan(progress=self._progress, cancel=lambda: self.cancel_flag, **opts)
            else:
                if not self.scanner:
                    self.scanner = escl.EsclScanner(self.host_var.get())
                imgs = self.scanner.scan(progress=self._progress, cancel=lambda: self.cancel_flag, **opts)
            if factor > 1:
                out = []
                for i, im in enumerate(imgs, 1):
                    self._progress(f"Enhancing page {i} to {want} DPI…")
                    out.append(upscale(im, factor))
                imgs = out
            return [Page(im, want, enhanced=factor > 1) for im in imgs]

        def done(pages, err):
            self.busy = False
            self.scan_btn.configure(state="normal")
            self.cancel_btn.configure(state="disabled")
            if err:
                self._set_status(f"Scan failed: {err}")
                messagebox.showerror("Scan failed", str(err))
                return
            if not pages:
                self._set_status("No pages were scanned.")
                return
            target = self.job if (self.addto_var.get() and self.job in self.jobs) else None
            if target:
                target.pages.extend(pages)
            else:
                self.scan_count += 1
                target = Job(f"Scan {self.scan_count} – {time.strftime('%H:%M')}", pages)
                self.jobs.append(target)
            self._refresh_tree()
            self.show_job(target, pages[0], fit=True)
            if self.tree.exists(f"p{pages[0].id}"):
                self.tree.selection_set(f"p{pages[0].id}")
                self.tree.see(f"p{pages[0].id}")
            self._set_status(f"Scanned {len(pages)} page(s) at {want} DPI into “{target.name}”.")
        self._run(fn, done)

    def _cancel(self):
        self.cancel_flag = True
        self._set_status("Cancelling…")

    # ================================================================ jobs tree
    def _refresh_tree(self):
        sel = set(self.tree.selection())
        open_ = {i for i in self.tree.get_children() if self.tree.item(i, "open")}
        self.tree.delete(*self.tree.get_children())
        for j in self.jobs:
            jid = f"j{j.id}"
            self.tree.insert("", "end", iid=jid, text=f"📄 {j.name}  ({len(j.pages)} p)",
                             open=(jid in open_ or j is self.job))
            for i, p in enumerate(j.pages, 1):
                edited = " ✎" if p.ops else ""
                self.tree.insert(jid, "end", iid=f"p{p.id}", text=f"Page {i}{edited}")
        keep = [i for i in sel if self.tree.exists(i)]
        if keep:
            self.tree.selection_set(keep)

    def _find_page(self, pid):
        for j in self.jobs:
            for p in j.pages:
                if p.id == pid:
                    return j, p
        return None, None

    def _selected_jobs(self):
        out = []
        for iid in self.tree.selection():
            if iid.startswith("j"):
                j = next((j for j in self.jobs if f"j{j.id}" == iid), None)
            else:
                j, _ = self._find_page(int(iid[1:]))
            if j and j not in out:
                out.append(j)
        if not out and self.job:
            out = [self.job]
        return out

    def _on_tree(self, _e=None):
        sel = self.tree.selection()
        if not sel:
            return
        iid = sel[-1]
        if iid.startswith("p"):
            j, p = self._find_page(int(iid[1:]))
            if p and not (p is self.page and j is self.job):
                self.show_job(j, p, scroll_to=True)
        else:
            j = next((j for j in self.jobs if f"j{j.id}" == iid), None)
            if j and j is not self.job:
                self.show_job(j, j.pages[0] if j.pages else None, fit=True)

    def merge_selected(self):
        jobs = [j for j in self.jobs if j in self._selected_jobs()]   # keep list order
        if len(jobs) < 2:
            messagebox.showinfo(APP_NAME, "Select two or more jobs (Ctrl+click) to merge them.")
            return
        name = simpledialog.askstring(APP_NAME, "Name for the merged job:",
                                      initialvalue=" + ".join(j.name.split(" – ")[0] for j in jobs),
                                      parent=self)
        if name is None:
            return
        merged = merge_jobs(jobs, name or None)
        idx = self.jobs.index(jobs[0])
        self.jobs = [j for j in self.jobs if j not in jobs]
        self.jobs.insert(idx, merged)
        self._refresh_tree()
        self.show_job(merged, merged.pages[0], fit=True)
        self._set_status(f"Merged {len(jobs)} jobs into “{merged.name}” ({len(merged.pages)} pages).")

    def rename_job(self):
        jobs = self._selected_jobs()
        if not jobs:
            return
        j = jobs[0]
        name = simpledialog.askstring(APP_NAME, "Job name:", initialvalue=j.name, parent=self)
        if name:
            j.name = name
            self._refresh_tree()

    def delete_selected(self):
        sel = self.tree.selection()
        if not sel:
            return
        pages = [s for s in sel if s.startswith("p")]
        jobs = [s for s in sel if s.startswith("j")]
        what = (f"{len(jobs)} job(s)" if jobs else "") + (" and " if jobs and pages else "") + \
               (f"{len(pages)} page(s)" if pages else "")
        if not messagebox.askyesno(APP_NAME, f"Delete {what}?"):
            return
        for iid in pages:
            j, p = self._find_page(int(iid[1:]))
            if j:
                j.pages.remove(p)
        self.jobs = [j for j in self.jobs if f"j{j.id}" not in jobs and j.pages]
        if self.job not in self.jobs:
            self.job = self.jobs[-1] if self.jobs else None
        if self.job and self.page not in self.job.pages:
            self.page = self.job.pages[0] if self.job.pages else None
        self._refresh_tree()
        self.show_job(self.job, self.page)

    # ================================================================ saving
    def _save_jobs(self, jobs, ask_path):
        if not jobs:
            messagebox.showinfo(APP_NAME, "Nothing to save yet – scan something first.")
            return
        self._remember()
        fmt = self.fmt_var.get()
        folder = Path(self.folder_var.get() or Path.home())
        folder.mkdir(parents=True, exist_ok=True)
        written = []
        self.config(cursor="watch")
        self.update_idletasks()
        try:
            for j in jobs:
                if fmt == "PDF":
                    default = folder / (safe_name(j.name) + ".pdf")
                    if ask_path and len(jobs) == 1:
                        path = filedialog.asksaveasfilename(initialdir=folder, initialfile=default.name,
                                                            defaultextension=".pdf",
                                                            filetypes=[("PDF", "*.pdf")])
                        if not path:
                            return
                    else:
                        path, n = default, 2
                        while Path(path).exists():
                            path = folder / f"{safe_name(j.name)} ({n}).pdf"
                            n += 1
                    written += save_job(j, "PDF", path)
                else:
                    written += save_job(j, fmt, folder)
        except Exception as e:  # noqa: BLE001
            messagebox.showerror(APP_NAME, f"Could not save: {e}")
            return
        finally:
            self.config(cursor="")
        self._set_status(f"Saved {len(written)} file(s) to {os.path.dirname(written[0])}")
        if messagebox.askyesno("Saved", f"Saved {len(written)} file(s).\n\nOpen the folder?"):
            try:
                os.startfile(os.path.dirname(written[0]))  # noqa: S606 (Windows)
            except AttributeError:
                pass

    def save_selected(self):
        self._save_jobs(self._selected_jobs(), ask_path=True)

    def save_all(self):
        self._save_jobs(list(self.jobs), ask_path=False)

    # ================================================================ preview: layout & drawing
    def show_job(self, job, page=None, fit=False, scroll_to=False):
        changed = job is not self.job
        self.job = job
        if page is not self.page:
            self.page = page
            self._reset_angle()
            self._clear_crop(redraw=False)
        if job and self.tree.exists(f"j{job.id}"):
            self.tree.item(f"j{job.id}", open=True)
        if fit or (changed and job):
            self.fit_width(redraw=False)
        self._relayout()
        if scroll_to or changed:
            self._scroll_to_page(page)
        self._update_info()

    def _pending(self):
        try:
            return float(self.angle_var.get()) if self.page else 0.0
        except (tk.TclError, ValueError):
            return 0.0

    def _src(self, p):
        """Image to draw for page p, and how many full-res pixels one of its pixels covers."""
        img = p.rendered()
        ang = self._pending() if p is self.page else 0.0
        if not ang:
            return img, 1.0
        if not (self._proxy and self._proxy[0] == p.id and self._proxy[1] == p.ver):
            f = max(1, int(max(img.size) / 1600 + 0.999))
            small = img.reduce(f) if f > 1 else img
            if small.mode == "1":
                small = small.convert("L")
            self._proxy = (p.id, p.ver, f, small)
        _, _, f, small = self._proxy
        key = (p.id, p.ver, ang)
        if not (self._rot and self._rot[0] == key):
            fill = 255 if small.mode == "L" else (255, 255, 255)
            self._rot = (key, small.rotate(-ang, resample=Image.Resampling.BICUBIC, expand=True,
                                           fillcolor=fill))
        return self._rot[1], f

    def _page_dims(self, p):
        img, f = self._src(p)
        return img.width * f, img.height * f

    def _relayout(self):
        self.layout = []
        cw = max(self.canvas.winfo_width(), 50)
        if not self.job or not self.job.pages:
            self.canvas.configure(scrollregion=(0, 0, cw, max(self.canvas.winfo_height(), 50)))
            self._redraw()
            return
        dims = [self._page_dims(p) for p in self.job.pages]
        maxw = max(w for w, _ in dims) * self.zoom
        total_w = max(cw, maxw + 2 * PAD)
        y = PAD
        for p, (w, h) in zip(self.job.pages, dims):
            dw, dh = w * self.zoom, h * self.zoom
            x = (total_w - dw) / 2
            self.layout.append((p, x, y, dw, dh))
            y += dh + GAP
        self.canvas.configure(scrollregion=(0, 0, total_w, y - GAP + PAD))
        self.zoom_lbl.configure(text=f"{self.zoom * 100:.0f}%")
        self._redraw()

    def _schedule_redraw(self):
        if self._redraw_job is None:
            self._redraw_job = self.after_idle(self._redraw)

    def _redraw(self):
        self._redraw_job = None
        cv = self.canvas
        cv.delete("all")
        self._imgs = []
        vx0, vy0 = cv.canvasx(0), cv.canvasy(0)
        vx1, vy1 = vx0 + cv.winfo_width(), vy0 + cv.winfo_height()
        if not self.layout:
            cv.create_text((vx0 + vx1) / 2, (vy0 + vy1) / 2, fill="#bbb", font=("Segoe UI", 13),
                           text="Place your document and press Scan")
            return
        ang = self._pending()
        for p, x, y, w, h in self.layout:
            cv.create_rectangle(x + 4, y + 4, x + w + 4, y + h + 4, fill="#222", outline="")
            ix0, iy0, ix1, iy1 = max(x, vx0), max(y, vy0), min(x + w, vx1), min(y + h, vy1)
            if ix1 - ix0 >= 1 and iy1 - iy0 >= 1:
                img, f = self._src(p)
                k = self.zoom * f                     # screen px per source px
                box = ((ix0 - x) / k, (iy0 - y) / k, (ix1 - x) / k, (iy1 - y) / k)
                size = (max(1, round(ix1 - ix0)), max(1, round(iy1 - iy0)))
                src = img.convert("L") if img.mode == "1" else img
                resample = Image.Resampling.NEAREST if k >= 2 else Image.Resampling.BILINEAR
                part = src.resize(size, resample, box=box, reducing_gap=2.0 if k < 1 else None)
                ph = ImageTk.PhotoImage(part)
                self._imgs.append(ph)
                cv.create_image(ix0, iy0, image=ph, anchor="nw")
            if p is self.page:
                cv.create_rectangle(x - 2, y - 2, x + w + 2, y + h + 2, outline="#3b8eea", width=3)
                if ang:
                    step = 40
                    gx = x + step
                    while gx < x + w:
                        cv.create_line(gx, y, gx, y + h, fill="#00a0ff", dash=(2, 4))
                        gx += step
                    gy = y + step
                    while gy < y + h:
                        cv.create_line(x, gy, x + w, gy, fill="#00a0ff", dash=(2, 4))
                        gy += step
        if self.crop and self.crop[0] in [l[0] for l in self.layout]:
            p, l, t, r, b = self.crop
            _, x, y, w, h = next(item for item in self.layout if item[0] is p)
            z = self.zoom
            X0, Y0, X1, Y1 = x + l * z, y + t * z, x + r * z, y + b * z
            for rect in ((x, y, x + w, Y0), (x, Y1, x + w, y + h), (x, Y0, X0, Y1), (X1, Y0, x + w, Y1)):
                if rect[2] > rect[0] and rect[3] > rect[1]:
                    cv.create_rectangle(*rect, fill="#000", stipple="gray50", outline="")
            cv.create_rectangle(X0, Y0, X1, Y1, outline="#ffcc00", width=2, dash=(6, 3))

    # ---------------- scrolling & zoom ----------------
    def _yview(self, *args):
        self.canvas.yview(*args)
        self._schedule_redraw()

    def _xview(self, *args):
        self.canvas.xview(*args)
        self._schedule_redraw()

    def _scroll(self, delta, horizontal=False):
        step = int(-delta / 120 * 90) or (-1 if delta > 0 else 1)
        (self.canvas.xview_scroll if horizontal else self.canvas.yview_scroll)(step, "units")
        self._schedule_redraw()

    def _wheel(self, e):
        self._scroll(e.delta)

    def _wheel_h(self, e):
        self._scroll(e.delta, horizontal=True)

    def _wheel_zoom(self, e):
        self.zoom_by(1.2 if e.delta > 0 else 1 / 1.2, anchor=(e.x, e.y))

    def set_zoom(self, z, anchor=None):
        z = max(0.02, min(8.0, z))
        cv = self.canvas
        ax, ay = anchor or (cv.winfo_width() / 2, cv.winfo_height() / 2)
        sr = [float(v) for v in str(cv.cget("scrollregion")).split()] or [0, 0, 1, 1]
        old_w, old_h = max(sr[2], 1), max(sr[3], 1)
        fx, fy = (cv.canvasx(ax)) / old_w, (cv.canvasy(ay)) / old_h
        self.zoom = z
        self._relayout()
        sr = [float(v) for v in str(cv.cget("scrollregion")).split()]
        new_w, new_h = max(sr[2], 1), max(sr[3], 1)
        cv.xview_moveto(max(0, (fx * new_w - ax) / new_w))
        cv.yview_moveto(max(0, (fy * new_h - ay) / new_h))
        self._redraw()

    def zoom_by(self, f, anchor=None):
        self.set_zoom(self.zoom * f, anchor)

    def fit_width(self, redraw=True):
        if not self.job or not self.job.pages:
            return
        cw = max(self.canvas.winfo_width(), 200)
        maxw = max(self._page_dims(p)[0] for p in self.job.pages)
        self.zoom = max(0.02, (cw - 2 * PAD) / maxw)
        if redraw:
            self._relayout()
            self._scroll_to_page(self.page)

    def fit_page(self):
        if not self.page:
            return
        cw, ch = max(self.canvas.winfo_width(), 200), max(self.canvas.winfo_height(), 200)
        w, h = self._page_dims(self.page)
        self.zoom = max(0.02, min((cw - 2 * PAD) / w, (ch - 2 * PAD) / h))
        self._relayout()
        self._scroll_to_page(self.page)

    def _scroll_to_page(self, page):
        item = next((it for it in self.layout if it[0] is page), None)
        if not item:
            return
        sr = [float(v) for v in str(self.canvas.cget("scrollregion")).split()]
        self.canvas.yview_moveto(max(0, (item[2] - PAD / 2) / max(sr[3], 1)))
        self._redraw()

    # ---------------- mouse: select / pan / crop ----------------
    def _hit(self, cx, cy):
        for p, x, y, w, h in self.layout:
            if x <= cx <= x + w and y <= cy <= y + h:
                return p, x, y, w, h
        return None

    def _press(self, e):
        cv = self.canvas
        cv.focus_set()
        cx, cy = cv.canvasx(e.x), cv.canvasy(e.y)
        hit = self._hit(cx, cy)
        if hit and hit[0] is not self.page:
            self._select_page(hit[0])
            hit = self._hit(cx, cy)
        if self.crop_mode.get() and hit:
            self._drag = ("crop", hit, cx, cy)
        else:
            cv.scan_mark(e.x, e.y)
            self._drag = ("pan",)

    def _motion(self, e):
        if not self._drag:
            return
        cv = self.canvas
        if self._drag[0] == "pan":
            cv.scan_dragto(e.x, e.y, gain=1)
            self._schedule_redraw()
            return
        _, (p, x, y, w, h), sx, sy = self._drag
        cx = min(max(cv.canvasx(e.x), x), x + w)
        cy = min(max(cv.canvasy(e.y), y), y + h)
        z = self.zoom
        l, r = sorted(((sx - x) / z, (cx - x) / z))
        t, b = sorted(((sy - y) / z, (cy - y) / z))
        self.crop = (p, l, t, r, b)
        self.crop_apply_btn.configure(state="normal" if (r - l) > 4 and (b - t) > 4 else "disabled")
        self.crop_lbl.configure(text=f"Box: {int(r - l)} × {int(b - t)} px "
                                     f"({(r - l) / p.dpi * 2.54:.1f} × {(b - t) / p.dpi * 2.54:.1f} cm)")
        self._schedule_redraw()

    def _release(self, _e):
        self._drag = None

    def _select_page(self, p):
        if self.job and p in self.job.pages:
            self.page = p
            self._reset_angle()
            self._clear_crop(redraw=False)
            if self.tree.exists(f"p{p.id}"):
                self.tree.selection_set(f"p{p.id}")
                self.tree.see(f"p{p.id}")
            self._relayout()
            self._update_info()

    # ================================================================ editing
    def _targets(self):
        if not self.page:
            return []
        if self.all_pages_var.get() and self.job:
            return list(self.job.pages)
        return [self.page]

    def _after_edit(self, msg=""):
        self._proxy = None
        self._refresh_tree()
        if self.page and self.tree.exists(f"p{self.page.id}"):
            self.tree.selection_set(f"p{self.page.id}")
        self._relayout()
        self._update_info()
        if msg:
            self._set_status(msg)

    def _busy_apply(self, fn):
        self.config(cursor="watch")
        self.update_idletasks()
        try:
            fn()
        finally:
            self.config(cursor="")

    def rotate(self, deg):
        ts = self._targets()
        if not ts:
            return
        self._commit_angle()

        def go():
            for p in ts:
                p.push("rotate", deg)
                p.rendered()
        self._busy_apply(go)
        self._after_edit(f"Rotated {len(ts)} page(s).")

    def _reset_angle(self):
        self.angle_var.set(0.0)

    def _angle_changed(self):
        if self.crop:
            self._clear_crop(redraw=False)
        self._relayout()

    def _commit_angle(self):
        ang = self._pending()
        if not ang or not self.page:
            return False
        ts = self._targets()

        def go():
            for p in ts:
                p.push("rotate", -ang)
                p.rendered()
        self._busy_apply(go)
        self._reset_angle()
        return True

    def apply_angle(self):
        if self._commit_angle():
            self._after_edit("Angle applied.")
        else:
            self._set_status("Move the angle slider first.")

    def _crop_toggled(self):
        if self.crop_mode.get():
            if self._commit_angle():
                self._after_edit()
            self.canvas.configure(cursor="crosshair")
            self._set_status("Crop: drag a box over the page, then press Apply crop. Esc clears it.")
        else:
            self.canvas.configure(cursor="")
            self._clear_crop()

    def _clear_crop(self, redraw=True):
        self.crop = None
        self.crop_apply_btn.configure(state="disabled")
        self.crop_lbl.configure(text="")
        if redraw:
            self._redraw()

    def apply_crop(self):
        if not self.crop:
            return
        p, l, t, r, b = self.crop
        self._busy_apply(lambda: (p.push("crop", (round(l), round(t), round(r), round(b))), p.rendered()))
        self._clear_crop(redraw=False)
        self._after_edit("Cropped.")

    def _scale_val(self):
        try:
            v = float(self.scale_var.get())
            return v if 1 <= v <= 1000 else None
        except ValueError:
            return None

    def _scale_hint(self):
        v = self._scale_val()
        if not self.page or v is None:
            self.scale_lbl.configure(text="")
            return
        w, h = self.page.size
        nw, nh = round(w * v / 100), round(h * v / 100)
        cw, ch = nw / self.page.dpi * 2.54, nh / self.page.dpi * 2.54
        self.scale_lbl.configure(text=f"→ {nw} × {nh} px, prints at {cw:.1f} × {ch:.1f} cm")

    def apply_scale(self):
        v = self._scale_val()
        ts = self._targets()
        if v is None or not ts:
            self._set_status("Enter a size between 10 and 400 %.")
            return
        if v == 100:
            return
        self._commit_angle()

        def go():
            for p in ts:
                p.push("scale", v)
                p.rendered()
        self._busy_apply(go)
        self.scale_var.set("100")
        self._after_edit(f"Resized {len(ts)} page(s) to {v:g}%.")

    def move_page(self, d):
        if not self.page or not self.job:
            return
        ps = self.job.pages
        i = ps.index(self.page)
        if 0 <= i + d < len(ps):
            ps[i], ps[i + d] = ps[i + d], ps[i]
            self._after_edit()
            self._scroll_to_page(self.page)

    def undo(self):
        if self.page and self.page.undo():
            self._busy_apply(self.page.rendered)
            self._after_edit("Undone.")

    def reset_page(self):
        if self.page and self.page.ops and messagebox.askyesno(APP_NAME, "Undo all edits on this page?"):
            self.page.reset()
            self._after_edit("Page reset to the original scan.")

    def _update_info(self):
        p = self.page
        if not p or not self.job:
            self.info_lbl.configure(text="No page selected")
            self.scale_lbl.configure(text="")
            return
        w, h = p.size
        cw, ch = p.print_size_cm()
        n = self.job.pages.index(p) + 1
        extra = "\n(enhanced above optical DPI)" if p.enhanced else ""
        edits = f"\nEdits: {len(p.ops)}" if p.ops else ""
        self.info_lbl.configure(text=f"{self.job.name}\nPage {n} of {len(self.job.pages)}\n"
                                     f"{w} × {h} px · {p.dpi} DPI\n{cw:.1f} × {ch:.1f} cm{extra}{edits}")
        self._scale_hint()


def _selftest():
    """Used by the build pipeline: open the window, check bundled parts, write a report, exit."""
    import sys
    import tempfile
    report = [f"version={__version__}"]
    try:
        import win32com.client  # noqa: F401
        report.append("wia=ok")
    except Exception as e:  # noqa: BLE001
        report.append(f"wia=missing ({e})")
    report.append("icon=" + ("ok" if os.path.exists(resource("assets/icon.ico")) else "missing"))
    app = App()
    app.check_updates = lambda quiet=False: None

    def finish():
        report.append("window=ok")
        with open(os.path.join(tempfile.gettempdir(), "mcs_selftest.txt"), "w") as f:
            f.write("\n".join(report))
        app.destroy()
    app.after(2500, finish)
    app.mainloop()
    sys.exit(0)


if __name__ == "__main__":
    import sys
    if "--selftest" in sys.argv:
        _selftest()
    App().mainloop()
