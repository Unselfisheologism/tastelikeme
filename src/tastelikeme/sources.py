"""Pluggable intake sources. A source is a function (target) -> iterable of item dicts:
  {kind: image|text|link, ref: path-or-url, title?, text?, origin: str}
Register new ones in SOURCES."""
import json, re, urllib.request, zipfile
from html.parser import HTMLParser
from pathlib import Path

IMG = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".avif", ".heic"}
TXT = {".txt", ".md", ".markdown"}
UA = {"User-Agent": "tastelikeme/0.1 (+local cli)"}

def _get(url, tries=3):
    import time
    for n in range(tries):
        try:
            req = urllib.request.Request(url, headers=UA)
            with urllib.request.urlopen(req, timeout=30) as r:
                return r.read().decode("utf-8", "replace")
        except Exception:
            if n == tries - 1: raise
            time.sleep(1.5 * (n + 1))

def folder(target):
    root = Path(target).expanduser()
    if not root.is_dir():
        raise SystemExit(f"not a folder: {root}")
    for p in sorted(root.rglob("*")):
        if not p.is_file() or p.name.startswith("."):
            continue
        ext = p.suffix.lower()
        if ext in IMG:
            yield {"kind": "image", "ref": str(p.resolve()), "title": p.stem, "origin": f"folder:{root}"}
        elif ext in TXT:
            yield {"kind": "text", "ref": str(p.resolve()), "title": p.stem,
                   "text": p.read_text(encoding="utf-8", errors="replace")[:4000], "origin": f"folder:{root}"}

def arena(target):
    """are.na public channel: slug or https://www.are.na/<user>/<slug>. No auth."""
    slug = target.rstrip("/").split("/")[-1]
    page = 1
    limit = int(__import__("os").environ.get("TASTELIKEME_ARENA_MAX", "200"))
    if limit < 0: raise ValueError("TASTELIKEME_ARENA_MAX must not be negative")
    scanned = 0
    while scanned < limit:
        data = json.loads(_get(f"https://api.are.na/v2/channels/{slug}/contents?per=25&page={page}"))
        blocks = data.get("contents", [])
        for b in blocks:
            if scanned >= limit: return
            scanned += 1
            cls = b.get("class")
            if cls == "Image":
                img = (b.get("image") or {}).get("original", {}).get("url") or (b.get("image") or {}).get("display", {}).get("url")
                if img:
                    yield {"kind": "image", "ref": img, "title": b.get("title") or "", "text": b.get("description") or "",
                           "origin": f"arena:{slug}"}
            elif cls == "Text":
                yield {"kind": "text", "ref": f"arena-block:{b.get('id')}", "title": b.get("title") or "",
                       "text": b.get("content") or "", "origin": f"arena:{slug}"}
            elif cls in ("Link", "Media", "Attachment"):
                src = (b.get("source") or {}).get("url") or (b.get("attachment") or {}).get("url")
                img = (b.get("image") or {}).get("original", {}).get("url")
                if img:
                    yield {"kind": "image", "ref": img, "title": b.get("title") or "", "text": b.get("description") or "",
                           "origin": f"arena:{slug}"}
                elif src:
                    yield {"kind": "link", "ref": src, "title": b.get("title") or "", "text": b.get("description") or "",
                           "origin": f"arena:{slug}"}
        if len(blocks) < 25:
            break
        page += 1

def pinterest(target):
    """Best-effort: public board page, pulls pin image URLs from the HTML. Pinterest
    may change markup or throttle; are.na or folder intake is the reliable path."""
    html = _get(target)
    seen = set()
    for m in re.finditer(r"https://i\.pinimg\.com/(?:originals|736x|564x)/[\w/.\-]+\.(?:jpg|jpeg|png|webp)", html):
        url = m.group(0).replace("/564x/", "/736x/")
        key = url.rsplit("/", 1)[-1]
        if key in seen: continue
        seen.add(key)
        yield {"kind": "image", "ref": url, "title": "", "origin": f"pinterest:{target}"}

def x_export(target):
    """X (Twitter) data archive zip: reads data/like.js and data/bookmark.js if present."""
    with zipfile.ZipFile(target) as z:
        for name in z.namelist():
            base = name.rsplit("/", 1)[-1]
            if base not in ("like.js", "bookmark.js"):
                continue
            raw = z.read(name).decode("utf-8", "replace")
            arr = json.loads(raw[raw.index("["):])
            for row in arr:
                obj = row.get("like") or row.get("bookmark") or row
                tid = obj.get("tweetId", "")
                yield {"kind": "text", "ref": obj.get("expandedUrl") or f"https://x.com/i/status/{tid}",
                       "title": f"x:{tid}", "text": obj.get("fullText", ""), "origin": f"x-export:{base}"}

