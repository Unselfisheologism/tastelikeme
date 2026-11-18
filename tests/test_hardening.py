import json, os, subprocess, sys, tempfile, unittest, zipfile
from pathlib import Path
from unittest.mock import patch
from tastelikeme import cli, embed, sources
from tastelikeme.store import Store, append

class Hardening(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.home = self.root / 'store'
        self.call('init')

    def call(self, *args, stdin=None, ok=True):
        p = subprocess.run([sys.executable, '-m', 'tastelikeme.cli', '--home', str(self.home), '--json', '--non-interactive', *args], input=stdin, text=True, capture_output=True, env=dict(os.environ, TASTELIKEME_EMBED='hash'))
        if ok:
            self.assertEqual(p.returncode, 0, p.stderr)
            return json.loads(p.stdout)
        self.assertNotEqual(p.returncode, 0, p.stdout)
        self.assertNotIn('Traceback', p.stderr)
        return p

    def test_ingest_json_is_one_document(self):
        f = self.root / 'files'; f.mkdir(); (f / 'x.md').write_text('warm red poster')
        r = self.call('ingest', 'folder', str(f))
        self.assertEqual(r['added'], 1)
        self.assertIn('creative director', r['prompt'])
        self.assertEqual(self.call('ingest', 'folder', str(f), '--no-prompt')['added'], 0)

    def test_config_invalid_does_not_mutate(self):
        before = (self.home / 'config.json').read_bytes()
        for value in ['distill_every=nope', 'distill_every=0', 'distill_every=-1', 'cadence=potato', 'cadence=0h', 'cadence=']:
            self.call('config', '--set', value, ok=False)
            self.assertEqual((self.home / 'config.json').read_bytes(), before)
        self.call('init', '--distill-every', '-1', ok=False)

    def test_strict_flags(self):
        self.call('status', '--garbage', ok=False)
        self.call('log', '--text', 'missing request', ok=False)
        p = self.call('log', '--id', 'a', '--request', 'poster', '--text', 'red')['piece']
        self.call('verdict', '--piece', p, '--up', '--down', ok=False)
        self.call('log', '--request', 'poster', '--text', 'red', '--file', 'missing.png', ok=False)
        self.call('log', '--request', 'poster', '--file', 'missing.png', ok=False)
        self.call('log', '--id', 'a', '--request', 'changed', '--text', 'blue', ok=False)
        self.call('canon', '--k', '0', ok=False)
        self.call('brief', '--n', '-1', ok=False)

    def test_describe_validation_is_atomic(self):
        f = self.root / 'files'; f.mkdir(); (f / 'x.txt').write_text('hello')
        self.call('ingest', 'folder', str(f), '--no-prompt')
        iid = self.call('prompt')['items'][0]['id']
        before = (self.home / 'canon.jsonl').read_bytes()
        for text in ['{bad}', '{}', json.dumps({'id': iid, 'description': 4}), json.dumps({'id': iid, 'description': ''}), json.dumps({'id': 'unknown', 'description': 'x'})]:
            self.call('describe', '--stdin', stdin=text, ok=False)
            self.assertEqual((self.home / 'canon.jsonl').read_bytes(), before)
        self.call('describe', '--stdin', stdin=json.dumps({'id': iid, 'description': 'red grid'})+'\n{}', ok=False)
        self.assertEqual((self.home / 'canon.jsonl').read_bytes(), before)

    def test_log_ids_unique_even_same_second(self):
        ids = [self.call('log', '--request', 'same', '--text', 'same')['piece'] for _ in range(8)]
        self.assertEqual(len(set(ids)), 8)
        self.assertEqual(self.call('status')['generations'], 8)

    def test_exact_twenty_verdict_trigger_and_amend(self):
        for i in range(20):
            p = self.call('log', '--id', str(i), '--request', 'red poster', '--text', 'red grid')['piece']
            v = self.call('verdict', '--piece', p, '--down' if i == 0 else '--up')
            self.assertEqual(v['distill_due'], i == 19)
            if i == 0:
                self.assertTrue(v['needs_followup'])
                self.call('verdict', '--piece', p, '--feedback', 'too cold', '--aspect', 'palette')
        b = self.call('brief', '--request', 'red poster')
        self.assertEqual(b['verdicts_total'], 20)
        self.assertEqual(b['disliked'][0]['feedback'], 'too cold')
        self.assertTrue(b['distill_due'])
        self.assertEqual(self.call('distill')['new_verdicts'], 20)
        self.call('rubric', 'set', '--text', '- Prefer warm red')
        self.assertFalse(self.call('brief')['distill_due'])
        self.assertEqual(self.call('distill')['new_verdicts'], 0)
        self.assertEqual(self.call('distill', '--all')['new_verdicts'], 20)
        self.call('verdict', '--piece', '0', '--up')
        self.assertEqual(self.call('distill')['new_verdicts'], 1)

    def test_folder_nested_empty_weird_files(self):
        f = self.root / 'files'; f.mkdir(); (f/'nested').mkdir(); (f/'empty').mkdir()
        (f/'ignore.bin').write_bytes(b'\xff\x00'); (f/'nested'/'snow .txt').write_text('snow')
        (f/'README.MD').write_text('grid'); (f/'.hidden.txt').write_text('skip')
        self.assertEqual(self.call('ingest', 'folder', str(f), '--no-prompt')['added'], 2)
        self.assertEqual(self.call('ingest', 'folder', str(f/'empty'), '--no-prompt')['added'], 0)
        self.call('ingest', 'folder', str(f/'missing'), ok=False)

    def test_arena_exact_cap(self):
        data = {'contents': [{'class': 'Text', 'id': i, 'content': 'x'} for i in range(25)]}
        for cap in [0, 1, 25, 26]:
            with patch.dict(os.environ, TASTELIKEME_ARENA_MAX=str(cap)), patch.object(sources, '_get', return_value=json.dumps(data)):
                self.assertEqual(len(list(sources.arena('slug'))), cap)

    def _pin_row(self, pid, **kw):
        fields = {"Title": "No data", "Details": "No data", "Image": "No data",
                  "Board Name": "No data", "Alive": "True", "Private": "False"}
        fields.update(kw)
        rows = [f'<a href="https://www.pinterest.com/pin/{pid}/">https://www.pinterest.com/pin/{pid}/</a>']
        for k, v in fields.items():
            if k == "Canonical Link":
                rows.append(f'<br>Canonical Link: <a href="{v}">{v}</a>,')
            else:
                rows.append(f"<br>{k}: {v},")
        return "\n".join(rows)

    def test_pinterest_export_full_parse(self):
        h1_, h2 = '012ed7a4306970554ea9ab2ecdb614cb', '871591992af53cb9ada62ae6ab404604'
        doc = ('<div id="contents"><h1 id=y1bkd>Pins</h1>'
               + self._pin_row('1', Title='Warm poster', Details='red grid swiss typography', Image=h1_,
                               **{'Board Name': 'Posters', 'Alt Text': 'red poster', 'Canonical Link': 'https://example.com/warm'})
               + self._pin_row('2', Image=h2, **{'Board Name': 'Secret stuff', 'Private': 'True'})
               + self._pin_row('3', Details='moody short film', **{'Is Video': 'Yes', 'Canonical Link': 'https://example.com/film'})
               + self._pin_row('4', Image='c6292f772df53483d96351352e7ce646', Alive='False')
               + '<h1 id=z>Boards</h1></div>')
        z = self.root / 'pinterest-data.zip'
        with zipfile.ZipFile(z, 'w') as f:
            f.writestr('Pinterest Data/Pins.html', doc)
            f.writestr('Pinterest Data/Profile.html', '<h1>Profile</h1>')
        self.assertEqual(self.call('ingest', 'pinterest-export', str(z), '--no-prompt')['added'], 3)
        by_ref = {i['ref']: i for i in Store(self.home).canon().values()}
        u1 = f'https://i.pinimg.com/736x/{h1_[0:2]}/{h1_[2:4]}/{h1_[4:6]}/{h1_}.jpg'
        item = by_ref[u1]
        self.assertEqual(item['kind'], 'image')
        self.assertEqual(item['title'], 'Warm poster')
        self.assertEqual(item['origin'], 'pinterest-export:Posters')
        self.assertIn('red grid swiss typography', item['text'])
        self.assertIn('Alt text: red poster', item['text'])
        self.assertIn('Board: Posters', item['text'])
        secret = by_ref[f'https://i.pinimg.com/736x/{h2[0:2]}/{h2[2:4]}/{h2[4:6]}/{h2}.jpg']
        self.assertEqual(secret['origin'], 'pinterest-export:Secret stuff')
        self.assertEqual(by_ref['https://example.com/film']['kind'], 'link')
        self.assertFalse(any('c6292f77' in r for r in by_ref))  # dead pin skipped
        self.assertEqual(self.call('ingest', 'pinterest-export', str(z), '--no-prompt')['added'], 0)

    def test_pinterest_export_bare_html_and_board_merge(self):
        img = '24d7f286a8130e45ae9828642da4cca8'
        doc = ('<h1>Pins</h1>'
               + self._pin_row('1', Image=img, Title='grid', **{'Board Name': 'A'})
               + self._pin_row('2', Image=img, **{'Board Name': 'B'})
               + self._pin_row('3', Image='notahexhash', Title='weird')
               + '<h1>Boards</h1>')
        htmlp = self.root / 'pins.html'
        htmlp.write_text(doc)
        items = list(sources.pinterest_export(str(htmlp)))
        self.assertEqual(len(items), 2)
        merged = next(i for i in items if i['kind'] == 'image')
        self.assertEqual(merged['origin'], 'pinterest-export:A / B')
        self.assertIn('Board: A / B', merged['text'])
        weird = next(i for i in items if i['kind'] == 'link')
        self.assertEqual(weird['ref'], 'https://www.pinterest.com/pin/3/')
        self.assertEqual(weird['title'], 'weird')

    def test_pinterest_export_bad_inputs_are_clean(self):
        before = (self.home / 'canon.jsonl').read_bytes()
        (self.root / 'notzip.zip').write_text('hello')
        self.call('ingest', 'pinterest-export', str(self.root / 'notzip.zip'), ok=False)
        with zipfile.ZipFile(self.root / 'empty.zip', 'w') as f:
            f.writestr('readme.txt', 'no html here')
        self.call('ingest', 'pinterest-export', str(self.root / 'empty.zip'), ok=False)
        with zipfile.ZipFile(self.root / 'nopins.zip', 'w') as f:
            f.writestr('profile.html', '<h1>Profile</h1>')
        self.call('ingest', 'pinterest-export', str(self.root / 'nopins.zip'), ok=False)
        self.call('ingest', 'pinterest-export', str(self.root / 'missing.zip'), ok=False)
        self.call('ingest', 'pinterest-export', str(self.root), ok=False)
        self.assertEqual((self.home / 'canon.jsonl').read_bytes(), before)

    def test_x_likes_bookmarks_and_malformed(self):
        z = self.root/'x.zip'
        with zipfile.ZipFile(z,'w') as f:
            f.writestr('archive/data/like.js', 'window.YTD.like.part0 = [{"like":{"tweetId":"1","fullText":"warm"}}]')
            f.writestr('archive/data/bookmark.js', 'window.YTD.bookmark.part0 = [{"bookmark":{"tweetId":"2","fullText":"cool"}}]')
        self.assertEqual(self.call('ingest','x-export',str(z),'--no-prompt')['added'],2)
        (self.root/'bad.zip').write_text('not zip')
        self.call('ingest','x-export',str(self.root/'bad.zip'),ok=False)

    def test_labels_do_not_change_retrieval(self):
        f=self.root/'files';f.mkdir()
        for i,text in enumerate(['red grid poster','red typography poster','green forest watercolor','green forest leaves','monochrome ink line','black ink lines']):
            (f/f'{i}.txt').write_text(text)
        self.call('ingest','folder',str(f),'--no-prompt')
        for item in self.call('prompt')['items']:
            self.call('describe','--id',item['id'],'--text',item['text'])
        b=self.call('brief','--request','red poster')
        key=b['cluster']['key']
        self.call('label',key,'TEST DISPLAY ONLY')
        after=self.call('brief','--request','red poster')
        self.assertEqual(b['canon_nearest'],after['canon_nearest'])
        self.assertEqual(after['cluster']['label'],'TEST DISPLAY ONLY')
        self.assertEqual(self.call('brief','--request','red poster')['cluster'],after['cluster'])

    def test_fallback_never_mixes_dimensions(self):
        s=Store(self.home)
        # Simulate a cached ONNX vector and a later model failure.
        import hashlib
        append(s.vec_p, {'model':'bge-small-en-v1.5','h':hashlib.sha1(b'old').hexdigest(),'v':[1.0]+[0.0]*383})
        with patch.object(embed,'backend',return_value='bge-small-en-v1.5'), patch.object(embed,'embed_texts',return_value=('hash',[embed._hash_embed('new')])):
            vs=cli.vec_for(s,['old','new'])
        self.assertEqual(len(vs['old']),len(vs['new']))
        with self.assertRaises(ValueError): embed.cos([1,0],[1,0,0])

    def test_corrupt_history_is_not_silently_skipped(self):
        with (self.home/'canon.jsonl').open('a') as f: f.write('{bad}\n')
        p=self.call('status',ok=False)
        self.assertIn('line 1',p.stderr)

    def test_cluster_rubric_does_not_clear_global_reminder(self):
        f=self.root/'files';f.mkdir();(f/'a.txt').write_text('red grid')
        self.call('ingest','folder',str(f),'--no-prompt')
        key=self.call('canon')['clusters'][0]['cluster']
        self.call('config','--set','distill_every=1')
        self.call('log','--id','x','--request','red','--text','red')
        self.call('verdict','--piece','x','--up')
        self.call('rubric','set','--cluster',key,'--text','scoped')
        self.assertTrue(self.call('brief')['distill_due'])
        self.assertEqual(self.call('rubric','show')['text'],None)
        self.assertEqual(self.call('rubric','show','--cluster',key)['text'],'scoped')
        self.assertEqual(self.call('distill')['new_verdicts'],1)
        self.assertEqual(self.call('distill','--cluster',key)['new_verdicts'],0)

    def test_fallback_during_query_restarts_brief(self):
        s=Store(self.home)
        append(s.canon_p,{'op':'add','id':'x','kind':'text','ref':'x','text':'old','description':'old'})
        import hashlib
        append(s.vec_p,{'model':'bge-small-en-v1.5','h':hashlib.sha1(b'old').hexdigest(),'v':[1.0]+[0.0]*383})
        with patch.object(embed,'backend',return_value='bge-small-en-v1.5'), patch.object(embed,'embed_texts',return_value=('hash',[embed._hash_embed('new')])):
            b=cli.make_brief(s,'new',None,3)
        self.assertEqual(len(b['canon_nearest']),1)
        self.assertEqual(cli.active_backend(s),'hash')

if __name__ == '__main__': unittest.main()


class CriticLoop(Hardening):
    def test_critic_loop_states(self):
        img = self.root / 'p.png'; img.write_bytes(b'x')
        tgt = self.root / 't.png'; tgt.write_bytes(b'x')
        for i in range(1, 5): self.call('log', '--request', 'z', '--file', str(img), '--id', f'p{i}')
        pr = self.call('critic', 'prompt', '--piece', 'p1', '--target', str(tgt), '--slot', 'Z')
        self.assertEqual(pr['round'], 1); self.assertIn('fresh context', pr['prompt'])
        sc = lambda c, p, t, o, s_: json.dumps(dict(composition=c, palette_material=p, type=t, object=o, story=s_))
        r = self.call('critic', 'record', '--piece', 'p1', '--slot', 'Z', '--scores', sc(1, 1, 1, 0, 0), '--gap', 'flat boxes')
        self.assertEqual((r['total'], r['state']), (3.0, 'continue'))
        pr = self.call('critic', 'prompt', '--piece', 'p2', '--target', str(tgt), '--slot', 'Z')
        self.assertIn('PREVIOUS ROUND', pr['prompt'])
        r = self.call('critic', 'record', '--piece', 'p2', '--slot', 'Z', '--scores', sc(2, 1, 1, 0, 0), '--gap', 'Flat boxes!')
        self.assertEqual(r['state'], 'stall')
        r = self.call('critic', 'record', '--piece', 'p3', '--slot', 'Z', '--scores', sc(3, 3, 2, 1, 0.5), '--gap', 'copy thin', '--rethought')
        self.assertEqual(r['state'], 'pass')
        self.call('critic', 'record', '--piece', 'p4', '--slot', 'Z', '--scores', sc(4, 0, 0, 0, 0), '--gap', 'x', ok=False)
        self.call('critic', 'record', '--piece', 'p4', '--slot', 'Z', '--scores', sc(1, 1, 1, 0, 0), ok=False)
        self.assertEqual(len(self.call('critic', 'show', '--slot', 'Z')['rounds']), 3)
        self.assertIn('flat boxes', json.dumps(self.call('critic', 'show')['recurring_gaps']).lower())
