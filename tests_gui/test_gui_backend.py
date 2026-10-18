"""Focused nonmutating GUI-adapter tests (unittest and pytest compatible).

Run without installing anything:
  python tests_gui/test_gui_backend.py

Optionally select another checkout using POI_GUI_ROOT. All writes use temporary
folders; production source, examples, outputs, and user inputs are untouched.
"""
from __future__ import annotations

import base64
import copy
import hashlib
import http.client
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
import zipfile

ROOT = Path(os.environ.get('POI_GUI_ROOT', str(Path(__file__).resolve().parents[1]))).resolve()
sys.path.insert(0, str(ROOT / 'src'))
spec = importlib.util.spec_from_file_location('poi_gui_test_server', ROOT / 'gui/server.py')
gui = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gui)

CSV = b'id,x,y,category,date\nA,116.42,39.92,one,2026-01-01\nB,116.43,39.93,two,2026-01-02\n'
BOUNDARY = {'type': 'Polygon', 'coordinates': [[[116.40,39.90],[116.48,39.90],[116.48,39.98],[116.40,39.98],[116.40,39.90]]]}


def gis_request(**updates):
    req = {'mode':'gis','source':'demo','style':'premium','mapping_confirmed':True,'record_unit':'一个合成测试观测点',
           'fields':{'x':'lon','y':'lat','id':'poi_id','category':'type','name':'name','date':'observed'},
           'crs':'EPSG:4326','boundary_crs':'EPSG:4326','map_crs':'EPSG:32650',
           'convention':'WGS84','boundary_convention':'WGS84','date_format':'%Y-%m-%d'}
    req.update(updates)
    return req


def stats_request(**updates):
    req = {'mode':'stats','source':'demo','style':'premium','mapping_confirmed':True,
           'fields':{'x':'supply_example','y':'walk_example','group':'sample_group','id':'observation_id'},
           'record_unit':'one synthetic observation','x_unit':'points','y_unit':'points',
           'plots':['scatter','half_violin','quadrant'],'transform':'raw'}
    req.update(updates)
    return req


class TempCase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='poi-gui-qa-')
        self.work = Path(self.temp.name)
        self.app = gui.App(self.work / 'work')

    def tearDown(self):
        self.temp.cleanup()

    def upload_pair(self):
        csv_id = self.app.upload('csv', CSV, 'utf-8-sig', ',')['id']
        boundary_id = self.app.upload('boundary', json.dumps(BOUNDARY).encode(), 'utf-8-sig', ',')['id']
        return csv_id, boundary_id

    def uploaded_gis(self, **updates):
        csv_id, boundary_id = self.upload_pair()
        return gis_request(source='upload',csv_id=csv_id,boundary_id=boundary_id,
                           fields={'x':'x','y':'y','id':'id','category':'category','date':'date'},
                           crs_confirmed=True,**updates)

    def config_path(self, config):
        path = self.work / 'config.json'
        path.write_text(json.dumps(config), encoding='utf-8')
        return path


