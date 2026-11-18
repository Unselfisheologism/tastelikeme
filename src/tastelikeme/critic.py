"""Critic loop (adapted from the dream-loop judge pattern). The CLI never calls a model: `critic prompt`
emits a judge brief for an INDEPENDENT fresh-context agent (the harness spawns it), `critic record`
stores its verdict and returns the loop state (pass / continue / stall / stalled). The target is a real
canon image or a user-supplied bar image, never an AI-generated one."""
import re
from .store import append, read

PASS_AT = 8.0
MAX_LOOPS = 3
RUBRIC = [("composition", 3, "layout, one graphic idea at 40-60% of the area, 4-5 hierarchy levels, air, cell/spread structure"),
          ("palette_material", 3, "strict colour discipline, accent used once, grain/shadow/halftone where the target has material"),
          ("type", 2, "tight grotesque headline, heavy faces as marks only, data text medium+ and >=16px, consistent micro-labels"),
          ("object", 1, "reads as a designed object or real mechanism, not boxes or widgets"),
          ("story", 1, "one message per cell, brand world, no info-panel filler")]
CAPS = {k: m for k, m, _ in RUBRIC}

JUDGE = """You are an independent judge with a fresh context. Judge how close the PIECE is to the TARGET's bar (quality and system, not content).
Open every image path below with your vision tool before scoring.

PIECE: {piece_path}
TARGET (bar): {target_path}
{prev}
Score along this rubric (fractions allowed):
{rubric}
{house}
Be nitpicky and precise. List EVERY gap that blocks a perfect score, per category, each as an actionable fix that names what causes the impression and how to fix it. Avoid vague notes like "looks flat". If a previous verdict is given, stay consistent with it; if the piece regressed, score lower. Also check: no templating from earlier rounds, steal the target's system do not copy its content.
Return JSON only: {{"scores": {{{keys}}}, "gaps": ["..."]}} then the harness runs:
  tastelikeme critic record --piece {piece} --round {rnd} --scores '<json scores>' --gap '<gap>' ... [--slot {slot}]
"""

def _norm(g): return re.sub(r"[^a-z0-9 ]", "", g.lower()).strip()

def records(s, slot=None, piece=None):
    r = [x for x in read(s.critic_p) if x.get("op") == "critic"]
    if slot: r = [x for x in r if x["slot"] == slot]
    if piece: r = [x for x in r if x["piece"] == piece]
    return r

def judge_prompt(s, piece, target_path, piece_path, slot, house=""):
    prev = records(s, slot=slot)
    rnd = len(prev) + 1
    pv = ""
    if prev:
        p = prev[-1]
        pv = f"PREVIOUS ROUND: piece {p['piece']} scored {p['total']}/10. Prior gaps: " + "; ".join(p["gaps"]) + "\n"
    rub = "\n".join(f"- {k} (0-{m}): {d}" for k, m, d in RUBRIC) + "\nTotal out of 10."
    keys = ", ".join(f'"{k}": n' for k, _, _ in RUBRIC)
    return rnd, JUDGE.format(piece_path=piece_path, target_path=target_path, prev=pv, rubric=rub,
                             house=house, keys=keys, piece=piece, rnd=rnd, slot=slot)

def state(recs):
    """pass | continue | stall | stalled, from the slot's critic history (oldest first)."""
    if not recs: return "continue", "no rounds yet"
    last = recs[-1]
    if last["total"] >= PASS_AT: return "pass", f"score {last['total']} >= {PASS_AT}"
    if len(recs) >= 2 and {_norm(g) for g in last["gaps"]} & {_norm(g) for g in recs[-2]["gaps"]}:
        same = True
    else: same = False
    best = lambda rs: max(r["total"] for r in rs)
    flat = len(recs) >= 3 and best(recs) - best(recs[:-2]) < 1
    if len(recs) >= 2 and recs[-2].get("rethought") and last["total"] <= recs[-2]["total"]:
        return "stalled", "architectural rethink did not improve the score: deliver with open gaps stated, ask the user"
    if same or flat:
        return "stall", ("same gap named twice" if same else "best score not up by 1 in 2 rounds") + ": stop tweaking, rethink layout/hero idea, record next round with --rethought"
    if len(recs) >= MAX_LOOPS: return "stalled", f"{MAX_LOOPS} loops used: deliver with open gaps stated"
    return "continue", f"score {last['total']} < {PASS_AT}: fix ALL gaps and re-judge"

def record(s, piece, slot, rnd, scores, gaps, rethought=False):
    for k, v in scores.items():
        if k not in CAPS: raise ValueError(f"unknown rubric key {k}")
        if not 0 <= float(v) <= CAPS[k]: raise ValueError(f"{k} must be 0-{CAPS[k]}")
    missing = set(CAPS) - set(scores)
    if missing: raise ValueError("missing scores: " + ", ".join(sorted(missing)))
    total = round(sum(float(v) for v in scores.values()), 2)
    if total < 10 and not gaps: raise ValueError("a score below 10 needs at least one --gap")
    rec = {"op": "critic", "piece": piece, "slot": slot, "round": rnd, "scores": scores, "total": total, "gaps": gaps, "rethought": rethought}
    append(s.critic_p, rec)
    st, why = state(records(s, slot=slot))
    return {**rec, "state": st, "why": why}

def recurring_gaps(s, n=8):
    c = {}
    for r in records(s):
        for g in r["gaps"]: c.setdefault(_norm(g), [g, 0])[1] += 1
    return [(g, k) for g, k in sorted(c.values(), key=lambda x: -x[1]) if k >= 2][:n]
