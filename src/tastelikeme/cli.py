"""tastelikeme CLI. Dual mode: prompts when a human runs it in a terminal and flags are
missing; strict flags + --json for agents (or pass --non-interactive). The CLI never
loops and never calls a model. The harness owns the loop; this owns state and verbs."""
import argparse, datetime as dt, hashlib, json, re, sys, uuid
from pathlib import Path
from . import __version__, embed, prompts, sources
from .store import Store, home, now, sid, append, read

def interactive(a):
    return sys.stdin.isatty() and sys.stdout.isatty() and not a.non_interactive and not a.json

def out(a, obj, text=None):
    if a.json:
        print(json.dumps(obj, ensure_ascii=False, indent=2))
    else:
        print(text if text is not None else json.dumps(obj, ensure_ascii=False, indent=2))

def die(msg, code=2):
    print(f"error: {msg}", file=sys.stderr); sys.exit(code)

def ask(a, val, label, default=None):
    if val is not None: return val
    if interactive(a):
        s = input(f"{label}" + (f" [{default}]" if default else "") + ": ").strip()
        return s or default
    if default is not None: return default
    die(f"missing {label} (pass the flag, or run in a terminal for prompts)")

def store(a, need=True):
    s = Store(home(a.home))
    if need and not s.exists():
        die(f"no taste store at {s.root}. Run: tastelikeme init", 1)
    return s

# ---------- embedding helpers ----------
def item_text(it):
    if it.get("description"): return it["description"]
    return " ".join(x for x in (it.get("title"), it.get("text")) if x) or it.get("ref", "")

def active_backend(s):
    state = s.root / "embedding.json"
    if state.exists() and json.loads(state.read_text()).get("backend") == "hash": return "hash"
    return embed.backend()

def vec_for(s, texts):
    """Never mix embedding spaces; pin failed model to hash for this store."""
    state = s.root / "embedding.json"
    pinned = json.loads(state.read_text()).get("backend") if state.exists() else None
    model = "hash" if pinned == "hash" else embed.backend()
    cache = {(r["model"], r["h"]): r["v"] for r in read(s.vec_p)}
    res, missing = {}, []
    for t in dict.fromkeys(texts):
        h = hashlib.sha1(t.encode()).hexdigest()
        if (model, h) in cache: res[t] = cache[(model, h)]
        else: missing.append((t, h))
    if missing:
        if model == "hash":
            used, vs = "hash", [embed._hash_embed(t) for t, _ in missing]
        else:
            used, vs = embed.embed_texts([t for t, _ in missing])
        if used != model:
            state.write_text(json.dumps({"backend": "hash"}))
            print("warning: local model unavailable; using lower-quality hashing for this store", file=sys.stderr)
            return vec_for(s, texts)
        for (t, h), v in zip(missing, vs):
            res[t] = v
            append(s.vec_p, {"model": used, "h": h, "v": [round(x, 5) for x in v]})
    return res

def clusters(s, k=None):
    items = list(s.canon().values())
    if not items: return [], {}
    texts = [item_text(i) for i in items]
    vm = vec_for(s, texts)
    vecs = [vm[t] for t in texts]
    k = k or max(1, min(8, round((len(items) / 2) ** 0.5)))
    assign, cents = embed.kmeans(vecs, k)
    cl = []
    for c in range(len(cents)):
        mem = [i for i in range(len(items)) if assign[i] == c]
        if not mem: continue
        anchor = max(mem, key=lambda i: embed.cos(vecs[i], cents[c]))
        cl.append({"key": "c-" + items[anchor]["id"], "anchor": items[anchor]["id"], "centroid": cents[c],
                   "members": [items[i]["id"] for i in mem]})
    labels = {}
    for r in read(s.root / "labels.jsonl"):
        labels[r["anchor"]] = r["name"]
    for c in cl:
        c["label"] = next((labels[m] for m in c["members"] if m in labels), None)
    return cl, {i["id"]: (items[n], vecs[n]) for n, i in enumerate(items)}

def nearest_cluster(s, cl, text):
    if not cl: return None
    v = vec_for(s, [text])[text]
    return max(cl, key=lambda c: embed.cos(v, c["centroid"])), v

