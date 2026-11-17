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

First embedding run downloads a ~130MB ONNX model (`bge-small-en-v1.5`). No GPU. If it can't download, a hashing embedder takes over with lower quality. The store pins this fallback to avoid mixing incompatible vectors; delete `embedding.json` and `vectors.jsonl` inside the store to retry the local model. `status` reports the active backend.

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
| X data export | `ingest x-export archive.zip` | reads `data/like.js` and `data/bookmark.js` (also under an archive prefix); test coverage uses synthetic archive fixtures, not a private user archive |
| Pinterest data export | `ingest pinterest-export <export.zip>` | **first-class Pinterest route: every pin on every board, secret boards included.** Request in the app or site: Settings -> Privacy and data -> Request your data; Pinterest emails a download link (can take up to 48 hours). Also accepts the extracted Pins `.html` file. Details below |
| Pinterest board | `ingest pinterest <public-board-url>` | best effort, first-page HTML snapshot only, not a full board; may include non-pin images or break when Pinterest changes markup. Use `pinterest-export` for your full boards |

Add a source: write `def mysource(target): yield {"kind": "image|text|link", "ref": ..., "title": ..., "origin": ...}` and register it in `SOURCES`.

### Pinterest: full boards via your data export

1. In the Pinterest app or on pinterest.com: profile -> Settings -> Privacy and data -> Request your data -> Start request.
2. Pinterest emails a download link (their help page says it can take up to 48 hours).
3. `tastelikeme ingest pinterest-export <export.zip>` (or the `.html` file with the Pins section, extracted from the zip).

Each pin becomes an image item: a `736x` i.pinimg.com URL rebuilt from the export's image id, with your title, note, alt text and board name(s) as its text. One pin saved to several boards merges into one item that names all its boards. Pins without a usable image (videos, missing ids) become link items from their source URL. What it does **not** deliver: the export contains data and links, no image files, so URLs only work while the pins and account stay up; pins already deleted at export time are skipped, and a small share of the rest can still turn out dead. The Pinterest API v5 is deliberately not used: it requires a developer app, OAuth approval, and apps registered on or after 2026-09-14 can be denied board/pin reads for personal accounts. The export format is parsed as-is; if Pinterest changes it, intake fails with a clear error instead of guessing.

## How it works

1. **Ingest** appends items to `canon.jsonl` and prints a premade *creative director* prompt: for each item, subject, palette, composition, mood, why it works. Image items need a **vision-capable agent**. A text-only agent can still describe from titles and text (marked `[text-only]`), and text saves need no vision at all.
2. **Embeddings** run locally on CPU over the descriptions.
3. **Branching is clustering.** There are no predefined categories. Clusters emerge from the vectors (`canon`). The agent may name a cluster (`label`) purely for human readability. Names are never used for retrieval. Retrieval of the canon items nearest your request *is* the branching. `--purpose` (thumbnail, script, tweet) is a separate tag you set per generation.
4. **Verdicts.** `verdict --up|--down`. On a dislike the CLI asks one targeted question (`palette, layout, pacing...`). Agents get `needs_followup` in the JSON and ask the user. Vague feedback is stored, specific feedback is the training data.
5. **`brief`** returns the current rubric plus the top liked and disliked exemplars for the request's cluster. The brief contains the current rubric and a small set of nearest exemplars, not the entire canon. Undescribed canon items do not appear as exemplars.
6. **Re-distill** is verdict-triggered: after every ~20 new verdict events (a changed vote counts again; feedback amendments do not count) (`config --set distill_every=N`), `brief` and `verdict` report `distill_due`. `distill` prints a prompt for your agent, which saves the result with `rubric set`. The rubric plus marked exemplars is input for your own agent judge, not an implemented or calibrated scoring engine. Scoped rubrics do not clear the global distill reminder.
7. **Cadence** is a config value (`init --cadence 3d`, `config --set cadence=weekly`, `off` to stop). `due` reports whether a generation is due, for a human or an agent driver alike.

## Agent instructions (copy-paste into your agent's instructions)

```
You use the `tastelikeme` CLI to generate and judge content in the user's taste.
Always call it with: tastelikeme --json --non-interactive <command>   (uvx --from git+https://github.com/Unselfisheologism/tastelikeme tastelikeme ... if not installed)
Never rely on remembered taste. Files are the truth; your context may be compacted.

Setup / intake:
1. `init` once. `ingest <pinterest-export|folder|arena|x-export|pinterest> <target>`. Pinterest users: `ingest pinterest-export <export.zip>` gives full boards; request the export in the Pinterest app first (Settings -> Privacy and data -> Request your data).
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

## Input and storage limits

- Global flags `--home`, `--json`, and `--non-interactive` go **before** the command. `--json` never prompts and returns one JSON document on successful data commands; errors use stderr and nonzero exit codes. `ingest` includes its description prompt in the JSON `prompt` field.
- Folder intake uses extensions, not image decoding: nested images/text are included, unsupported extensions are ignored, and corrupt files with image extensions are still queued for the vision agent to inspect. Local file paths must remain accessible to your agent. Text intake is capped at 4,000 characters per file.
- Cluster keys come from a representative item, so they can change after new content or descriptions. Display labels follow surviving member anchors, but merges/splits can move a label or hide a conflicting label. Cluster-scoped rubrics use the current key and are not automatically migrated. Retrieval never uses display labels.
- Run one writer per store. Append-only JSONL is not a transactional database. Invalid JSONL raises an error with its path and line number rather than silently dropping your history. Back up the store before repairing it.
- File generations record a path, not a copy. Reusing an explicit piece ID is rejected. Auto-generated IDs are unique, including rapid repeated generations.
- Network intake is best effort. are.na private/missing channels return an error; the default public-channel scan cap is 200 blocks. A network failure after some items were added can leave a partial import with a warning on stderr. Pinterest export intake parses the Pins HTML of the current account-export format; a format change makes it fail with a clear error, not a guess.

## Tests

```bash
pip install -e . && python -m unittest discover -s tests
```
The end-to-end test covers init, folder ingest, description prompt, describe, clustering, log, verdict with follow-up, distill trigger, rubric, brief, cadence and the X and Pinterest exports.

The hardening suite also checks JSON-only agent intake, invalid inputs without partial writes, exact verdict boundaries, repeated IDs, malformed archives, exact are.na caps, display-only labels, model fallback dimensions, and Pinterest export parsing (secret boards, dead pins, board merges, bad archives). Network sources are tested separately from the deterministic suite.

MIT licensed.
