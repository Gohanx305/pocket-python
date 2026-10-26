"""PyMuPDF-compatible layer over MuPDF WebAssembly.

Covers the common PyMuPDF API: open/new documents, pages, text, search,
rendering to images, merging, deleting/reordering pages, inserting text
and images, metadata, table of contents, saving.
"""
import os
from builtins import open as _open
import mupdf as _m
from pyodide.ffi import to_js

VersionBind = "1.3.0 (mupdf.js)"
__version__ = VersionBind

_MAGIC = {
    ".pdf": "application/pdf", ".xps": "application/oxps", ".epub": "application/epub+zip",
    ".cbz": "application/x-cbz", ".fb2": "text/xml", ".png": "image/png", ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg", ".gif": "image/gif", ".bmp": "image/bmp", ".tif": "image/tiff",
    ".tiff": "image/tiff", ".svg": "image/svg+xml", ".webp": "image/webp",
}


def _u8(data):
    return to_js(memoryview(bytes(data)))


def _py_bytes(js_u8):
    return bytes(js_u8.to_py())


def _rect_list(r):
    return [float(v) for v in r.to_py()] if hasattr(r, "to_py") else [float(v) for v in r]


class Point:
    def __init__(self, x=0, y=0):
        if isinstance(x, (tuple, list, Point)):
            x, y = tuple(x)
        self.x, self.y = float(x), float(y)

    def __iter__(self):
        return iter((self.x, self.y))

    def __repr__(self):
        return f"Point({self.x}, {self.y})"


class Rect:
    def __init__(self, *a):
        if len(a) == 1:
            a = tuple(a[0])
        if len(a) == 2:
            a = (*a[0], *a[1])
        self.x0, self.y0, self.x1, self.y1 = (float(v) for v in (a or (0, 0, 0, 0)))

    width = property(lambda s: s.x1 - s.x0)
    height = property(lambda s: s.y1 - s.y0)
    tl = property(lambda s: Point(s.x0, s.y0))
    br = property(lambda s: Point(s.x1, s.y1))

    def __iter__(self):
        return iter((self.x0, self.y0, self.x1, self.y1))

    def __getitem__(self, i):
        return tuple(self)[i]

    def __repr__(self):
        return f"Rect({self.x0}, {self.y0}, {self.x1}, {self.y1})"


class Matrix:
    def __init__(self, *a):
        if len(a) == 1 and not isinstance(a[0], (int, float)):
            a = tuple(a[0])
        if len(a) == 1:
            a = (a[0], a[0])
        if len(a) == 2:
            a = (a[0], 0, 0, a[1], 0, 0)
        self.a, self.b, self.c, self.d, self.e, self.f = (float(v) for v in (a or (1, 0, 0, 1, 0, 0)))

    def __iter__(self):
        return iter((self.a, self.b, self.c, self.d, self.e, self.f))

    def __repr__(self):
        return f"Matrix{tuple(self)}"


Identity = Matrix(1, 1)


class Pixmap:
    def __init__(self, js_pixmap):
        self._p = js_pixmap

    width = w = property(lambda s: int(s._p.getWidth()))
    height = h = property(lambda s: int(s._p.getHeight()))
    n = property(lambda s: int(s._p.getNumberOfComponents()))

    @property
    def samples(self):
        return _py_bytes(self._p.getPixels())

    def tobytes(self, output="png", jpg_quality=90):
        output = output.lower().lstrip(".")
        if output in ("jpg", "jpeg"):
            return _py_bytes(self._p.asJPEG(jpg_quality, False))
        if output == "pam":
            return _py_bytes(self._p.asPAM())
        return _py_bytes(self._p.asPNG())

    def save(self, filename, output=None, jpg_quality=90):
        ext = output or os.path.splitext(str(filename))[1] or "png"
        with _open(filename, "wb") as f:
            f.write(self.tobytes(ext, jpg_quality))

    pil_save = save

    def __repr__(self):
        return f"Pixmap({self.width}x{self.height})"


def _pdf_str(text):
    out = []
    for ch in text:
        code = ord(ch)
        if ch in "()\\":
            out.append("\\" + ch)
        elif 32 <= code < 127:
            out.append(ch)
        elif code < 256:
            out.append("\\%03o" % code)
        else:
            out.append("?")
    return "".join(out)


