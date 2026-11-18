import json, subprocess, sys, tempfile, pathlib

def run(home, *args):
    return subprocess.run([sys.executable, "-c", "import sys; from tastelikeme.cli import main; sys.exit(main(sys.argv[1:]))", "--home", str(home), "--json", "--non-interactive", *args],
                          capture_output=True, text=True)

def test_brief_exposes_local_image_paths():
    d = pathlib.Path(tempfile.mkdtemp()); img = d / "ref.png"
    img.write_bytes(b"\x89PNG\r\n\x1a\n" + b"0" * 64)
    home = d / "store"
    assert run(home, "init").returncode == 0
    assert run(home, "ingest", "folder", str(d), "--no-prompt").returncode == 0
    cid = json.loads((home / "canon.jsonl").read_text().splitlines()[0])["id"]
    assert run(home, "describe", "--id", cid, "--text", "test poster").returncode == 0
    img.unlink()  # store must keep its own copy
    b = json.loads(run(home, "brief", "--request", "poster").stdout)
    assert b["images"][0]["path"] and pathlib.Path(b["images"][0]["path"]).is_file()
