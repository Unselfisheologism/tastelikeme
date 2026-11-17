# tastelikeme

Turn the things you save into a **taste canon** your own coding agent can generate against and judge with.

- **CLI, not MCP.** Interactive prompts when a human runs it, strict flags and `--json` when an agent runs it.
- **No API keys.** The repo ships deterministic code only: storage, local CPU embeddings, clustering, retrieval, prompts. Your agent (Claude Code, OpenCode, Codex...) is the intelligence.
- **Loop-agnostic.** The CLI exposes primitives. Your harness, a cron job or you owns the loop.
- **Files are the truth.** `canon.jsonl`, `verdicts.jsonl` and `generations.jsonl` are append-only. The agent session is only a cache, so context compaction cannot lose your taste.

## Install and run (free, CPU only)

```bash
uvx --from git+https://github.com/Unselfisheologism/tastelikeme tastelikeme --help
# or: pipx install git+https://github.com/Unselfisheologism/tastelikeme
```

First embedding run downloads a ~130MB ONNX model (`bge-small-en-v1.5`). No GPU. If it can't download, a hashing embedder takes over so nothing breaks (lower quality).

## Quickstart (human)

```bash
tastelikeme init                              # prompts for cadence
tastelikeme ingest arena my-channel-slug      # or: folder ~/Pictures/inspo
tastelikeme prompt                            # copy this prompt into your agent so it describes each item
tastelikeme canon                             # see your emergent taste clusters
tastelikeme next --request "thumbnail about CRT retro" --purpose thumbnail
tastelikeme log --request "..." --purpose thumbnail --file out.png
tastelikeme verdict --piece <id> --down       # asks one question: which part missed?
tastelikeme brief --request "..."             # the rubric + best liked/disliked exemplars
```

The store lives in `./.tastelikeme` (override with `--home` or `$TASTELIKEME_HOME`).

## Sources (pluggable, in `src/tastelikeme/sources.py`)

| Source | Command | Notes |
|---|---|---|
| are.na channel | `ingest arena <slug-or-url>` | public API, no auth. `TASTELIKEME_ARENA_MAX` caps blocks (default 200) |
| local folder | `ingest folder <path>` | images (png/jpg/webp/...) and .txt/.md |
| X data export | `ingest x-export archive.zip` | reads likes and bookmarks from your X archive |
| Pinterest board | `ingest pinterest <public-board-url>` | best effort scrape of a public board; may break if Pinterest changes markup |

Add a source: write `def mysource(target): yield {"kind": "image|text|link", "ref": ..., "title": ..., "origin": ...}` and register it in `SOURCES`.

## How it works

1. **Ingest** appends items to `canon.jsonl` and prints a premade *creative director* prompt: for each item, subject, palette, composition, mood, why it works. Image items need a **vision-capable agent**. A text-only agent can still describe from titles and text (marked `[text-only]`), and text saves need no vision at all.
2. **Embeddings** run locally on CPU over the descriptions.
3. **Branching is clustering.** There are no predefined categories. Clusters emerge from the vectors (`canon`). The agent may name a cluster (`label`) purely for human readability. Names are never used for retrieval. Retrieval of the canon items nearest your request *is* the branching. `--purpose` (thumbnail, script, tweet) is a separate tag you set per generation.
4. **Verdicts.** `verdict --up|--down`. On a dislike the CLI asks one targeted question (`palette, layout, pacing...`). Agents get `needs_followup` in the JSON and ask the user. Vague feedback is stored, specific feedback is the training data.
5. **`brief`** returns the current rubric plus the top liked and disliked exemplars for the request's cluster. Every generation starts cold with your whole taste injected.
6. **Re-distill** is verdict-triggered: after every ~20 new verdicts (`config --set distill_every=N`), `brief` and `verdict` report `distill_due`. `distill` prints a prompt for your agent, which saves the result with `rubric set`. The rubric plus marked exemplars *is* the eval: a judge can score a new candidate against it with no human present.
7. **Cadence** is a config value (`init --cadence 3d`, `config --set cadence=weekly`, `off` to stop). `due` reports whether a generation is due, for a human or an agent driver alike.

## Agent instructions (copy-paste into your agent's instructions)

```
You use the `tastelikeme` CLI to generate and judge content in the user's taste.
Always call it with: tastelikeme --json --non-interactive <command>   (uvx --from git+https://github.com/Unselfisheologism/tastelikeme tastelikeme ... if not installed)
Never rely on remembered taste. Files are the truth; your context may be compacted.

Setup / intake:
1. `init` once. `ingest <arena|folder|x-export|pinterest> <target>`.
2. `prompt` returns undescribed items plus a creative-director prompt. Describe each item (you must LOOK at image items; if you cannot see images, say so and mark descriptions "[text-only]"). Save with `describe --stdin` (JSON lines {"id","description"}).
3. `canon` shows emergent clusters. Optionally `label <cluster> "<human name>"` for readability only.

Each generation (if `due` says due, or the user asks):
1. `next --request "<what to make>" --purpose <thumbnail|script|tweet|...>`: read the brief (rubric, nearest canon, liked/disliked pieces) and follow it.
2. Make the piece. Record it: `log --request "..." --purpose ... --text "..."` or `--file path`. Keep the returned piece id.
3. Show the user the piece and ask for a thumbs up or down. Record: `verdict --piece <id> --up|--down`.
4. If the result has `needs_followup`, ask the user that exact question, then `verdict --piece <id> --feedback "<their answer>" --aspect <palette|layout|pacing|subject|tone>`.
5. If `distill_due` is true: run `distill`, write the rubric it asks for, save with `rubric set --text "..."`.

Generation frequency is the user's config (`due`, `config --set cadence=...`). Keep producing on cadence until the user says stop. On stop, run `distill --all`, save the rubric, and tell the user the rubric and liked/disliked exemplars are their reusable eval.
Never ask the user for API keys. Never invent categories; clusters are computed.
```

## Tests

```bash
pip install -e . && python -m unittest discover -s tests
```
The end-to-end test covers init, folder ingest, description prompt, describe, clustering, log, verdict with follow-up, distill trigger, rubric, brief, cadence and X export.

MIT licensed.