class Page:
    def __init__(self, doc, number):
        self.parent = doc
        self.number = number
        self._p = doc._d.loadPage(number)

    @property
    def rect(self):
        return Rect(_rect_list(self._p.getBounds()))

    mediabox = cropbox = rect

    def get_text(self, option="text", sort=False):
        st = self._p.toStructuredText("preserve-whitespace")
        if option == "json":
            return st.asJSON()
        if option in ("blocks", "words", "dict"):
            import json
            data = json.loads(st.asJSON())
            if option == "dict":
                return data
            blocks = []
            for i, b in enumerate(data.get("blocks", [])):
                lines = b.get("lines", [])
                txt = "\n".join(l.get("text", "") for l in lines)
                bb = b.get("bbox", {})
                r = (bb.get("x", 0), bb.get("y", 0), bb.get("x", 0) + bb.get("w", 0), bb.get("y", 0) + bb.get("h", 0))
                if option == "blocks":
                    blocks.append((*r, txt, i, 0))
                else:
                    for w_i, w in enumerate(txt.split()):
                        blocks.append((*r, w, i, 0, w_i))
            return blocks
        if option == "html":
            return st.asHTML(self.number)
        return st.asText()

    getText = get_text

    def search_for(self, needle, quads=False):
        hits = []
        for hit in self._p.search(needle).to_py():
            for q in hit:
                xs, ys = q[0::2], q[1::2]
                hits.append(Rect(min(xs), min(ys), max(xs), max(ys)))
        return hits

    def get_pixmap(self, matrix=None, dpi=None, alpha=False, colorspace=None):
        if dpi:
            m = [dpi / 72, 0, 0, dpi / 72, 0, 0]
        elif matrix is not None:
            m = list(Matrix(matrix))
        else:
            m = [1, 0, 0, 1, 0, 0]
        cs = _m.ColorSpace.DeviceGray if str(colorspace).lower() in ("gray", "csgray") else _m.ColorSpace.DeviceRGB
        return Pixmap(self._p.toPixmap(to_js(m), cs, bool(alpha), True))

    getPixmap = get_pixmap

    def get_links(self):
        out = []
        for l in self._p.getLinks():
            out.append({"from": Rect(_rect_list(l.getBounds())), "uri": l.getURI()})
        return out

    # --- writing (PDF only) ---
    def _pdf(self):
        self.parent._need_pdf()
        return self.parent._d, self.parent._d.findPage(self.number)

    def _add_content(self, ops, res_kind, res_name, res_ref):
        d, pobj = self._pdf()
        res = pobj.getInheritable("Resources")
        if res.isNull():
            res = d.newDictionary()
            pobj.put("Resources", res)
        sub = res.get(res_kind)
        if sub.isNull():
            sub = d.newDictionary()
            res.put(res_kind, sub)
        sub.put(res_name, res_ref)
        new = d.addStream(_u8(ops.encode("latin-1")), d.newDictionary())
        old = pobj.get("Contents")
        arr = d.newArray()
        arr.push(d.addStream(_u8(b"q\n"), d.newDictionary()))
        if old.isArray():
            for i in range(int(old.length)):
                arr.push(old.get(i))
        elif not old.isNull():
            arr.push(old)
        arr.push(d.addStream(_u8(b"\nQ\n"), d.newDictionary()))
        arr.push(new)
        pobj.put("Contents", arr)
        self._p = d.loadPage(self.number)

    def insert_text(self, point, text, fontsize=11, fontname="helv", color=(0, 0, 0), lineheight=1.2):
        d, _ = self._pdf()
        base = {"helv": "Helvetica", "tiro": "Times-Roman", "cour": "Courier", "hebo": "Helvetica-Bold"}.get(fontname.lower(), "Helvetica")
        font = d.addSimpleFont(_m.Font.new(base), "Latin")
        name = "F" + base.replace("-", "")
        x, y = Point(point)
        h = self.rect.height
        r, g, b = color
        lines = str(text).split("\n")
        ops = [f"BT /{name} {fontsize} Tf {r} {g} {b} rg {fontsize * lineheight} TL {x} {h - y} Td"]
        for i, line in enumerate(lines):
            ops.append(("T* " if i else "") + f"({_pdf_str(line)}) Tj")
        ops.append("ET")
        self._add_content(" ".join(ops), "Font", name, font)
        return len(lines)

    def insert_image(self, rect, filename=None, stream=None, keep_proportion=True):
        d, _ = self._pdf()
        data = stream if stream is not None else _open(filename, "rb").read()
        img = _m.Image.new(_u8(data))
        ref = d.addImage(img)
        iw, ih = int(img.getWidth()), int(img.getHeight())
        r = Rect(rect)
        w, h = r.width, r.height
        if keep_proportion and iw and ih:
            s = min(w / iw, h / ih)
            w, h = iw * s, ih * s
        x = r.x0 + (r.width - w) / 2
        y = self.rect.height - r.y0 - (r.height - h) / 2 - h
        name = f"Im{self.parent._counter()}"
        self._add_content(f"q {w} 0 0 {h} {x} {y} cm /{name} Do Q", "XObject", name, ref)

    def __repr__(self):
        return f"page {self.number} of {self.parent.name}"


