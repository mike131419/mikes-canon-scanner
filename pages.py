"""
Scan jobs and pages for Mike's Canon Scanner.

Every page keeps its original scan untouched plus a list of edits
(rotate / crop / resize). The edited image is rebuilt from the original
each time, so repeated edits never degrade quality and Undo / Reset are exact.
"""
import itertools
import math
import os
import re
from pathlib import Path

from PIL import Image

_ids = itertools.count(1)
WHITE = {"1": 1, "L": 255, "RGB": (255, 255, 255)}


def _norm(img):
    if img.mode not in ("1", "L", "RGB"):
        img = img.convert("RGB")
    return img


def apply_op(img, op, val):
    if op == "rotate":            # val: degrees, counter-clockwise (PIL convention)
        val = val % 360
        if val == 0:
            return img
        if val in (90, 180, 270):
            return img.transpose({90: Image.Transpose.ROTATE_90,
                                  180: Image.Transpose.ROTATE_180,
                                  270: Image.Transpose.ROTATE_270}[val])
        src = img.convert("L") if img.mode == "1" else img
        out = src.rotate(val, resample=Image.Resampling.BICUBIC, expand=True,
                         fillcolor=WHITE[src.mode])
        return out.point(lambda v: 255 if v > 127 else 0, "1") if img.mode == "1" else out
    if op == "crop":              # val: (left, top, right, bottom) in pixels
        l, t, r, b = val
        l, t = max(0, int(l)), max(0, int(t))
        r, b = min(img.width, int(r)), min(img.height, int(b))
        if r - l < 2 or b - t < 2:
            return img
        return img.crop((l, t, r, b))
    if op == "scale":             # val: percent
        w = max(1, round(img.width * val / 100))
        h = max(1, round(img.height * val / 100))
        if img.mode == "1":
            return img.convert("L").resize((w, h), Image.Resampling.LANCZOS) \
                      .point(lambda v: 255 if v > 127 else 0, "1")
        return img.resize((w, h), Image.Resampling.LANCZOS)
    raise ValueError(op)


class Page:
    def __init__(self, img, dpi, enhanced=False):
        self.id = next(_ids)
        self.orig = _norm(img)
        self.dpi = dpi
        self.enhanced = enhanced      # True if upscaled above the scanner's optical DPI
        self.ops = []
        self.ver = 0
        self._r, self._rv = None, -1

    def rendered(self):
        if self._rv != self.ver:
            im = self.orig
            for op, v in self.ops:
                im = apply_op(im, op, v)
            self._r, self._rv = im, self.ver
        return self._r

    @property
    def size(self):
        return self.rendered().size

    def push(self, op, val):
        if op == "rotate" and self.ops and self.ops[-1][0] == "rotate":
            total = (self.ops[-1][1] + val) % 360
            total = round(total, 3)
            if total in (0, 360):
                self.ops.pop()
            else:
                self.ops[-1] = ("rotate", total)
        elif op == "scale" and self.ops and self.ops[-1][0] == "scale":
            self.ops[-1] = ("scale", self.ops[-1][1] * val / 100)
        else:
            if op == "rotate" and round(val % 360, 3) == 0:
                return
            if op == "scale" and val == 100:
                return
            self.ops.append((op, val))
        self.ver += 1

    def undo(self):
        if self.ops:
            self.ops.pop()
            self.ver += 1
            return True
        return False

    def reset(self):
        if self.ops:
            self.ops.clear()
            self.ver += 1

    def print_size_cm(self):
        w, h = self.size
        return w / self.dpi * 2.54, h / self.dpi * 2.54


class Job:
    def __init__(self, name, pages=None):
        self.id = next(_ids)
        self.name = name
        self.pages = list(pages or [])


def merge_jobs(jobs, name=None):
    pages = [p for j in jobs for p in j.pages]
    return Job(name or " + ".join(j.name for j in jobs), pages)


def safe_name(s):
    s = re.sub(r'[\\/:*?"<>|]+', "-", s).strip(" .")
    return s or "Scan"


def save_job(job, fmt, path_or_folder, quality=92):
    """fmt: 'PDF' -> path is a file; 'JPG'/'PNG' -> path is a folder.
    Returns list of written paths."""
    imgs = [p.rendered() for p in job.pages]
    if not imgs:
        return []
    if fmt == "PDF":
        dpi = job.pages[0].dpi
        out = []
        for p, im in zip(job.pages, imgs):
            if p.dpi != dpi:   # merged jobs with different DPI: keep printed size the same
                f = dpi / p.dpi
                im = im.resize((max(1, round(im.width * f)), max(1, round(im.height * f))),
                               Image.Resampling.LANCZOS)
            out.append(im.convert("RGB") if im.mode == "1" else im)
        path = str(path_or_folder)
        out[0].save(path, "PDF", save_all=True, append_images=out[1:], resolution=dpi)
        return [path]
    folder = Path(path_or_folder)
    folder.mkdir(parents=True, exist_ok=True)
    ext = ".jpg" if fmt == "JPG" else ".png"
    base = safe_name(job.name)
    written = []
    for i, (p, im) in enumerate(zip(job.pages, imgs), 1):
        name = folder / (base + ("" if len(imgs) == 1 else f"_p{i}") + ext)
        n = 2
        while name.exists():
            name = folder / (base + ("" if len(imgs) == 1 else f"_p{i}") + f" ({n}){ext}")
            n += 1
        if fmt == "JPG":
            (im.convert("L") if im.mode == "1" else im).save(name, "JPEG", quality=quality,
                                                             dpi=(p.dpi, p.dpi))
        else:
            im.save(name, "PNG", dpi=(p.dpi, p.dpi))
        written.append(str(name))
    return written


def upscale(img, factor):
    """Software enlargement for DPI above the scanner's optical limit."""
    w, h = round(img.width * factor), round(img.height * factor)
    if img.mode == "1":
        return img.convert("L").resize((w, h), Image.Resampling.LANCZOS) \
                  .point(lambda v: 255 if v > 127 else 0, "1")
    return img.resize((w, h), Image.Resampling.LANCZOS)


def rotated_size(w, h, deg):
    r = math.radians(deg)
    c, s = abs(math.cos(r)), abs(math.sin(r))
    return round(w * c + h * s), round(w * s + h * c)


__all__ = ["Page", "Job", "merge_jobs", "save_job", "upscale", "apply_op",
           "rotated_size", "safe_name", "os"]
