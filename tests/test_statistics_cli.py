"""Real CLI/atomic-output integration checks. stdlib unittest, no test install needed."""
import csv
import hashlib
from html.parser import HTMLParser
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import xml.etree.ElementTree as ET

from poi_audit.common import AuditError
from poi_audit.cli import run_statistics

PROJECT=Path(__file__).resolve().parents[1]

class References(HTMLParser):
    def __init__(self):
        super().__init__(); self.refs=[]; self.scripts=0
    def handle_starttag(self,tag,attrs):
        self.scripts += tag == 'script'
        self.refs.extend(v for k,v in attrs if k in {'src','href'})

class StatisticsCLITests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(); self.base=Path(self.temp.name)
        self.addCleanup(self.temp.cleanup)
        (self.base/'metrics.csv').write_text('id,g,x,y\na,A,1,2\na,A,2,4\n,B,3,6\nd,B,4,8\ne,B,,9\nf,A,2026-01-01,3\n',encoding='utf-8')
        axis=lambda f:{'field':f,'label':f,'unit':'units'}
        self.c={'schema_version':1,'kind':'statistical_plots','title':'Demo <script>bad()</script>', 'synthetic':True,'input':{'path':'metrics.csv','record_unit':'one fictional sample','id_field':'id'},'plots':[
            {'id':'scatter','type':'scatter','x':axis('x'),'y':axis('y'),'group_field':'g','fit':'ols','correlation':'spearman'},
            {'id':'raincloud','type':'half_violin','value':axis('y'),'group_field':'g'},
            {'id':'quadrant','type':'quadrant','x':axis('x'),'y':axis('y'),'thresholds':{'x':{'method':'median'},'y':{'method':'median'}}},]}
        self.config=self.base/'config.json'; self.config.write_text(json.dumps(self.c),encoding='utf-8')
    def invoke(self,*args):
        env=os.environ.copy();env['PYTHONPATH']=str(PROJECT/'src')+os.pathsep+env.get('PYTHONPATH','')
        return subprocess.run([sys.executable,'-m','poi_audit',*map(str,args)],cwd=self.base,env=env,capture_output=True,text=True,timeout=60)
    def digest(self,p):return hashlib.sha256(p.read_bytes()).hexdigest()
    def test_real_cli_validate_read_only(self):
        before={p.name:self.digest(p) for p in self.base.iterdir()}
        result=self.invoke('stats-validate','--config',self.config)
        self.assertEqual(result.returncode,0,result.stderr)
        data=json.loads(result.stdout);self.assertEqual(data['validation'],'passed')
        self.assertEqual(data['plots'][0]['n_included'],4)
        self.assertEqual(before,{p.name:self.digest(p) for p in self.base.iterdir()})
    def test_real_cli_bundle_formats_hashes_offline_links_and_input_immutability(self):
        input_hash=self.digest(self.base/'metrics.csv');out=self.base/'out'
        result=self.invoke('stats','--config',self.config,'--output',out)
        self.assertEqual(result.returncode,0,result.stderr)
        manifest=json.loads((out/'manifest.json').read_text())
        self.assertEqual(manifest['workflow'],'statistical_plots'); self.assertEqual(manifest['version'],'0.3.0')
        self.assertEqual(manifest['human_visual_review'],'not_recorded')
        self.assertEqual(input_hash,self.digest(self.base/'metrics.csv'))
        self.assertEqual(manifest['config_sha256'],self.digest(self.config))
        self.assertEqual(set(manifest['output_sha256']),{p.name for p in out.iterdir()}-{'manifest.json'})
        for name,sha in manifest['output_sha256'].items():self.assertEqual(self.digest(out/name),sha,name)
        for pid in ['scatter','raincloud','quadrant']:
            self.assertTrue((out/f'stats_{pid}.png').read_bytes().startswith(b'\x89PNG\r\n\x1a\n'))
            self.assertTrue((out/f'stats_{pid}.pdf').read_bytes().startswith(b'%PDF-'))
            self.assertTrue(ET.parse(out/f'stats_{pid}.svg').getroot().tag.endswith('svg'))
            counts=manifest['counts'][pid];self.assertEqual(counts['n_raw'],counts['n_included']+counts['n_excluded'])
        html=(out/'report.html').read_text();parser=References();parser.feed(html)
        self.assertEqual(parser.scripts,0); self.assertIn('&lt;script&gt;',html)
        self.assertTrue(parser.refs);self.assertTrue(all((out/ref).is_file() for ref in parser.refs))
        self.assertTrue(all(not ref.startswith(('http:','https:','data:','//')) for ref in parser.refs))
    def test_nonempty_directory_and_file_refused(self):
        for kind in ('directory','file'):
            with self.subTest(kind=kind):
                out=self.base/kind
                if kind=='directory':out.mkdir();(out/'important').write_text('keep')
                else:out.write_text('keep')
                result=self.invoke('stats','--config',self.config,'--output',out)
                self.assertEqual(result.returncode,2);self.assertIn('Output exists',result.stderr);self.assertNotIn('Traceback',result.stderr)
                self.assertEqual((out/'important' if kind=='directory' else out).read_text(),'keep')
    def test_render_failure_is_atomic(self):
        out=self.base/'atomic'
        with patch('poi_audit.statistics_render.render_statistics',side_effect=AuditError('Deliberate render error')):
            with self.assertRaisesRegex(AuditError,'Deliberate'):run_statistics(self.config,out)
        self.assertFalse(out.exists());self.assertEqual(list(self.base.glob('.poi-statistics-*')),[])
    def test_concurrent_output_is_not_overwritten(self):
        out=self.base/'concurrent'
        def render(*args):
            out.mkdir();(out/'important').write_text('keep');return {'fonts':{'warnings':[]},'output_stems':[],'plots':[]}
        with patch('poi_audit.statistics_render.render_statistics',side_effect=render):
            with self.assertRaisesRegex(AuditError,'Output changed'):run_statistics(self.config,out)
        self.assertEqual([p.name for p in out.iterdir()],['important']);self.assertEqual((out/'important').read_text(),'keep')
        self.assertEqual(list(self.base.glob('.poi-statistics-*')),[])
    def test_bad_config_actionable(self):
        self.c['plots'][0]['x']['field']='unknown';self.config.write_text(json.dumps(self.c))
        result=self.invoke('stats','--config',self.config,'--output',self.base/'none')
        self.assertEqual(result.returncode,2);self.assertNotIn('Traceback',result.stderr);self.assertFalse((self.base/'none').exists())
    def test_help_version_and_all_empty_output(self):
        for opt in ['--help','--version']:
            result=self.invoke(opt);self.assertEqual(result.returncode,0,result.stderr)
        (self.base/'metrics.csv').write_text('id,g,x,y\n')
        result=self.invoke('stats','--config',self.config,'--output',self.base/'empty')
        self.assertEqual(result.returncode,0,result.stderr)
        manifest=json.loads((self.base/'empty'/'manifest.json').read_text())
        self.assertTrue(all(c['n_included']==0 for c in manifest['counts'].values()))

if __name__=='__main__':unittest.main()
