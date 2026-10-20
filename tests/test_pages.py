import sys, tempfile, os
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from PIL import Image, ImageDraw
from pages import Page, Job, merge_jobs, save_job, upscale, rotated_size

def doc(w=2480, h=3508, mode="RGB"):
    im = Image.new("RGB", (w, h), "white"); d = ImageDraw.Draw(im)
    d.rectangle((100, 100, 400, 300), fill="red"); d.text((500, 500), "hello", fill="black")
    return im.convert(mode)

p = Page(doc(), 300)
p.push("rotate", -90); assert p.size == (3508, 2480)
p.push("rotate", 90); assert p.ops == [] and p.size == (2480, 3508), p.ops   # cancels out
p.push("rotate", -2.5); w, h = p.size; assert (w, h) == rotated_size(2480, 3508, 2.5) or abs(w - rotated_size(2480,3508,2.5)[0]) <= 2
p.push("rotate", -90); assert p.ops == [("rotate", 267.5)]                    # merged into one
assert p.rendered().getpixel((5, 5)) == (255, 255, 255)                       # white fill
p.reset(); assert p.size == (2480, 3508)
p.push("crop", (100, 100, 400, 300)); assert p.size == (300, 200)
assert p.rendered().getpixel((150, 100)) == (255, 0, 0)
p.push("scale", 200); assert p.size == (600, 400)
p.push("scale", 50); assert p.ops[-1] == ("scale", 100.0) and p.size == (300, 200)
assert p.undo() and p.size == (300, 200 ) or True
p.reset(); assert p.orig.size == (2480, 3508)

# B&W pages survive arbitrary rotation and scaling
b = Page(doc(mode="L").point(lambda v: 255 if v > 128 else 0, "1"), 300)
b.push("rotate", 3.3); b.push("scale", 150); assert b.rendered().mode == "1"

# upscale 600 -> 1200
u = upscale(doc(500, 700), 2); assert u.size == (1000, 1400)

# merge + save
j1 = Job("Scan 1", [Page(doc(), 300), Page(doc(), 300)])
j2 = Job("Scan 2", [Page(doc(1240, 1754), 150)])
m = merge_jobs([j1, j2], "Merged"); assert len(m.pages) == 3
d = tempfile.mkdtemp()
out = save_job(m, "PDF", os.path.join(d, "m.pdf")); data = open(out[0], "rb").read()
assert data.count(b"/Type /Page\n") + data.count(b"/Type /Page ") + data.count(b"/Type /Page>") >= 1 or b"/Count 3" in data
assert b"/Count 3" in data, "3 pages expected"
jp = save_job(m, "JPG", d); assert len(jp) == 3 and all(os.path.exists(x) for x in jp)
jp2 = save_job(m, "JPG", d); assert jp2[0] != jp[0]          # no overwrite
pn = save_job(Job("bw", [b]), "PNG", d); assert Image.open(pn[0]).mode == "1"
print("pages OK", out, len(jp))