class Document:
    def __init__(self, filename=None, stream=None, filetype=None):
        self.name = str(filename or "")
        self._n = 0
        if filename is None and stream is None:
            self._d = _m.PDFDocument.new()
            return
        if stream is None:
            with _open(filename, "rb") as f:
                stream = f.read()
        ext = (filetype if filetype and filetype.startswith(".") else "." + filetype) if filetype else os.path.splitext(self.name)[1]
        magic = _MAGIC.get(ext.lower(), filetype or "application/pdf")
        self._d = _m.Document.openDocument(_u8(stream), magic)

    def _counter(self):
        self._n += 1
        return self._n

    @property
    def is_pdf(self):
        return bool(self._d.isPDF())

    def _need_pdf(self):
        if not self.is_pdf:
            raise ValueError("هذه العملية تعمل على ملفات PDF فقط")

    @property
    def page_count(self):
        return int(self._d.countPages())

    def __len__(self):
        return self.page_count

    def load_page(self, n=0):
        if n < 0:
            n += self.page_count
        if not 0 <= n < self.page_count:
            raise IndexError("page not in document")
        return Page(self, n)

    loadPage = load_page

    def __getitem__(self, n):
        return self.load_page(n)

    def __iter__(self):
        for i in range(self.page_count):
            yield Page(self, i)

    def pages(self, start=0, stop=None, step=1):
        for i in range(start, self.page_count if stop is None else stop, step):
            yield Page(self, i)

    @property
    def needs_pass(self):
        return bool(self._d.needsPassword())

    def authenticate(self, password):
        return int(self._d.authenticatePassword(password))

    @property
    def metadata(self):
        keys = {"format": "format", "title": "info:Title", "author": "info:Author", "subject": "info:Subject",
                "keywords": "info:Keywords", "creator": "info:Creator", "producer": "info:Producer",
                "creationDate": "info:CreationDate", "modDate": "info:ModDate", "encryption": "encryption"}
        out = {}
        for k, v in keys.items():
            val = self._d.getMetaData(v)
            out[k] = "" if val is None else str(val)
        return out

    def set_metadata(self, meta):
        for k, v in meta.items():
            key = "info:" + k[0].upper() + k[1:] if not k.startswith("info:") else k
            self._d.setMetaData(key, str(v))

    def get_toc(self, simple=True):
        toc = []

        def walk(items, level):
            for it in items or []:
                it = dict(it.to_py()) if hasattr(it, "to_py") else it
                toc.append([level, it.get("title") or "", int(it.get("page", -1)) + 1])
                walk(it.get("down"), level + 1)
        outline = self._d.loadOutline()
        walk(outline.to_py() if outline else [], 1)
        return toc

    def new_page(self, pno=-1, width=595, height=842):
        self._need_pdf()
        d = self._d
        ref = d.addPage(to_js([0, 0, width, height]), 0, d.newDictionary(), _u8(b""))
        at = self.page_count if pno < 0 else pno
        d.insertPage(at, ref)
        return Page(self, at)

    newPage = new_page

    def insert_pdf(self, src, from_page=0, to_page=-1, start_at=-1):
        self._need_pdf()
        src._need_pdf()
        last = src.page_count - 1 if to_page < 0 else to_page
        at = self.page_count if start_at < 0 else start_at
        gm = self._d.newGraftMap()
        step = 1 if last >= from_page else -1
        for i in range(from_page, last + step, step):
            gm.graftPage(at, src._d, i)
            at += 1

    insertPDF = insert_pdf

    def delete_page(self, pno=-1):
        self._need_pdf()
        self._d.deletePage(pno if pno >= 0 else self.page_count + pno)

    def delete_pages(self, *args):
        nums = sorted(set(args[0] if len(args) == 1 and not isinstance(args[0], int) else args), reverse=True)
        for n in nums:
            self.delete_page(n)

    def select(self, pages):
        self._need_pdf()
        self._d.rearrangePages(to_js(list(pages)))

    def move_page(self, pno, to=-1):
        order = list(range(self.page_count))
        order.pop(pno)
        order.insert(len(order) if to < 0 else to, pno)
        self.select(order)

    def tobytes(self, garbage=0, deflate=False, **kw):
        self._need_pdf()
        opts = []
        if garbage:
            opts.append("garbage=%d" % min(int(garbage), 4))
        if deflate or kw.get("deflate_images") or kw.get("compress"):
            opts.append("compress")
        return _py_bytes(self._d.saveToBuffer(",".join(opts)).asUint8Array())

    write = tobytes

    def save(self, filename, garbage=0, deflate=False, **kw):
        data = self.tobytes(garbage=garbage, deflate=deflate, **kw)
        with _open(filename, "wb") as f:
            f.write(data)

    def ez_save(self, filename, **kw):
        self.save(filename, garbage=3, deflate=True)

    def convert_to_pdf(self):
        """Renders each page into a new PDF (for images, EPUB, XPS...)."""
        out = Document()
        for page in self:
            r = page.rect
            png = page.get_pixmap(dpi=150).tobytes("png")
            p = out.new_page(width=r.width, height=r.height)
            p.insert_image(r, stream=png)
        return out.tobytes()

    def close(self):
        self._d = None

    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()

    def __repr__(self):
        return f"Document('{self.name}')"


open = Document  # noqa: A001
