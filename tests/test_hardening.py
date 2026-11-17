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