def positive_int(value):
    try: n = int(value)
    except (ValueError, TypeError): raise argparse.ArgumentTypeError("must be a positive integer")
    if n < 1: raise argparse.ArgumentTypeError("must be a positive integer")
    return n

def validate_config(c):
    positive_int(c.get("distill_every", 20))
    cadence = c.get("cadence", "weekly").lower()
    if cadence not in ("daily", "weekly", "monthly", "off", "manual", "none") and not re.fullmatch(r"[1-9]\d*[hdw]", cadence):
        raise ValueError("cadence must be daily, weekly, monthly, off, or a positive interval such as 3d or 12h")

def verdict_progress(s):
    total = sum(r.get("op") == "verdict" for r in read(s.verdicts_p))
    seen = max([r.get("verdicts_seen", 0) for r in s.rubrics() if r.get("cluster") in (None, "global")] or [0])
    return total, seen

# ---------- commands ----------
def cmd_init(a):
    s = Store(home(a.home))
    c = s.config() if s.exists() else {}
    cad = ask(a, a.cadence, "Generation cadence (daily, weekly, 3d, 12h, off)", c.get("cadence", "weekly"))
    c.update(cadence=cad, distill_every=a.distill_every or c.get("distill_every", 20))
    validate_config(c)
    s.init()
    c.setdefault("created", s.config()["created"])
    s.save_config(c)
    out(a, {"ok": True, "root": str(s.root), "config": c}, f"initialized {s.root}\ncadence: {cad}, re-distill every {c['distill_every']} verdicts")

def cmd_config(a):
    s = store(a); c = s.config()
    if a.set:
        k, _, v = a.set.partition("=")
        if k not in ("cadence", "distill_every"): die("settable keys: cadence, distill_every")
        c[k] = positive_int(v) if k == "distill_every" else v
        validate_config(c)
        s.save_config(c)
    out(a, c)

def cmd_ingest(a):
    s = store(a)
    fn = sources.SOURCES.get(a.source) or die(f"unknown source {a.source}; choose from {', '.join(sources.SOURCES)}")
    known = s.canon(); added = 0
    try:
        for it in fn(a.target):
            iid = sid(it["kind"], it["ref"])
            if iid in known: continue
            rec = {"op": "add", "id": iid, **{k: v for k, v in it.items() if v not in (None, "")}}
            append(s.canon_p, rec); known[iid] = rec; added += 1
            s.asset_path(rec, fetch=False)  # copy local images into the store
    except Exception as e:
        if added == 0: die(f"ingest failed: {e}", 1)
        print(f"warning: stopped early: {e}", file=sys.stderr)
    pending = [i for i in s.canon().values() if not i.get("description")]
    result = {"added": added, "total": len(known), "pending_description": len(pending)}
    if pending and not a.no_prompt: result["prompt"] = describe_prompt(pending, a.batch)
    out(a, result, f"added {added} new item(s), canon has {len(known)}, {len(pending)} need a description.")
    if pending and not a.no_prompt and not a.json:
        print("\n" + result["prompt"])

def describe_prompt(pending, batch=25):
    pending = pending[:batch]
    lines = []
    for i in pending:
        t = f"- id={i['id']} kind={i['kind']} ref={i['ref']}"
        if i.get("title"): t += f" title={i['title']!r}"
        if i.get("text"): t += f" text={i['text'][:300]!r}"
        lines.append(t)
    has_img = any(i["kind"] == "image" for i in pending)
    return prompts.DESCRIBE.format(vision=prompts.VISION_IMAGE if has_img else prompts.VISION_TEXT, items="\n".join(lines))

def cmd_prompt(a):
    s = store(a)
    pending = [i for i in s.canon().values() if not i.get("description")]
    if a.json:
        out(a, {"pending": len(pending), "prompt": describe_prompt(pending, a.batch) if pending else None, "items": pending[:a.batch]})
    else:
        print(describe_prompt(pending, a.batch) if pending else "nothing pending: every item has a description.")

