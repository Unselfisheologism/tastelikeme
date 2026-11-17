import json, os, subprocess, sys, tempfile, unittest, zipfile, base64
from pathlib import Path

PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==")

def run(home, *args, stdin=None):
    env = dict(os.environ, TASTELIKEME_EMBED="hash")
    r = subprocess.run([sys.executable, "-m", "tastelikeme.cli", "--home", home, "--non-interactive", *args],
                       input=stdin, capture_output=True, text=True, env=env)
    assert r.returncode == 0, r.stderr + r.stdout
    return r.stdout

class E2E(unittest.TestCase):
    def test_flow(self):
        with tempfile.TemporaryDirectory() as d:
            home, folder = d + "/store", Path(d) / "saves"; folder.mkdir()
            for n in ("retro_crt_poster", "ghibli_forest", "stickman_comic", "crt_scanlines_ui"):
                (folder / f"{n}.png").write_bytes(PNG)
            (folder / "note.md").write_text("Tight cropping and cold palettes feel sterile.")
            run(home, "init", "--cadence", "3d")
            out = run(home, "ingest", "folder", str(folder))
            self.assertIn("creative director", out); self.assertIn("LOOK at the image", out)
            items = json.loads(run(home, "--json", "prompt"))["items"]
            self.assertEqual(len(items), 5)
            lines = "\n".join(json.dumps({"id": i["id"], "description": "retro crt neon scanlines poster" if "crt" in i["ref"] else "soft ghibli forest green painterly" if "ghibli" in i["ref"] else "minimal stickman black white comic"}) for i in items)
            run(home, "describe", "--stdin", stdin=lines)
            self.assertEqual(json.loads(run(home, "--json", "prompt"))["pending"], 0)
            self.assertIn("clusters", run(home, "canon"))
            # re-ingest is idempotent
            self.assertIn("added 0", run(home, "ingest", "folder", str(folder), "--no-prompt"))
            piece = json.loads(run(home, "--json", "log", "--request", "retro crt thumbnail", "--purpose", "thumbnail", "--text", "neon scanline cover"))["piece"]
            v = json.loads(run(home, "--json", "verdict", "--piece", piece, "--down"))
            self.assertTrue(v["needs_followup"])
            run(home, "verdict", "--piece", piece, "--feedback", "palette too cold", "--aspect", "palette")
            b = json.loads(run(home, "--json", "brief", "--request", "retro crt thumbnail"))
            self.assertEqual(b["disliked"][0]["feedback"], "palette too cold")
            self.assertTrue(b["canon_nearest"])
            for i in range(20):
                p = json.loads(run(home, "--json", "log", "--request", f"r{i}", "--text", f"t{i}"))["piece"]
                run(home, "verdict", "--piece", p, "--up")
            self.assertTrue(json.loads(run(home, "--json", "brief", "--request", "x"))["distill_due"])
            self.assertIn("tastelikeme rubric set", run(home, "distill"))
            run(home, "rubric", "set", "--text", "- AVOID cold palettes")
            b = json.loads(run(home, "--json", "brief", "--request", "retro crt thumbnail"))
            self.assertIn("cold", b["rubric"]); self.assertFalse(b["distill_due"])
            self.assertFalse(json.loads(run(home, "--json", "due"))["due"])
            self.assertIn("TASTE BRIEF", run(home, "next", "--request", "a tweet", "--purpose", "tweet"))
            # append-only files exist
            for f in ("canon.jsonl", "verdicts.jsonl"):
                self.assertTrue((Path(home) / f).stat().st_size > 0)

    def test_x_export(self):
        with tempfile.TemporaryDirectory() as d:
            z = d + "/x.zip"
            with zipfile.ZipFile(z, "w") as zf:
                zf.writestr("data/like.js", 'window.YTD.like.part0 = [{"like":{"tweetId":"1","fullText":"clean grid layouts","expandedUrl":"https://x.com/a/status/1"}}]')
            home = d + "/s"; run(home, "init")
            self.assertIn("added 1", run(home, "ingest", "x-export", z, "--no-prompt"))

if __name__ == "__main__":
    unittest.main()
