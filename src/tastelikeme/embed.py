"""Local CPU embeddings. fastembed (small ONNX model) when installed, else a
deterministic hashing embedder so everything still runs offline."""
import hashlib, json, math, os, re
DIM = 256

def _hash_embed(text):
    toks = re.findall(r"[a-z0-9']+", text.lower())
    grams = toks + [a + "_" + b for a, b in zip(toks, toks[1:])]
    v = [0.0] * DIM
    for g in grams:
        h = int(hashlib.sha1(g.encode()).hexdigest(), 16)
        v[h % DIM] += 1.0 if (h >> 8) & 1 else -1.0
    return _norm(v)

def _norm(v):
    n = math.sqrt(sum(x * x for x in v)) or 1.0
    return [x / n for x in v]

_model = None
def backend():
    if os.environ.get("TASTELIKEME_EMBED") == "hash":
        return "hash"
    try:
        import fastembed  # noqa
        return "bge-small-en-v1.5"
    except Exception:
        return "hash"

def embed_texts(texts):
    name = backend()
    if name == "hash":
        return "hash", [_hash_embed(t) for t in texts]
    global _model
    try:
        if _model is None:
            from fastembed import TextEmbedding
            _model = TextEmbedding("BAAI/bge-small-en-v1.5")
        return name, [_norm([float(x) for x in e]) for e in _model.embed(texts)]
    except Exception:
        return "hash", [_hash_embed(t) for t in texts]

def cos(a, b): return sum(x * y for x, y in zip(a, b))

def kmeans(vecs, k, iters=25):
    """Deterministic: farthest-first init, then Lloyd iterations."""
    n = len(vecs)
    k = max(1, min(k, n))
    cents = [vecs[0]]
    while len(cents) < k:
        far = min(range(n), key=lambda i: max(cos(vecs[i], c) for c in cents))
        cents.append(vecs[far])
    assign = [0] * n
    for _ in range(iters):
        new = [max(range(k), key=lambda c: cos(vecs[i], cents[c])) for i in range(n)]
        if new == assign and _ > 0: break
        assign = new
        for c in range(k):
            mem = [vecs[i] for i in range(n) if assign[i] == c]
            if mem:
                cents[c] = _norm([sum(col) / len(mem) for col in zip(*mem)])
    return assign, cents
