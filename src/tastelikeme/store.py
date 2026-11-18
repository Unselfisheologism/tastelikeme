"""Append-only storage. canon.jsonl, verdicts.jsonl and generations.jsonl are the
source of truth. vectors.jsonl and clusters.json are derived and can be deleted."""
import hashlib, json, os, shutil, time, urllib.request
from pathlib import Path

IMG_EXT = {'.png', '.jpg', '.jpeg', '.webp', '.gif', '.bmp', '.tif', '.tiff'}

def home(override=None):
    return Path(override or os.environ.get("TASTELIKEME_HOME") or ".tastelikeme").resolve()

def now():
    return time.strftime("%Y-%m-%dT%H:%M:%S%z")

def append(path, rec):
    rec = dict(rec); rec.setdefault("ts", now())
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    return rec

def read(path):
    p = Path(path)
    if not p.exists():
        return []
    out = []
    for number, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
        line = line.strip()
        if line:
            try: record = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"invalid JSON in {p}, line {number}: {exc.msg}") from exc
            if not isinstance(record, dict): raise ValueError(f"expected object in {p}, line {number}")
            out.append(record)
    return out

def sid(*parts):
    return hashlib.sha1("|".join(parts).encode()).hexdigest()[:10]

class Store:
    def __init__(self, root):
        self.root = Path(root)
        self.canon_p = self.root / "canon.jsonl"
        self.verdicts_p = self.root / "verdicts.jsonl"
        self.gens_p = self.root / "generations.jsonl"
        self.vec_p = self.root / "vectors.jsonl"
        self.clusters_p = self.root / "clusters.json"
        self.rubric_p = self.root / "rubric.jsonl"
        self.config_p = self.root / "config.json"
        self.assets = self.root / "assets"

    def exists(self): return self.config_p.exists()

    def init(self):
        self.root.mkdir(parents=True, exist_ok=True)
        for p in (self.canon_p, self.verdicts_p, self.gens_p, self.rubric_p):
            p.touch()
        if not self.config_p.exists():
            self.save_config({"cadence": "weekly", "distill_every": 20, "created": now()})

    def config(self):
        return json.loads(self.config_p.read_text()) if self.config_p.exists() else {}
    def save_config(self, c): self.config_p.write_text(json.dumps(c, indent=2))

    def canon(self):
        """Fold add/describe events into one record per item."""
        items = {}
        for r in read(self.canon_p):
            if r.get("op") == "add":
                items.setdefault(r["id"], dict(r))
            elif r.get("op") == "describe" and r["id"] in items:
                items[r["id"]]["description"] = r["description"]
                items[r["id"]]["described_ts"] = r.get("ts")
        return items

    def verdicts(self):
        """Fold verdict + amend events: latest feedback per piece wins."""
        v = {}
        for r in read(self.verdicts_p):
            if r.get("op") == "verdict":
                v[r["piece"]] = dict(r)
            elif r.get("op") == "amend" and r["piece"] in v:
                v[r["piece"]].update({k: r[k] for k in ("feedback", "aspect") if r.get(k)})
        return list(v.values())

    def generations(self):
        return {r["id"]: r for r in read(self.gens_p) if r.get("op") == "generation"}

    def rubrics(self): return read(self.rubric_p)

    # ---------- image assets (so a vision-capable generator can LOOK at canon) ----------
    def _asset_name(self, item):
        ref = item.get("ref", "")
        ext = Path(ref.split("?")[0]).suffix.lower()
        return f"{item['id']}{ext if ext in IMG_EXT else '.jpg'}"

    def asset_path(self, item, fetch=True):
        """Return a local image path for a canon item, or None. Local refs are copied into
        .tastelikeme/assets so the store carries its own images; URL refs are downloaded
        once and cached. Text/link items return None."""
        if item.get("kind") != "image": return None
        ref = item.get("ref", ""); dest = self.assets / self._asset_name(item)
        if dest.exists() and dest.stat().st_size > 0: return str(dest)
        if ref.startswith(("http://", "https://")):
            if not fetch: return None
            try:
                self.assets.mkdir(parents=True, exist_ok=True)
                req = urllib.request.Request(ref, headers={"User-Agent": "Mozilla/5.0 tastelikeme"})
                with urllib.request.urlopen(req, timeout=20) as r, open(dest, "wb") as f: f.write(r.read())
                return str(dest) if dest.stat().st_size > 0 else None
            except Exception:
                if dest.exists(): dest.unlink()
                return None
        src = Path(ref)
        if src.is_file():
            self.assets.mkdir(parents=True, exist_ok=True); shutil.copyfile(src, dest); return str(dest)
        return None