def cmd_describe(a):
    s = store(a); canon = s.canon(); n = 0
    pairs = []
    if a.stdin:
        for line in sys.stdin.read().splitlines():
            if line.strip():
                r = json.loads(line)
                if not isinstance(r, dict) or "id" not in r or "description" not in r:
                    raise ValueError("each description line needs id and description")
                pairs.append((r["id"], r["description"]))
    else:
        pairs.append((ask(a, a.id, "item id"), ask(a, a.text, "description")))
    for iid, d in pairs:
        if not isinstance(iid, str) or iid not in canon: raise ValueError(f"unknown item id: {iid}")
        if not isinstance(d, str) or not d.strip(): raise ValueError("description must be a non-empty string")
    for iid, d in pairs:
        append(s.canon_p, {"op": "describe", "id": iid, "description": d}); n += 1
    out(a, {"described": n}, f"saved {n} description(s)")

def cmd_canon(a):
    s = store(a); cl, _ = clusters(s, a.k)
    where = {m: c for c in cl for m in c["members"]}
    items = s.canon().values()
    rows = [{"id": i["id"], "kind": i["kind"], "ref": i["ref"], "described": bool(i.get("description")),
             "cluster": where.get(i["id"], {}).get("key"), "cluster_label": where.get(i["id"], {}).get("label")} for i in items]
    summ = [{"cluster": c["key"], "label": c["label"], "size": len(c["members"]), "anchor": c["anchor"]} for c in cl]
    if a.json: out(a, {"clusters": summ, "items": rows}); return
    print(f"{len(rows)} items, {len(cl)} clusters (emergent, from embeddings; embedder={active_backend(s)})")
    for c in summ:
        print(f"  {c['cluster']}  n={c['size']}  label={c['label'] or '(unlabeled - agent may name it)'}")
    if a.verbose:
        for r in rows: print(f"  {r['id']} {r['kind']:5} {r['cluster']} {r['ref']}")

def cmd_label(a):
    s = store(a); cl, _ = clusters(s)
    c = next((c for c in cl if c["key"] == a.cluster), None) or die(f"unknown cluster {a.cluster}; run: tastelikeme canon")
    append(s.root / "labels.jsonl", {"anchor": c["anchor"], "name": a.name})
    out(a, {"cluster": c["key"], "label": a.name}, f"{c['key']} labeled {a.name!r} (display only, never used for retrieval)")

def gens_due(s):
    cad = (s.config().get("cadence") or "weekly").lower()
    if cad in ("off", "manual", "none"): return {"cadence": cad, "due": False, "reason": "cadence off"}
    m = re.fullmatch(r"(\d+)([hdw])", cad)
    secs = {"daily": 86400, "weekly": 7 * 86400, "monthly": 30 * 86400}.get(cad)
    if m: secs = int(m.group(1)) * {"h": 3600, "d": 86400, "w": 604800}[m.group(2)]
    if not secs: return {"cadence": cad, "due": True, "reason": "unrecognized cadence; treating as due"}
    g = list(s.generations().values())
    if not g: return {"cadence": cad, "due": True, "last": None, "reason": "no generations yet"}
    last = max(dt.datetime.strptime(x["ts"], "%Y-%m-%dT%H:%M:%S%z") for x in g)
    nxt = last + dt.timedelta(seconds=secs)
    return {"cadence": cad, "due": dt.datetime.now(last.tzinfo) >= nxt, "last": last.isoformat(), "next_due": nxt.isoformat()}

def cmd_due(a):
    d = gens_due(store(a)); out(a, d, f"due: {d['due']} (cadence {d['cadence']}" + (f", next {d['next_due']}" if d.get("next_due") else "") + ")")