class ConfigTests(TempCase):
    def test_gis_demo_matches_engine_schema(self):
        from poi_audit.core import load_config, load_boundary
        c, info = gui.build_config(gis_request(), {})
        loaded, base = load_config(self.config_path(c))
        self.assertTrue(loaded['synthetic'])
        self.assertGreater(info['rows'], 0)
        self.assertTrue(load_boundary(loaded, base).is_valid)
        self.assertEqual(loaded['inputs'][0]['date_format'], '%Y-%m-%d')

    def test_uploaded_gis_matches_engine_schema(self):
        from poi_audit.core import load_config, load_boundary, read_rows
        c, info = gui.build_config(self.uploaded_gis(), self.app.uploads)
        loaded, base = load_config(self.config_path(c))
        self.assertFalse(loaded['synthetic'])
        self.assertEqual(info['rows'], 2)
        self.assertTrue(load_boundary(loaded, base).is_valid)
        self.assertEqual(len(read_rows(loaded, base)[0]), 2)

    def test_gis_rejects_invalid_user_mapping_and_declarations(self):
        cases = [
            {'fields': {'x':'lon','y':'lon'}},
            {'fields': {'x':'not_a_column','y':'lat'}},
            {'fields': {'x':'lon'}},
            {'fields': []}, {'crs':''}, {'boundary_crs':''}, {'map_crs':''},
            {'convention':'GCJ-02'}, {'convention':'BD-09'}, {'boundary_convention':'GCJ-02'},
            {'style':'bad'}, {'mode':'bad'}, {'source':'bad'},
            {'kde':True,'bandwidth':0}, {'kde':True,'cell':'NaN'},
            {'kde':True,'bandwidth':'Infinity'}, {'title':'a\x00b'},
        ]
        for updates in cases:
            with self.subTest(updates=updates), self.assertRaises(gui.InputError):
                gui.build_config(gis_request(**updates), {})

    def test_upload_requires_explicit_crs_confirmation_and_own_boundary(self):
        req = self.uploaded_gis()
        for changes in [{'crs_confirmed':False},{'crs_confirmed':'true'},{'boundary_id':''},{'csv_id':''}]:
            with self.subTest(changes=changes), self.assertRaises(gui.InputError):
                gui.build_config({**req,**changes}, self.app.uploads)
        self.assertNotIn(str(gui.ROOT/'examples/boundary.geojson'), str(gui.build_config(req,self.app.uploads)[0]))

    def test_engine_rejects_semantically_invalid_crs_configs(self):
        from poi_audit.core import load_config
        from poi_audit.common import AuditError
        cases = [
            {'crs':'bogus:crs'}, {'crs':'EPSG:3857','convention':'WGS84'},
            {'crs':'EPSG:4326','convention':'CRS_NATIVE'},
            {'boundary_crs':'EPSG:3857','boundary_convention':'WGS84'},
            {'map_crs':'EPSG:4326'}, {'map_crs':'EPSG:2263'},
            {'crs':'EPSG:4979','convention':'CRS_NATIVE'},
        ]
        for updates in cases:
            with self.subTest(updates=updates):
                try:
                    c,_ = gui.build_config(gis_request(**updates), {})
                except gui.InputError:
                    continue
                with self.assertRaises(AuditError):
                    load_config(self.config_path(c))

    def test_stats_all_plot_combinations_match_engine_schema(self):
        from poi_audit.statistics import load_statistics_config
        for plots in [['scatter'],['half_violin'],['quadrant'],['scatter','half_violin','quadrant']]:
            for transform in ['raw','zscore']:
                with self.subTest(plots=plots,transform=transform):
                    c, info = gui.build_config(stats_request(plots=plots,transform=transform,ols=True,correlation=True), {})
                    loaded,_ = load_statistics_config(self.config_path(c))
                    self.assertEqual(len(loaded['plots']),len(plots))
                    self.assertEqual(loaded['kind'],'statistical_plots')
                    self.assertGreater(info['rows'],0)
                    self.assertNotIn('inputs',loaded)
                    self.assertNotIn('boundary',loaded)

    def test_stats_fixed_thresholds_and_user_labels(self):
        c,_ = gui.build_config(stats_request(plots=['quadrant'], threshold='fixed',x_threshold='1.5',y_threshold='-2',x_label='Supply',y_label='Walking'),{})
        self.assertEqual(c['plots'][0]['thresholds'], {'x':{'method':'fixed','value':1.5},'y':{'method':'fixed','value':-2.0}})
        self.assertEqual(c['plots'][0]['x']['label'],'Supply')

    def test_stats_half_violin_does_not_require_x(self):
        c,_ = gui.build_config(stats_request(plots=['half_violin'],fields={'y':'walk_example','group':'sample_group'}),{})
        self.assertEqual(c['plots'][0]['value']['field'],'walk_example')
        self.assertNotIn('x',c['plots'][0])

    def test_stats_invalid_fields_methods_and_observation_units(self):
        cases = [
            {'record_unit':''},{'record_unit':'  '},{'plots':[]},{'plots':['bar']},
            {'plots':'scatter'},{'fields':{'x':'walk_example','y':'walk_example'}},
            {'fields':{'x':'supply_example','y':'walk_example'}},
            {'fields':{'x':'supply_example','group':'sample_group'}},
            {'transform':'log'},{'threshold':'wrong'},
            {'threshold':'fixed','x_threshold':'NaN','y_threshold':2},
        ]
        for updates in cases:
            with self.subTest(updates=updates), self.assertRaises(gui.InputError):
                gui.build_config(stats_request(**updates),{})

    def test_stats_blank_axis_units_use_explicit_unknown_marker(self):
        # Engine requires nonempty units. The GUI must never guess a physical unit.
        from poi_audit.statistics import load_statistics_config
        for updates in [{'x_unit':''},{'y_unit':''},{'x_unit':'   '},{'y_unit':None}]:
            with self.subTest(updates=updates):
                c,_ = gui.build_config(stats_request(**updates),{})
                load_statistics_config(self.config_path(c))
                axis = next(iter(updates)).split('_')[0]
                self.assertEqual(c['plots'][0][axis]['unit'],'未声明单位')

    def test_uploaded_stats_and_semicolon_normalization(self):
        csv_id = self.app.upload('csv',b'id,x,y,category\nA,1,2,G\nB,2,3,H\n','utf-8-sig',',')['id']
        req = stats_request(source='upload',csv_id=csv_id,fields={'x':'x','y':'y','id':'id','group':'category'})
        c,info = gui.build_config(req,self.app.uploads)
        self.assertFalse(c['synthetic']); self.assertEqual(info['rows'],2)
        sid = self.app.upload('csv',b'x;y;category\n1;2;G\n','utf-8-sig',';')['id']
        c,info=gui.build_config(stats_request(source='upload',csv_id=sid,delimiter=';',fields={'x':'x','y':'y','group':'category'}),self.app.uploads)
        self.assertEqual(info['rows'],1)
        self.assertEqual(c['input']['encoding'],'utf-8-sig')


