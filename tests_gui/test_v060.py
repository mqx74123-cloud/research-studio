"""Numerical and integration regression checks for the offline release."""
import copy,csv,json,tempfile,time,unittest
from pathlib import Path
from gui.server import App,build_config
from gui.projects import save_project,load_project
from gui.research import prepare,run
from gui.table_import import InputError
from poi_audit.exporting import validate_export
from PIL import Image

class ResearchTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.base=Path(self.tmp.name);self.app=App(self.base/'work')
 def tearDown(self):self.tmp.cleanup()
 def request(self,chart='heatmap',data='label,group,a,b,lo,hi\nx,G1,1,2,0,2\ny,G1,2,4,1,3\nz,G2,3,,2,4\n',**extra):
  info=self.app.upload('csv',data.encode('utf-8'))
  fields={'heatmap':{'label':'label'},'forest':{'label':'label','estimate':'a','lower':'lo','upper':'hi'},'dumbbell':{'label':'label','before':'a','after':'b'},'multi_raincloud':{'group':'group'}}[chart]
  return dict(mode='research',source='upload',csv_id=info['id'],mapping_confirmed=True,chart=chart,record_unit='一条合成观测',fields=fields,metrics=['a','b'],interval_label='合成测试区间',export={'width_mm':180,'dpi':100,'svg_text':'editable'},**extra)
 def test_pairwise_correlation_and_missing(self):
  c,_=build_config(self.request(color_mode='correlation'),self.app.uploads);b=prepare(c,self.base)
  pairs={(r['row_variable'],r['column_variable']):r for r in b['summaries']}
  self.assertEqual(pairs['a','b']['n'],2);self.assertAlmostEqual(pairs['a','b']['pearson_r'],1)
  self.assertEqual(len(b['exclusions']),1)
 def test_heatmap_all_missing_row_is_visible(self):
  c,_=build_config(self.request(data='label,a,b\nx,1,2\ny,,\n'),self.app.uploads);b=prepare(c,self.base)
  self.assertEqual(b['arrays']['matrix'].shape,(2,2));self.assertEqual(b['labels'],['x','y'])
 def test_constant_correlation_is_undefined(self):
  c,_=build_config(self.request(data='label,a,b\nx,1,1\ny,1,2\n',color_mode='correlation'),self.app.uploads)
  b=prepare(c,self.base);self.assertIsNone(b['summaries'][0]['pearson_r'])
 def test_interval_reversed_rejected(self):
  c,_=build_config(self.request('forest',data='label,a,lo,hi\nx,1,2,0\n'),self.app.uploads)
  with self.assertRaisesRegex(InputError,'区间错误'):prepare(c,self.base)
 def test_duplicate_heatmap_label_rejected(self):
  c,_=build_config(self.request(data='label,a,b\nx,1,2\nx,3,4\n'),self.app.uploads)
  with self.assertRaisesRegex(InputError,'标签重复'):prepare(c,self.base)
 def test_relocated_project_snapshot(self):
  request=self.request();p=save_project(self.app,{'request':request,'name':'研究方案'})
  import shutil
  moved=self.base/'moved';shutil.copytree(self.app.work,moved)
  self.tmp_source=self.app.work
  restored=App(moved);loaded=load_project(restored,{'id':p['id']});c,_=build_config(loaded['request'],restored.uploads)
  self.assertTrue(Path(c['input']['path']).resolve().is_relative_to(moved.resolve()));self.assertEqual(len(prepare(c,self.base)['rows']),5)
 def test_complete_job_and_restart_history(self):
  job=self.app.submit(self.request('forest'));deadline=time.time()+90
  while time.time()<deadline and not (self.app.jobs[job['id']]['folder']/'status.json').exists():time.sleep(.05)
  while time.time()<deadline and self.app.jobs[job['id']]['state'] in {'queued','validating','running'}:time.sleep(.1)
  current=self.app.jobs[job['id']];self.assertEqual(current['state'],'complete',self.app.public(current))
  for _ in range(30):
   if App(self.app.work).jobs.get(job['id'],{}).get('state')=='complete':break
   time.sleep(.1)
  self.assertEqual(App(self.app.work).jobs[job['id']]['state'],'complete')
 def test_four_templates_and_publication_outputs(self):
  for chart in ['heatmap','forest','dumbbell','multi_raincloud']:
   with self.subTest(chart=chart):
    c,_=build_config(self.request(chart),self.app.uploads);p=self.base/(chart+'.json');p.write_text(json.dumps(c,ensure_ascii=False),encoding='utf-8')
    out=self.base/chart;run(p,out);stem='research_'+chart
    with Image.open(out/(stem+'.png')) as im:self.assertLessEqual(abs(im.width-180/25.4*100),1)
    import xml.etree.ElementTree as ET
    tree=ET.parse(out/(stem+'.svg'));self.assertGreater(len(tree.findall('.//{http://www.w3.org/2000/svg}text')),0)
    self.assertTrue((out/(stem+'.pdf')).read_bytes().startswith(b'%PDF'))
    manifest=json.loads((out/'manifest.json').read_text(encoding='utf-8'));self.assertEqual(manifest['counts']['input_rows'],3)
 def test_legacy_title_and_export_config(self):
  r=dict(mode='stats',source='demo',plots=['scatter'],fields={'x':'supply_example','y':'walk_example'},title='我的论文标题',record_unit='合成观测',export={'width_mm':180,'dpi':300,'svg_text':'editable'})
  c,_=build_config(r,{});self.assertEqual(c['plots'][0]['title'],'我的论文标题');self.assertEqual(c['export']['dpi'],300)
 def test_export_constraints(self):
  for value in [{'dpi':float('nan')},{'width_mm':5},{'svg_text':'bad'}]:
   with self.assertRaises(ValueError):validate_export(value)

if __name__=='__main__':unittest.main()