class _ExportTokens(HTMLParser):
    """Flat token stream for the loose HTML in a Pinterest account export."""
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.tokens = []
        self._h1 = None
    def handle_starttag(self, tag, attrs):
        if tag == "h1":
            self._h1 = []
        elif tag == "a":
            self.tokens.append(("a", (dict(attrs).get("href") or "").strip()))
    def handle_endtag(self, tag):
        if tag == "h1" and self._h1 is not None:
            self.tokens.append(("h1", "".join(self._h1).strip()))
            self._h1 = None
    def handle_data(self, data):
        if self._h1 is not None:
            self._h1.append(data)
        elif data.strip():
            self.tokens.append(("text", data))

_PIN_FIELDS = {"title": "title", "details": "details", "image": "image", "board name": "board",
               "board id": "board_id", "alt text": "alt", "canonical link": "canonical",
               "created at": "created", "alive": "alive", "is a repin": "repin",
               "private": "private", "is video": "is_video", "video": "video"}

def _clean_field(v):
    v = v.strip().rstrip(",").strip()
    low = v.lower()
    return "" if low in ("", "no data") or low.startswith("none") else v

def _pins_from_html(html):
    """Pin records from the export's 'Pins' section, or None when it has none."""
    parser = _ExportTokens()
    parser.feed(html)
    parser.close()
    toks = parser.tokens
    start = next((i for i, t in enumerate(toks) if t[0] == "h1" and t[1].lower() == "pins"), None)
    if start is None:
        return None
    pins, cur, want_link = [], None, False
    for t in toks[start + 1:]:
        if t[0] == "h1":
            break
        if t[0] == "a" and t[1].startswith("https://www.pinterest.com/pin/"):
            if cur:
                pins.append(cur)
            cur, want_link = {"link": t[1]}, False
        elif cur is not None and t[0] == "a":
            if want_link and t[1]:
                cur["canonical"] = t[1]
            want_link = False
        elif cur is not None and t[0] == "text":
            key, sep, val = t[1].partition(": ")
            if not sep:
                continue
            field = _PIN_FIELDS.get(key.strip().rstrip(":").strip().lower())
            if field is None:
                continue
            val = _clean_field(val)
            if field == "canonical" and not val:
                want_link = True  # the URL is in the anchor that follows
            elif val:
                cur[field] = val
    if cur:
        pins.append(cur)
    return pins

def _export_docs(target):
    p = Path(target).expanduser()
    if not p.exists():
        raise ValueError(f"no such file: {p}")
    if zipfile.is_zipfile(p):
        with zipfile.ZipFile(p) as z:
            docs = [z.read(n).decode("utf-8", "replace") for n in z.namelist()
                    if n.lower().endswith((".html", ".htm"))]
        if not docs:
            raise ValueError(f"no .html files in {p}")
        return docs
    if p.suffix.lower() in (".html", ".htm"):
        return [p.read_text(encoding="utf-8", errors="replace")]
    raise ValueError(f"not a Pinterest export zip or extracted .html file: {p}")

def pinterest_export(target):
    """Pinterest account data export: the zip Pinterest emails after Settings ->
    Privacy and data -> Request your data, or the Pins .html file extracted from it.
    Yields every pin on every board (secret boards included) as a 736x image URL
    rebuilt from the export's image id, or as a link item when the pin has no usable
    image. No image files ship in the export, so URLs die with their pins; pins
    already dead at export time are skipped."""
    docs = _export_docs(target)
    merged, section = {}, False
    for doc in docs:
        pins = _pins_from_html(doc)
        if pins is None:
            continue
        section = True
        for pin in pins:
            if pin.get("alive", "").lower() == "false":
                continue
            image = pin.get("image", "").lower()
            if re.fullmatch(r"[0-9a-f]{32}", image):
                kind, ref = "image", f"https://i.pinimg.com/736x/{image[0:2]}/{image[2:4]}/{image[4:6]}/{image}.jpg"
            else:
                kind, ref = "link", pin.get("canonical") or pin["link"]
            cur = merged.setdefault((kind, ref), {"kind": kind, "ref": ref, "title": pin.get("title", ""),
                                                  "details": pin.get("details", ""), "alt": pin.get("alt", ""), "boards": []})
            board = pin.get("board") or pin.get("board_id") or ""
            if board and board not in cur["boards"]:
                cur["boards"].append(board)
    if not section:
        raise ValueError(f"no 'Pins' section in {target}; is this a Pinterest account data export?")
    for cur in merged.values():
        boards = " / ".join(cur["boards"])
        extras = ([f"Alt text: {cur['alt']}"] if cur["alt"] else []) + ([f"Board: {boards}"] if boards else [])
        text = cur["details"] + ("\n" if cur["details"] and extras else "") + "\n".join(extras)
        yield {"kind": cur["kind"], "ref": cur["ref"], "title": cur["title"], "text": text,
               "origin": f"pinterest-export:{boards or 'pins'}"}

SOURCES = {"folder": folder, "arena": arena, "pinterest": pinterest,
           "pinterest-export": pinterest_export, "x-export": x_export}