class UploadTests(TempCase):
    def test_csv_bom_encoding_delimiter_and_multiline(self):
        cases=[('utf-8-sig',',','甲,乙\n1,2\n'),('gb18030',',','甲,乙\n1,2\n'),('utf-8-sig','\t','a\tb\n1\t2\n'),('utf-8-sig',';','a;b\n1;2\n'),('utf-8-sig',',','a,b\n"one\ntwo",2\n')]
        for encoding,delimiter,content in cases:
            with self.subTest(encoding=encoding,delimiter=delimiter):
                info=self.app.upload('csv',content.encode(encoding),encoding,delimiter)
                self.assertEqual(info['rows'],1); self.assertEqual(len(info['headers']),2)

    def test_csv_limits_and_invalid_inputs_leave_no_uploaded_files(self):
        bad=[b'',b'a,b\n',b'a,a\n1,2\n',b',b\n1,2\n',b'a\x00,b\n1,2\n',b'a,b\n"unterminated,2\n',b'\xff\xff']
        for data in bad:
            with self.subTest(data=data),self.assertRaises((gui.InputError,UnicodeError)):
                self.app.upload('csv',data,'utf-8-sig',',')
        self.assertEqual(self.app.uploads,{})
        self.assertEqual(list((self.app.work/'inputs').glob('*')),[])
        for encoding,delimiter in [('latin-1',','),('utf-8-sig','|')]:
            with self.subTest(encoding=encoding,delimiter=delimiter),self.assertRaises(gui.InputError):
                self.app.upload('csv',CSV,encoding,delimiter)
        with patch.object(gui,'MAX_BYTES',10),self.assertRaises(gui.InputError):
            self.app.upload('csv',CSV,'utf-8-sig',',')
        with patch.object(gui,'MAX_ROWS',1),self.assertRaises(gui.InputError):
            self.app.upload('csv',CSV,'utf-8-sig',',')
        many=','.join('col'+str(i) for i in range(201))+'\n'+','.join('1' for _ in range(201))+'\n'
        with self.assertRaises(gui.InputError):
            self.app.upload('csv',many.encode(),'utf-8-sig',',')

    def test_samples_are_limited_but_rows_are_not_silently_sampled(self):
        data='a,b\n'+''.join(f'{i},{"x"*250}\n' for i in range(12))
        info=self.app.upload('csv',data.encode(),'utf-8-sig',',')
        self.assertEqual(info['rows'],12); self.assertEqual(len(info['sample']),5)
        self.assertEqual(len(info['sample'][0]['b']),200)

    def test_boundary_shapes_and_bad_json(self):
        good=[BOUNDARY,{'type':'Feature','properties':{},'geometry':BOUNDARY},{'type':'FeatureCollection','features':[{'type':'Feature','properties':{},'geometry':BOUNDARY}]}]
        for data in good:
            with self.subTest(type=data['type']):
                self.assertIn('id',self.app.upload('boundary',json.dumps(data).encode(),'utf-8-sig',','))
        for data in [b'not JSON',b'[]',b'null',b'{"type":"Point","coordinates":[0,0]}']:
            with self.subTest(data=data),self.assertRaises((gui.InputError,ValueError)):
                self.app.upload('boundary',data,'utf-8-sig',',')

    def test_nested_nonpolygon_or_empty_boundary_never_passes_engine(self):
        from poi_audit.core import load_config,load_boundary
        from poi_audit.common import AuditError
        bad=[{'type':'FeatureCollection','features':[]},{'type':'Feature','geometry':{'type':'Point','coordinates':[116.42,39.92]}}]
        for boundary in bad:
            with self.subTest(boundary=boundary):
                req=self.uploaded_gis()
                try:
                    bid=self.app.upload('boundary',json.dumps(boundary).encode(),'utf-8-sig',',')['id']
                    c,_=gui.build_config({**req,'boundary_id':bid},self.app.uploads)
                except gui.InputError:
                    continue
                loaded,base=load_config(self.config_path(c))
                with self.assertRaises(AuditError):load_boundary(loaded,base)


