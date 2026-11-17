"""Append-only storage. canon.jsonl, verdicts.jsonl and generations.jsonl are the
source of truth. vectors.jsonl and clusters.json are derived and can be deleted."""
import hashlib, json, os, time
from pathlib import Path

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
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            try: out.append(json.loads(line))
            except json.JSONDecodeError: pass
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