def exemplars(s, cl, cluster, rvec, n):
    members = set(cluster["members"]) if cluster else set()
    canon = s.canon()
    near = sorted((i for i in canon.values() if i["id"] in members and i.get("description")),
                  key=lambda i: -embed.cos(vec_for(s, [item_text(i)])[item_text(i)], rvec) if rvec else 0)[:n]
    gens = s.generations(); liked, disliked = [], []
    for v in s.verdicts():
        g = gens.get(v["piece"])
        if not g: continue
        gt = g.get("request", "") + " " + (g.get("text") or "")
        gv = vec_for(s, [gt])[gt]
        gc = nearest_cluster(s, cl, gt)
        if cluster and gc and gc[0]["key"] != cluster["key"]: continue
        ex = {"piece": g["id"], "purpose": g.get("purpose"), "request": g.get("request"),
              "content": (g.get("text") or g.get("file"))[:600] if (g.get("text") or g.get("file")) else None,
              "feedback": v.get("feedback"), "aspect": v.get("aspect"), "sim": embed.cos(gv, rvec) if rvec else 0}
        (liked if v["verdict"] == "up" else disliked).append(ex)
    key = lambda e: -e["sim"]
    return near, sorted(liked, key=key)[:n], sorted(disliked, key=key)[:n]

def attach_images(s, b, fetch=True):
    """Resolve real image files for canon exemplars and liked/disliked pieces so a vision-capable
    generator can open them. Adds 'image' to each exemplar plus a flat 'images' list."""
    imgs = []
    canon = s.canon()
    for i in b["canon_nearest"]:
        p = s.asset_path(canon[i["id"]], fetch=fetch) if i["id"] in canon else None
        i["image"] = p
        imgs.append({"role": "canon", "id": i["id"], "path": p})
    for role in ("liked", "disliked"):
        for e in b[role]:
            f = (s.generations().get(e["piece"]) or {}).get("file")
            p = f if f and Path(f).is_file() and Path(f).suffix.lower() in IMG_EXT_CLI else None
            e["image"] = p
            if p: imgs.append({"role": role, "id": e["piece"], "path": p})
    b["images"] = imgs
    return b

IMG_EXT_CLI = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp"}

def make_brief(s, request, cluster_key, n):
    marker = s.root / "embedding.json"
    before = marker.read_text() if marker.exists() else None
    try: return _make_brief(s, request, cluster_key, n)
    except ValueError:
        after = marker.read_text() if marker.exists() else None
        if before == after: raise
        return _make_brief(s, request, cluster_key, n)

def _make_brief(s, request, cluster_key, n):
    cl, _ = clusters(s)
    cluster, rvec = None, None
    if request and cl:
        cluster, rvec = nearest_cluster(s, cl, request)
    if cluster_key:
        cluster = next((c for c in cl if c["key"] == cluster_key), None) or die(f"unknown cluster {cluster_key}")
    rubrics = s.rubrics()
    scoped = [r for r in rubrics if cluster and r.get("cluster") == cluster["key"]]
    rub = (scoped or [r for r in rubrics if r.get("cluster") in (None, "global")] or [None])[-1]
    total, seen = verdict_progress(s)
    every = s.config().get("distill_every", 20)
    near, liked, disliked = exemplars(s, cl, cluster, rvec, n)
    return {"request": request,
            "cluster": ({"key": cluster["key"], "label": cluster["label"], "size": len(cluster["members"])} if cluster else None),
            "rubric": rub["text"] if rub else None,
            "canon_nearest": [{"id": i["id"], "kind": i["kind"], "ref": i["ref"], "description": i["description"]} for i in near],
            "liked": liked, "disliked": disliked,
            "verdicts_total": total, "verdicts_since_distill": total - seen, "distill_due": (total - seen) >= every}

