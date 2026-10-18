"""Actual loopback API regression tests for coordinate failures; uses existing deps.
Run: PYTHONPATH=src:<existing-deps> python tests_gui/test_projection_preflight.py
"""
import csv
import http.client
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT),str(ROOT/'src')]
from gui.server import App, Handler, ThreadingHTTPServer


def polygon(x=117.26,y=31.85,d=.05):
    return {'type':'Polygon','coordinates':[[[x,y],[x+d,y],[x+d,y+d],[x,y+d],[x,y]]]}


class ProjectionApiTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='poi-projection-api-')
        self.app=App(Path(self.temp.name)/'work')
        self.server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
        self.server.app=self.app
        self.server.daemon_threads=True
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start()
    def tearDown(self):
        self.server.shutdown();self.server.server_close();self.thread.join()
        self.temp.cleanup()
    def request(self,path,body=None,raw=False):
        conn=http.client.HTTPConnection('127.0.0.1',self.server.server_port,timeout=15)
        headers={'Cookie':'poi_session='+self.app.token,'X-POI-Token':self.app.token,
                 'Origin':f'http://127.0.0.1:{self.server.server_port}'}
        if body is not None:
            body=body if raw else json.dumps(body).encode()
            headers['Content-Type']='application/octet-stream' if raw else 'application/json'
        conn.request('POST' if body is not None else 'GET',path,body=body,headers=headers)
        res=conn.getresponse();data=res.read();status=res.status;conn.close()
        try:return status,json.loads(data)
        except ValueError:return status,data
    def upload(self,kind,data):
        status,res=self.request('/api/upload?kind='+kind,data,True)
        self.assertEqual(status,200,res)
        return res['id']
    def make_request(self,rows,boundary=None,**updates):
        data='id,x,y,lon,lat\n'+''.join(f'{i},{x},{y},117.28,31.87\n' for i,(x,y) in enumerate(rows))
        tid=self.upload('csv',data.encode())
        bid=self.upload('boundary',json.dumps(boundary or polygon()).encode())
        req={'source':'upload','mode':'gis','mapping_confirmed':True,'crs_confirmed':True,
             'csv_id':tid,'boundary_id':bid,'fields':{'x':'x','y':'y','id':'id'},
             'crs':'EPSG:4326','convention':'WGS84','boundary_crs':'EPSG:4326','boundary_convention':'WGS84',
             'map_crs':'EPSG:32650','kde':True,'bandwidth':200,'cell':100,'title':'Projection test'}
        req['record_unit']='一个采样点'
        req.update(updates)
        return req
    def run_job(self,req):
        status,job=self.request('/api/jobs',req);self.assertEqual(status,202,job)
        deadline=time.monotonic()+120
        while job['state'] in {'queued','validating','running'} and time.monotonic()<deadline:
            time.sleep(.15);status,job=self.request('/api/jobs/'+job['id']);self.assertEqual(status,200,job)
        self.assertNotIn(job['state'],{'queued','validating','running'},job)
        return job
    def assert_failed_cleanly(self,job,phrase):
        self.assertEqual(job['state'],'failed',job)
        self.assertIn(phrase,job['error']);self.assertEqual(job['files'],[])
        folder=self.app.jobs[job['id']]['folder']
        self.assertFalse((folder/'output').exists());self.assertFalse((folder/'result.zip').exists())
        self.assertNotIn('findfont:',job['error']);self.assertNotIn('Traceback',job['error'])
        self.assertIn('"validation": "failed"',job['log'])
        status,_=self.request('/output/'+job['id']+'/atlas_01_spatial_p01.png')
        self.assertEqual(status,400)
    def test_projected_magnitudes_rejected_as_geographic(self):
        job=self.run_job(self.make_request([(13000000,3700000),(13000100,3700100)]))
        self.assert_failed_cleanly(job,'超出经纬度范围')
        self.assertEqual(job['counts']['rejected'],2)
    def test_swapped_latitude_longitude_has_explicit_hint(self):
        job=self.run_job(self.make_request([(31.87,117.28),(31.871,117.281)]))
        self.assert_failed_cleanly(job,'经纬度可能选反')
    def test_geographic_magnitudes_declared_projected_get_explanation(self):
        job=self.run_job(self.make_request([(117.28,31.87),(117.281,31.871)],crs='EPSG:3857',convention='CRS_NATIVE'))
        self.assert_failed_cleanly(job,'数值都落在经纬度范围')
        self.assertTrue(job['preflight']['sources'][0]['projected_values_look_like_degrees'])
    def test_no_overlap_never_renders_empty_result(self):
        job=self.run_job(self.make_request([(117.28,31.87),(117.281,31.871)],boundary=polygon(116.4,39.9)))
        self.assert_failed_cleanly(job,'保留 0 / 2')
        self.assertEqual(job['counts']['outside_boundary'],2)
        self.assertIn('点范围',job['error']);self.assertIn('边界范围',job['error'])
    def test_finite_geographic_values_nonfinite_map_projection_caught_early(self):
        job=self.run_job(self.make_request([(27,0),(28,.1)]))
        self.assert_failed_cleanly(job,'不是有限坐标')
        self.assertGreater(job['preflight']['nonfinite_map_points'],0)
    def test_nonfinite_boundary_projection_caught_before_render(self):
        job=self.run_job(self.make_request([(117.28,31.87)],boundary=polygon(26.9,-.1,.2)))
        self.assert_failed_cleanly(job,'边界无法有效转换')
        self.assertFalse(job['preflight']['boundary_projection_finite'])
    def test_nonfinite_raw_values_rejected_before_job(self):
        status,res=self.request('/api/upload?kind=csv',b'id,x,y\n1,inf,31\n',True)
        self.assertEqual(status,400,res)
        self.assertIn('NaN / Infinity',res['error'])
        self.assertEqual(self.app.jobs,{})
    def test_valid_synthetic_control_exports_real_kde(self):
        # Deliberately synthetic, labeled control; not the user's study-area boundary.
        job=self.run_job(self.make_request([(117.28,31.87),(117.281,31.871),(117.282,31.8715)],title='SYNTHETIC CONTROL ONLY'))
        self.assertEqual(job['state'],'complete',job)
        self.assertEqual(job['counts']['kept'],3)
        self.assertTrue(job['preflight']['render_ready'])
        files={f['name'] for f in job['files']}
        self.assertIn('atlas_03_density_p01.png',files)
        status,png=self.request('/output/'+job['id']+'/atlas_03_density_p01.png')
        self.assertEqual(status,200);self.assertTrue(png.startswith(b'\x89PNG'))
        manifest=json.loads((self.app.jobs[job['id']]['folder']/'output/manifest.json').read_text())
        self.assertFalse(manifest['synthetic']) # Upload provenance is never relabeled by guesses.
    def test_explicit_point_extent_preview_ignores_unrelated_boundary_and_labels_every_plot(self):
        req=self.make_request([(117.28,31.87),(117.281,31.871),(117.282,31.8715)],boundary=polygon(116.4,39.9),
            boundary_mode='extent_preview',extent_preview_confirmed=True,extent_padding_percent=5,map_crs='auto',title='SYNTHETIC CONTROL ONLY')
        req['boundary_id']='';req['boundary_convention']='';req['boundary_crs']=''
        job=self.run_job(req)
        self.assertEqual(job['state'],'complete',job);self.assertEqual(job['counts']['kept'],3)
        folder=self.app.jobs[job['id']]['folder'];out=folder/'output'
        config=json.loads((folder/'config.json').read_text());report=json.loads((folder/'import_report.json').read_text())
        self.assertEqual(config['boundary']['role'],'point_extent_preview')
        self.assertEqual(report['boundary_mode'],'extent_preview')
        self.assertFalse(report['boundary_provenance']['uses_uploaded_boundary'])
        for name in ['atlas_01_spatial_p01.svg','atlas_02_audit_p01.svg','atlas_03_density_p01.svg']:
            self.assertIn('点范围预览 / 非行政或正式研究区边界',(out/name).read_text())
            self.assertIn('每行代表：一个采样点',(out/name).read_text())
        density_svg=(out/'atlas_03_density_p01.svg').read_text()
        self.assertIn('未按 POI总数加权',density_svg)
        self.assertNotIn('POI 核密度图谱',density_svg)
        self.assertIn('不能当作城市行政研究结论',(out/'report.html').read_text())
        self.assertEqual(json.loads((out/'manifest.json').read_text())['boundary']['role'],'point_extent_preview')
        self.assertEqual(json.loads((out/'manifest.json').read_text())['record_unit'],'一个采样点')
        self.assertIn('采样点密度，不是原始 POI 点密度',(out/'report.html').read_text())
    def test_point_preview_needs_specific_confirmation_and_known_source(self):
        req=self.make_request([(117.28,31.87)],boundary_mode='extent_preview',extent_preview_confirmed=True)
        for update in [{'extent_preview_confirmed':False},{'convention':'unknown'},{'convention':'GCJ-02'}]:
            status,res=self.request('/api/jobs',{**req,**update});self.assertEqual(status,400,res)
        self.assertEqual(self.app.jobs,{})
    def test_own_gis_requires_observation_unit(self):
        req=self.make_request([(117.28,31.87)])
        for unit in ['',None,'x'*81,'a\nb']:
            status,res=self.request('/api/jobs',{**req,'record_unit':unit})
            self.assertEqual(status,400,res)
        self.assertEqual(self.app.jobs,{})
    def test_font_warning_is_separate_from_failure(self):
        path=Path(self.temp.name)/'log.txt'
        path.write_text('findfont: Failed to find font weight bold for SimHei, now using 400.\nERROR: 制图前检查未通过：\n当前范围内保留 0 / 2 条\n')
        info=self.app.log_diagnostics(path)
        self.assertNotIn('findfont',info['error']);self.assertIn('保留 0',info['error'])
        self.assertTrue(any('不是坐标投影失败' in s for s in info['warnings']))


if __name__=='__main__':unittest.main(verbosity=2)
