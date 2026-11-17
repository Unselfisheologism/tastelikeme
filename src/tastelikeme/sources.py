"""Pluggable intake sources. A source is a function (target) -> iterable of item dicts:
  {kind: image|text|link, ref: path-or-url, title?, text?, origin: str}
Register new ones in SOURCES."""
import json, re, urllib.request, zipfile
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

SOURCES = {"folder": folder, "arena": arena, "pinterest": pinterest, "x-export": x_export}