def render_brief(b):
    L = ["# TASTE BRIEF", f"request: {b['request'] or '(none)'}"]
    c = b["cluster"]
    L.append(f"cluster: {c['key']} ({c['label'] or 'unlabeled'}, {c['size']} canon items)" if c else "cluster: none yet (empty canon)")
    L += ["", "## Rubric", b["rubric"] or "(no rubric yet: judge from the exemplars below)", "", "## Canon exemplars nearest the request"]
    L += [f"- [{i['id']}] {i['description']}" for i in b["canon_nearest"]] or ["(none)"]
    L += ["", "## Liked pieces"] + [f"- [{e['piece']}] {e['request']}: {e['content'] or ''}" for e in b["liked"]] or ["(none)"]
    L += ["", "## Disliked pieces (avoid)"]
    L += [f"- [{e['piece']}] {e['request']}: {e['content'] or ''} | feedback ({e['aspect'] or 'general'}): {e['feedback'] or 'none given'}" for e in b["disliked"]] or ["(none)"]
    shown = [x for x in b.get("images", []) if x.get("path")]
    if shown:
        L += ["", "## LOOK AT THESE IMAGES FIRST (vision-capable generators: open each path before designing)"]
        L += [f"- {x['role']} [{x['id']}] {x['path']}" for x in shown]
    missing = [x for x in b.get("images", []) if x["role"] == "canon" and not x.get("path")]
    if missing:
        L.append("(no image file for: " + ", ".join(x["id"] for x in missing) + "; local file missing or URL dead. Re-ingest the file to attach it.)")
    L.append("\nSTYLE STUDY: read .tastelikeme/reference/style_study.md (system, type, texture, bar refs) before designing.")
    L.append(f"\nverdicts since last distill: {b['verdicts_since_distill']}" + ("  -> DISTILL DUE: run `tastelikeme distill`" if b["distill_due"] else ""))
    return "\n".join(L)

def cmd_brief(a):
    s = store(a); b = make_brief(s, a.request, a.cluster, a.n)
    if not a.no_images: attach_images(s, b)
    out(a, b, render_brief(b))

def cmd_assets(a):
    """Backfill: make every canon image available as a local file inside the store."""
    s = store(a); ok = miss = 0; gone = []
    for it in s.canon().values():
        if it.get("kind") != "image": continue
        if s.asset_path(it, fetch=not a.local_only): ok += 1
        else: miss += 1; gone.append(it["id"])
    out(a, {"available": ok, "missing": miss, "missing_ids": gone, "dir": str(s.assets)},
        f"{ok} image(s) in {s.assets}, {miss} unavailable" + (": " + ", ".join(gone[:20]) if gone else ""))

def cmd_next(a):
    s = store(a); b = make_brief(s, a.request, a.cluster, a.n); d = gens_due(s)
    if not a.no_images: attach_images(s, b)
    purpose = a.purpose or "unspecified"
    if a.json:
        out(a, {"due": d, "purpose": purpose, "brief": b, "instructions": [
            "If brief.images has paths, open each image with your vision tool BEFORE generating; the descriptions are summaries, the images are the taste.",
            "Generate the piece using the brief as taste. Purpose is a tag, not a taste category.",
            "Log it: tastelikeme log --request <request> --purpose <purpose> --text <content> | --file <path>",
            "Ask the user for a verdict, then: tastelikeme verdict --piece <id> --up|--down [--feedback ...]"]}); return
    print(render_brief(b))
    print(f"\n## Generate\npurpose: {purpose}. cadence: {d['cadence']} (due: {d['due']}).")
    print("Make the piece, then run: tastelikeme log --request \"...\" --purpose %s --text \"...\"  (or --file PATH)" % purpose)

def cmd_log(a):
    s = store(a)
    req = ask(a, a.request, "request")
    if not req.strip(): raise ValueError("request must not be empty")
    text = a.text
    if text is None and a.file is None:
        text = ask(a, None, "generated content (text, or use --file for an image path)")
    if a.file is not None and not Path(a.file).expanduser().is_file():
        raise ValueError(f"generated file does not exist: {a.file}")
    pid = a.id or uuid.uuid4().hex[:16]
    if pid in s.generations(): raise ValueError(f"piece id already exists: {pid}")
    append(s.gens_p, {"op": "generation", "id": pid, "request": req, "purpose": a.purpose or "unspecified", "text": text, "file": a.file})
    out(a, {"piece": pid}, f"logged piece {pid}")