class HTTPTests(TempCase):
    def setUp(self):
        super().setUp()
        self.server=gui.ThreadingHTTPServer(('127.0.0.1',0),gui.Handler)
        self.server.app=self.app; self.server.daemon_threads=True
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True); self.thread.start()
        self.host=f'127.0.0.1:{self.server.server_port}'

    def tearDown(self):
        self.server.shutdown(); self.server.server_close(); self.thread.join(timeout=3)
        super().tearDown()

    def request(self,path='/',method='GET',body=None,headers=None,auth=True):
        h={'Host':self.host}
        if auth:h['Cookie']='poi_session='+self.app.token
        if auth and method=='POST':h.update({'X-POI-Token':self.app.token,'Origin':'http://'+self.host})
        h.update(headers or {})
        conn=http.client.HTTPConnection('127.0.0.1',self.server.server_port,timeout=3)
        try:
            conn.request(method,path,body=body,headers=h)
            res=conn.getresponse(); return res.status,dict(res.getheaders()),res.read()
        finally:conn.close()

    def test_bootstrap_redirect_is_host_and_token_bound(self):
        status,h,body=self.request('/?token='+self.app.token,auth=False)
        self.assertEqual(status,302); self.assertEqual(h['Location'],'/')
        self.assertIn('HttpOnly',h['Set-Cookie']); self.assertIn('SameSite=Strict',h['Set-Cookie'])
        for path,headers in [('/',{}),('/?token=wrong',{}),('/?token='+self.app.token,{'Host':'attacker.example'})]:
            with self.subTest(path=path,headers=headers):self.assertEqual(self.request(path,auth=False,headers=headers)[0],403)

    def test_authenticated_page_security_headers_and_no_token_in_info(self):
        status,h,body=self.request()
        self.assertEqual(status,200); self.assertIn(self.app.token.encode(),body)
        self.assertEqual(h['Cache-Control'],'no-store'); self.assertEqual(h['X-Content-Type-Options'],'nosniff')
        self.assertEqual(h['Referrer-Policy'],'no-referrer'); self.assertIn("frame-ancestors 'none'",h['Content-Security-Policy'])
        status,h,body=self.request('/api/info')
        self.assertEqual(status,200); self.assertNotIn(self.app.token.encode(),body)
        self.assertEqual(json.loads(body)['engine'],'0.3.0')

    def test_wrong_host_cookie_and_missing_cookie_are_rejected(self):
        for headers in [{'Host':'localhost:'+str(self.server.server_port)},{'Host':'evil.example'},{'Cookie':'poi_session=wrong'},{'Cookie':''}]:
            with self.subTest(headers=headers):self.assertEqual(self.request('/api/info',headers=headers)[0],403)

    def test_post_requires_cookie_token_and_same_origin(self):
        cases=[{'Cookie':''},{'X-POI-Token':''},{'X-POI-Token':'wrong'},{'Origin':''},{'Origin':'https://'+self.host},{'Origin':'http://evil.example'},{'Host':'evil.example'}]
        for headers in cases:
            with self.subTest(headers=headers):
                self.assertEqual(self.request('/api/upload?kind=csv',method='POST',body=CSV,headers=headers)[0],403)
        self.assertEqual(self.app.uploads,{})
        status,_,body=self.request('/api/upload?kind=csv&header_row=1',method='POST',body=CSV)
        self.assertEqual(status,200); self.assertEqual(json.loads(body)['rows'],2)

    def test_xlsx_http_selection_and_template_download(self):
        template=ROOT/'examples/templates/stats.xlsx'
        status,headers,raw=self.request('/api/template/stats.xlsx')
        self.assertEqual(status,200);self.assertEqual(raw,template.read_bytes())
        self.assertEqual(headers['Content-Disposition'],'attachment')
        status,_,raw=self.request('/api/upload?kind=xlsx',method='POST',body=template.read_bytes())
        self.assertEqual(status,200);uploaded=json.loads(raw)
        body=json.dumps({'id':uploaded['id'],'sheet':'数据','header_row':1}).encode()
        self.assertEqual(self.request('/api/table',method='POST',body=body,headers={'Origin':'http://evil.example'})[0],403)
        status,_,raw=self.request('/api/table',method='POST',body=body)
        self.assertEqual(status,200);selected=json.loads(raw)
        self.assertNotEqual(selected['id'],uploaded['id']);self.assertEqual(selected['rows'],21)
        self.assertEqual(selected['sample'][0]['记录编号'],'001')
        self.assertNotIn('csv_path',selected)
    def test_csv_http_title_row_selection_and_bad_xlsx(self):
        data='表格说明\n编号,值\n001,2\n'.encode()
        status,_,raw=self.request('/api/upload?kind=csv',method='POST',body=data)
        self.assertEqual(status,200);uploaded=json.loads(raw);self.assertTrue(uploaded['needs_selection'])
        status,_,raw=self.request('/api/table',method='POST',body=json.dumps({'id':uploaded['id'],'header_row':2}).encode())
        self.assertEqual(status,200);self.assertEqual(json.loads(raw)['sample'][0]['编号'],'001')
        self.assertEqual(self.request('/api/upload?kind=xlsx',method='POST',body=b'fake')[0],400)

    def test_csv_upload_larger_than_old_25mb_limit_is_streamed(self):
        path=self.work/'larger.csv'
        with path.open('wb') as output:
            output.write(b'id,note\n')
            for index in range(10000): output.write(f'{index:05d},'.encode()+b'x'*2700+b'\n')
        size=path.stat().st_size
        self.assertGreater(size,25*1024*1024)
        with path.open('rb') as source:
            status,_,raw=self.request('/api/upload?kind=csv&header_row=1',method='POST',body=source,headers={'Content-Length':str(size)})
        self.assertEqual(status,200)
        parsed=json.loads(raw);self.assertEqual(parsed['rows'],10000)
        self.assertEqual(parsed['sample'][0]['id'],'00000')
        self.assertFalse(list((self.app.work/'inputs').glob('upload-*.tmp')))

    def test_upload_rejects_chunked_framing_and_json_separate_cap(self):
        self.assertEqual(self.request('/api/upload?kind=csv',method='POST',body=CSV,headers={'Transfer-Encoding':'chunked'})[0],400)
        with patch.object(gui,'MAX_JSON_BYTES',8):
            self.assertEqual(self.request('/api/jobs',method='POST',body=b'{"mode":"gis"}')[0],400)

    def test_post_rejects_empty_oversize_invalid_json_and_nonobject(self):
        for body in [b'',b'[]',b'null',b'{',b'"string"']:
            with self.subTest(body=body):self.assertEqual(self.request('/api/jobs',method='POST',body=body)[0],400)
        with patch.object(gui,'MAX_BYTES',8):self.assertEqual(self.request('/api/jobs',method='POST',body=b'123456789')[0],400)
        self.assertEqual(self.request('/api/jobs',method='POST',body=b'x',headers={'Content-Length':'bad'})[0],400)
        self.assertEqual(self.request('/api/not-real',method='POST',body=b'{}')[0],404)

    def test_static_path_traversal_and_symlink_are_denied(self):
        for path in ['/static/../server.py','/static/%2e%2e/server.py','/static/%2fetc/passwd','/static/%2e%2e%2f%2e%2e%2fpyproject.toml','/static/..%5cserver.py','/static/%00']:
            with self.subTest(path=path):self.assertNotEqual(self.request(path)[0],200)
        # Override the static root only in memory to test symlink containment.
        static=self.work/'static'; static.mkdir(); secret=self.work/'secret.txt';secret.write_text('secret')
        (static/'escape.txt').symlink_to(secret)
        with patch.object(gui,'STATIC',static):self.assertEqual(self.request('/static/escape.txt')[0],400)

    def test_output_requires_known_completed_job_and_safe_path(self):
        folder=self.work/'job'; (folder/'output').mkdir(parents=True)
        (folder/'output'/'safe.csv').write_text('a\n1\n'); (folder/'config.json').write_text('private')
        self.app.jobs['test']={'id':'test','state':'running','folder':folder,'cancel':False,'process':None}
        self.assertEqual(self.request('/output/missing/safe.csv')[0],400)
        self.assertEqual(self.request('/output/test/safe.csv')[0],400)
        self.app.jobs['test']['state']='complete'
        status,h,body=self.request('/output/test/safe.csv')
        self.assertEqual(status,200);self.assertEqual(h['Content-Disposition'],'attachment')
        for path in ['/output/test/../config.json','/output/test/%2e%2e/config.json','/output/test/%2fetc/passwd']:
            with self.subTest(path=path):self.assertEqual(self.request(path)[0],400)

    def test_completed_html_report_allows_its_inline_style_without_inline_scripts(self):
        folder=self.work/'job'; (folder/'output').mkdir(parents=True)
        style='body{color:red}'
        (folder/'output'/'report.html').write_text('<!doctype html><style>'+style+'</style><h1>Report</h1>')
        self.app.jobs['test']={'id':'test','state':'complete','folder':folder,'cancel':False,'process':None}
        status,h,body=self.request('/output/test/report.html')
        self.assertEqual(status,200)
        csp=h['Content-Security-Policy']
        directives={part.strip().split()[0]:part.strip().split()[1:] for part in csp.split(';') if part.strip()}
        allowed_styles=directives.get('style-src',directives.get('default-src',[]))
        matching_hash="'sha256-"+base64.b64encode(hashlib.sha256(style.encode()).digest()).decode()+"'"
        self.assertTrue("'unsafe-inline'" in allowed_styles or matching_hash in allowed_styles,
                        'Generated reports have inline <style>; global CSP must not leave them unstyled')
        self.assertNotIn("'unsafe-inline'",directives.get('script-src',[]))
        # The application itself should retain its stricter style policy.
        self.assertNotIn("style-src 'self' 'unsafe-inline'",self.request('/')[1]['Content-Security-Policy'])

    def test_cancel_unknown_active_and_completed_states(self):
        self.assertEqual(self.request('/api/cancel/missing',method='POST',body=b'{}')[0],400)
        folder=self.work/'job'; folder.mkdir()
        for state in ['queued','validating','running','complete','failed','cancelled']:
            self.app.jobs['test']={'id':'test','state':state,'folder':folder,'cancel':False,'process':None}
            with self.subTest(state=state):
                status,_,body=self.request('/api/cancel/test',method='POST',body=b'{}')
                self.assertEqual(status,200);self.assertNotIn('folder',json.loads(body));self.assertNotIn('cancel',json.loads(body))
                self.assertEqual(self.app.jobs['test']['cancel'],state in {'queued','validating','running'})