def cmd_verdict(a):
    s = store(a); gens = s.generations()
    piece = a.piece
    if piece is None and interactive(a):
        for g in list(gens.values())[-8:]: print(f"  {g['id']}  {g['request'][:60]}")
        piece = ask(a, None, "piece id")
    piece = piece or die("missing --piece")
    if piece not in gens: die(f"unknown piece {piece}; log it first with: tastelikeme log", 1)
    vd = "up" if a.up else "down" if a.down else None
    existing = {v["piece"]: v for v in s.verdicts()}
    if vd is None and piece in existing and (a.feedback or a.aspect):
        append(s.verdicts_p, {"op": "amend", "piece": piece, "feedback": a.feedback, "aspect": a.aspect})
        out(a, {"piece": piece, "amended": True}, "feedback saved"); return
    if vd is None:
        if interactive(a): vd = "up" if ask(a, None, "verdict (up/down)").lower().startswith("u") else "down"
        else: die("pass --up or --down")
    fb, asp = a.feedback, a.aspect
    followup = None
    if vd == "down" and not (fb or asp):
        if interactive(a):
            asp = input(prompts.FOLLOWUP + " ").strip() or None
            fb = asp
        else:
            followup = prompts.FOLLOWUP
    append(s.verdicts_p, {"op": "verdict", "piece": piece, "verdict": vd, "feedback": fb, "aspect": asp})
    total, seen = verdict_progress(s)
    due = total - seen >= s.config().get("distill_every", 20)
    res = {"piece": piece, "verdict": vd, "needs_followup": followup, "verdicts_total": total, "distill_due": due}
    if followup:
        res["how"] = f"Ask the user, then run: tastelikeme verdict --piece {piece} --feedback \"<answer>\" [--aspect palette|layout|pacing|subject|tone]"
    out(a, res, f"verdict {vd} logged for {piece}" + (f"\nASK THE USER: {followup}\nthen: tastelikeme verdict --piece {piece} --feedback \"...\"" if followup else "") + ("\nrubric re-distill is due: tastelikeme distill" if due else ""))

def cmd_distill(a):
    s = store(a); vs = s.verdicts(); gens = s.generations()
    cur = [r for r in s.rubrics() if r.get("cluster", "global") == (a.cluster or "global")]
    checkpoint = max([r.get("verdict_events_seen", r.get("verdicts_seen", 0)) for r in cur] or [0])
    touched = {r["piece"] for r in read(s.verdicts_p)[checkpoint:] if "piece" in r}
    new = vs if a.all else [v for v in vs if v["piece"] in touched]
    if a.cluster:
        cl, _ = clusters(s)
        if not any(c["key"] == a.cluster for c in cl): raise ValueError(f"unknown cluster {a.cluster}")
        selected = []
        for v in new:
            g = gens.get(v["piece"], {})
            match = nearest_cluster(s, cl, g.get("request", "") + " " + (g.get("text") or ""))
            if match and match[0]["key"] == a.cluster: selected.append(v)
        new = selected
    lines = []
    for v in new:
        g = gens.get(v["piece"], {})
        lines.append(f"- {v['verdict'].upper()} [{v['piece']}] request={g.get('request')!r} content={(g.get('text') or g.get('file') or '')[:300]!r} feedback({v.get('aspect') or 'general'})={v.get('feedback') or 'none'}")
    rub = cur[-1]["text"] if cur else "(none yet)"
    p = prompts.DISTILL.format(rubric=rub, verdicts="\n".join(lines) or "(none)", cluster_flag=f"--cluster {a.cluster}" if a.cluster else "")
    out(a, {"new_verdicts": len(new), "prompt": p}, p)

def cmd_rubric(a):
    s = store(a)
    if a.action == "set":
        if a.cluster and not any(c["key"] == a.cluster for c in clusters(s)[0]):
            raise ValueError(f"unknown cluster {a.cluster}")
        text = a.text if a.text is not None else sys.stdin.read()
        if not text.strip(): die("empty rubric")
        append(s.rubric_p, {"cluster": a.cluster or "global", "text": text.strip(), "verdicts_seen": verdict_progress(s)[0], "verdict_events_seen": len(read(s.verdicts_p))})
        out(a, {"ok": True}, "rubric saved")
    else:
        r = [r for r in s.rubrics() if r.get("cluster", "global") == (a.cluster or "global")]
        out(a, r[-1] if r else {"text": None}, r[-1]["text"] if r else "(no rubric yet)")

def cmd_status(a):
    s = store(a); items = s.canon().values()
    d = {"root": str(s.root), "canon": len(items), "undescribed": sum(1 for i in items if not i.get("description")),
         "generations": len(s.generations()), "verdicts": len(s.verdicts()), "embedder": active_backend(s),
         "config": s.config(), "due": gens_due(s)}
    out(a, d)

def build():
    p = argparse.ArgumentParser(prog="tastelikeme", description="Taste canon CLI: primitives for your own agent. No API keys, no model calls.")
    p.add_argument("--home", help="store dir (default ./.tastelikeme or $TASTELIKEME_HOME)")
    p.add_argument("--json", action="store_true", help="machine-readable output (agents)")
    p.add_argument("--non-interactive", action="store_true", help="never prompt")
    p.add_argument("--version", action="version", version=__version__)
    sp = p.add_subparsers(dest="cmd", required=True)
    def add(name, fn, help):
        q = sp.add_parser(name, help=help); q.set_defaults(fn=fn); return q
    q = add("init", cmd_init, "create the store"); q.add_argument("--cadence"); q.add_argument("--distill-every", type=positive_int)
    q = add("config", cmd_config, "show or set config"); q.add_argument("--set", metavar="KEY=VALUE")
    q = add("ingest", cmd_ingest, "add saved content"); q.add_argument("source", choices=list(sources.SOURCES)); q.add_argument("target")
    q.add_argument("--no-prompt", action="store_true"); q.add_argument("--batch", type=positive_int, default=25)
    q = add("prompt", cmd_prompt, "emit the creative-director description prompt for undescribed items"); q.add_argument("--batch", type=positive_int, default=25)
    q = add("describe", cmd_describe, "save an item description"); q.add_argument("--id"); q.add_argument("--text"); q.add_argument("--stdin", action="store_true")
    q = add("canon", cmd_canon, "list canon and emergent clusters"); q.add_argument("--k", type=positive_int); q.add_argument("-v", "--verbose", action="store_true")
    q = add("label", cmd_label, "name a cluster (human display only)"); q.add_argument("cluster"); q.add_argument("name")
    add("due", cmd_due, "is a generation due per cadence?")
    for nm, fn, h in (("brief", cmd_brief, "rubric + top liked/disliked exemplars for the request's cluster"),
                      ("next", cmd_next, "brief + generation instructions + due state")):
        q = add(nm, fn, h); q.add_argument("--no-images", action="store_true", help="skip resolving image files for exemplars"); q.add_argument("--request"); q.add_argument("--cluster"); q.add_argument("--n", type=positive_int, default=3)
        if nm == "next": q.add_argument("--purpose", help="thumbnail, script, tweet... a tag, not a taste category")
    q = add("assets", cmd_assets, "copy/download every canon image into the store so generators can view them"); q.add_argument("--local-only", action="store_true")
    q = add("log", cmd_log, "record a generated piece"); q.add_argument("--request"); q.add_argument("--purpose"); payload = q.add_mutually_exclusive_group(); payload.add_argument("--text"); payload.add_argument("--file"); q.add_argument("--id")
    q = add("verdict", cmd_verdict, "thumbs up/down (+ targeted feedback)"); q.add_argument("--piece"); vote = q.add_mutually_exclusive_group(); vote.add_argument("--up", action="store_true")
    vote.add_argument("--down", action="store_true"); q.add_argument("--feedback"); q.add_argument("--aspect")
    q = add("distill", cmd_distill, "emit the rubric-distillation prompt"); q.add_argument("--all", action="store_true"); q.add_argument("--cluster")
    q = add("rubric", cmd_rubric, "show or set the rubric"); q.add_argument("action", choices=["show", "set"]); q.add_argument("--text"); q.add_argument("--cluster")
    add("status", cmd_status, "counts and config")
    return p

def main(argv=None):
    a = build().parse_args(argv)
    try: a.fn(a)
    except BrokenPipeError: pass
    except KeyboardInterrupt: sys.exit(130)
    except (ValueError, TypeError, OSError, argparse.ArgumentTypeError) as exc:
        die(str(exc))

if __name__ == "__main__":
    main()