class JobTests(TempCase):
    def make_job(self,mode='stats',cancel=False):
        folder=self.work/'manual-job';folder.mkdir(exist_ok=True)
        return {'id':'manual','state':'queued','mode':mode,'source':'demo','rows':2,'created_at':time.time(),'folder':folder,'process':None,'cancel':cancel,'error':'','files':[],'counts':None}

    def test_submit_snapshot_is_self_contained_and_single_active(self):
        for mode,req in [('gis',gis_request()),('stats',stats_request())]:
            with self.subTest(mode=mode):
                app=gui.App(self.work/mode)
                with patch.object(gui.threading.Thread,'start'):
                    public=app.submit(req)
                    job=app.jobs[public['id']]
                    config=json.loads((job['folder']/'config.json').read_text())
                    specs=config['inputs']+[config['boundary']] if mode=='gis' else [config['input']]
                    for spec in specs:
                        self.assertFalse(Path(spec['path']).is_absolute());self.assertTrue((job['folder']/spec['path']).is_file())
                    self.assertEqual(public['state'],'queued');self.assertNotIn('process',public);self.assertNotIn('folder',public)
                    with self.assertRaises(gui.InputError):app.submit(req)
                    job['state']='failed'
                    self.assertNotEqual(app.submit(req)['id'],public['id'])

    def test_precancel_never_starts_engine_and_marks_cancelled(self):
        job=self.make_job(cancel=True)
        with patch.object(gui.subprocess,'Popen') as popen:self.app.execute(job)
        popen.assert_not_called();self.assertEqual(job['state'],'cancelled');self.assertIn('finished_at',job)
        self.assertFalse((job['folder']/'result.zip').exists())

    def test_engine_failure_never_marks_partial_output_success(self):
        job=self.make_job()
        class FailingProcess:
            returncode=2
            def poll(self):return self.returncode
        with patch.object(gui.subprocess,'Popen',return_value=FailingProcess()) as popen:self.app.execute(job)
        self.assertEqual(popen.call_count,1);self.assertEqual(job['state'],'failed');self.assertTrue(job['error'])
        self.assertIsNone(job['process']);self.assertFalse((job['folder']/'result.zip').exists())

    def test_active_cancellation_terminates_process(self):
        job=self.make_job()
        class CancelProcess:
            returncode=None
            terminated=False
            def poll(self):
                if not self.terminated:job['cancel']=True
                return self.returncode
            def terminate(self):self.terminated=True;self.returncode=-15
        proc=CancelProcess()
        with patch.object(gui.subprocess,'Popen',return_value=proc):self.app.execute(job)
        self.assertTrue(proc.terminated);self.assertEqual(job['state'],'cancelled');self.assertIsNone(job['process'])

    def test_success_uses_validating_then_running_and_bundles_files(self):
        job=self.make_job();folder=job['folder'];(folder/'config.json').write_text('{}')
        observed=[]
        class SuccessProcess:
            returncode=0
            def poll(self):return 0
        def popen(args,**kwargs):
            observed.append((job['state'],args))
            if '--output' in args:
                out=folder/'output';out.mkdir()
                (out/'manifest.json').write_text(json.dumps({'counts':{'rows':2},'warnings':['fixture']}))
                (out/'chart.png').write_bytes(b'fixture')
            return SuccessProcess()
        with patch.object(gui.subprocess,'Popen',side_effect=popen):self.app.execute(job)
        self.assertEqual([x[0] for x in observed],['validating','running'])
        self.assertIn('stats-validate',observed[0][1]);self.assertIn('stats',observed[1][1])
        self.assertEqual(job['state'],'complete');self.assertEqual(job['counts'],{'rows':2});self.assertEqual(job['warnings'],['fixture'])
        with zipfile.ZipFile(folder/'result.zip') as z:
            self.assertIn('config.json',z.namelist());self.assertIn('output/chart.png',z.namelist());self.assertNotIn('result.zip',z.namelist())

    def test_cancellation_during_packaging_does_not_become_complete(self):
        job=self.make_job();folder=job['folder'];out=folder/'output';out.mkdir()
        (out/'manifest.json').write_text(json.dumps({'counts':{'rows':2},'warnings':[]}))
        class SuccessProcess:
            returncode=0
            def poll(self):return 0
        real_zipfile=zipfile.ZipFile
        def cancelling_zipfile(*args,**kwargs):
            # A real HTTP cancellation is accepted while state remains 'running'.
            job['cancel']=True
            return real_zipfile(*args,**kwargs)
        with patch.object(gui.subprocess,'Popen',return_value=SuccessProcess()),patch.object(gui.zipfile,'ZipFile',side_effect=cancelling_zipfile):
            self.app.execute(job)
        self.assertEqual(job['state'],'cancelled')

    def test_missing_manifest_is_failure_even_if_process_exits_zero(self):
        job=self.make_job()
        class SuccessProcess:
            returncode=0
            def poll(self):return 0
        with patch.object(gui.subprocess,'Popen',return_value=SuccessProcess()):self.app.execute(job)
        self.assertEqual(job['state'],'failed');self.assertIn('manifest.json',job['error'])

    def test_real_engine_rejects_bad_crs_with_error_log(self):
        req=gis_request(crs='invalid:crs')
        with patch.object(gui.threading.Thread,'start'):
            public=self.app.submit(req)
        job=self.app.jobs[public['id']]
        self.app.execute(job)
        self.assertEqual(job['state'],'failed');self.assertIn('invalid crs',self.app.public(job)['log'])
        self.assertFalse((job['folder']/'result.zip').exists())


if __name__ == '__main__':
    unittest.main(verbosity=2)
