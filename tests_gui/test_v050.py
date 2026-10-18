"""v0.5 import/projection integration coverage, no browser claims."""
import io, csv, json, sys, tempfile, unittest, zipfile
from pathlib import Path
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from gui.server import App,build_config,InputError
from gui.coordinates import auto_utm

class NewAdapterTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.root=Path(self.tmp.name);self.app=App(self.root/'work')
    def tearDown(self):self.tmp.cleanup()
    def boundary(self,lon=12.3,lat=48.1,extent=.02):
        return {'type':'Polygon','coordinates':[[[lon,lat],[lon+extent,lat],[lon+extent,lat+extent],[lon,lat+extent],[lon,lat]]]}
    def request(self,lon=12.3,lat=48.1,boundary=None):
        body=f'编号,经度,纬度,分类\n001,{lon+.005},{lat+.005},样本\n002,{lon+.01},{lat+.01},样本\n'.encode()
        table=self.app.upload('csv',body)
        boundary=self.app.upload('boundary',json.dumps(boundary or self.boundary(lon,lat)).encode())
        return {'mode':'gis','source':'upload','record_unit':'一个合成测试观测点','mapping_confirmed':True,'csv_id':table['id'],'boundary_id':boundary['id'],'fields':{'x':'经度','y':'纬度','id':'编号','category':'分类'},'crs':'EPSG:4326','boundary_crs':'EPSG:4326','convention':'WGS84','boundary_convention':'WGS84','crs_confirmed':True,'map_crs':'auto'}
    def test_auto_utm_northern_not_hefei(self):
        c,info=build_config(self.request(),self.app.uploads)
        self.assertEqual(c['map_crs'],'EPSG:32633');self.assertIn('33N',info['projection_note'])
    def test_auto_utm_southern(self):
        c,_=build_config(self.request(151.2,-33.9),self.app.uploads)
        self.assertEqual(c['map_crs'],'EPSG:32756')
    def test_auto_rejects_cross_zone_boundary(self):
        with self.assertRaisesRegex(InputError,'跨 UTM'):
            build_config(self.request(11.99,48.1,boundary=self.boundary(11.99,48.1)),self.app.uploads)
    def test_auto_rejects_polar_or_global(self):
        for lon,lat,boundary in [(12.3,85,None),(12.3,48.1,self.boundary(12.3,48.1,30))]:
            with self.subTest(lat=lat),self.assertRaises(InputError):
                build_config(self.request(lon,lat,boundary),self.app.uploads)
    def test_auto_requires_both_known_sources(self):
        req=self.request()
        for update in [{'convention':'unknown'},{'crs_confirmed':False},{'convention':'CRS_NATIVE','crs':'EPSG:4490'},{'boundary_convention':'CRS_NATIVE','boundary_crs':'EPSG:4490'},{'convention':'GCJ-02'},{'convention':'WGS84','crs':'EPSG:3857'}]:
            with self.subTest(update=update),self.assertRaises(InputError):build_config({**req,**update},self.app.uploads)
    def test_advanced_projection_explicit(self):
        req=self.request();req['map_crs']='EPSG:32633'
        c,info=build_config(req,self.app.uploads);self.assertEqual(c['map_crs'],'EPSG:32633');self.assertIn('高级',info['projection_note'])
    def test_upload_mapping_confirmation_is_explicit(self):
        req=self.request()
        for value in [False,None,'true']:
            with self.subTest(value=value),self.assertRaisesRegex(InputError,'字段对应'):
                build_config({**req,'mapping_confirmed':value},self.app.uploads)
    def test_upload_and_selection_immutable(self):
        table=self.app.upload('csv','a,b\n1,2\n3,4\n'.encode())
        later=self.app.select_table({'id':table['id'],'header_row':2})
        self.assertNotEqual(table['id'],later['id'])
        self.assertEqual(self.app.uploads[table['id']]['info']['headers'],['a','b'])
        self.assertEqual(self.app.uploads[later['id']]['info']['headers'],['1','2'])
    def test_snapshot_retains_original_and_import_metadata(self):
        req=self.request()
        with patch('gui.server.threading.Thread.start'):job=self.app.submit(req)
        folder=self.app.jobs[job['id']]['folder']
        self.assertTrue((folder/'input/original.csv').exists())
        report=json.loads((folder/'import_report.json').read_text())
        self.assertEqual(report['map_crs'],'EPSG:32633')
        self.assertNotIn('sample',report)
        self.assertEqual(job['map_crs'],'EPSG:32633')
    def stats_groups(self,count):
        body='id,x,y,group\n'+''.join(f'{i},{i+1},{i+2},g{i}\n' for i in range(count))
        table=self.app.upload('csv',body.encode())
        return {'mode':'stats','source':'upload','mapping_confirmed':True,'csv_id':table['id'],'fields':{'x':'x','y':'y','group':'group'},'record_unit':'one synthetic observation','plots':['scatter']}
    def test_large_groups_need_explicit_pagination(self):
        req=self.stats_groups(13)
        with self.assertRaisesRegex(InputError,'13 组'): build_config(req,self.app.uploads)
        req['high_cardinality_action']='paginate'
        config,info=build_config(req,self.app.uploads)
        self.assertEqual(info['group_count'],13);self.assertIn('PDF',info['grouping_note'])
    def test_group_hard_limit_and_uncolored_scatter(self):
        req=self.stats_groups(121);req['high_cardinality_action']='paginate'
        with self.assertRaisesRegex(InputError,'120'):build_config(req,self.app.uploads)
        req['fields']['group']=''
        config,info=build_config(req,self.app.uploads)
        self.assertNotIn('group_field',config['plots'][0])
    def test_twelve_groups_do_not_require_pagination(self):
        config,info=build_config(self.stats_groups(12),self.app.uploads)
        self.assertEqual(info['group_count'],12)
    def test_csv_title_rows_can_be_selected_after_inspection(self):
        upload=self.app.upload('csv','这是说明\n名称,数值\n甲,12\n乙,13\n'.encode(),allow_selection=True)
        self.assertTrue(upload['needs_selection']);self.assertEqual(upload['raw_preview'][0],['这是说明'])
        selected=self.app.select_table({'id':upload['id'],'header_row':2})
        self.assertEqual(selected['headers'],['名称','数值']);self.assertEqual(selected['rows'],2)
    def test_failed_sheet_selection_leaves_original_usable(self):
        upload=self.app.upload('xlsx',(ROOT/'examples/templates/stats.xlsx').read_bytes())
        with self.assertRaises(InputError):self.app.select_table({'id':upload['id'],'sheet':'missing','header_row':1})
        selected=self.app.select_table({'id':upload['id'],'sheet':'数据','header_row':1})
        self.assertEqual(selected['sample'][0]['记录编号'],'001')
    def test_xlsx_templates_can_select_and_map(self):
        for mode in ['gis','stats']:
            p=ROOT/'examples/templates'/f'{mode}.xlsx'
            if not p.exists():self.fail('Template is required: '+str(p))
            upload=self.app.upload('xlsx',p.read_bytes())
            self.assertIn('数据',[s['name'] for s in upload['sheets']])
            table=self.app.select_table({'id':upload['id'],'sheet':'数据','header_row':1})
            self.assertEqual(table['sample'][0]['记录编号'],'001')
            if mode=='gis':
                boundary=self.app.upload('boundary',(ROOT/'examples/boundary.geojson').read_bytes())
                req={'mode':'gis','source':'upload','record_unit':'一个合成测试观测点','mapping_confirmed':True,'csv_id':table['id'],'boundary_id':boundary['id'],'fields':{'x':'经度','y':'纬度','id':'记录编号','name':'名称','category':'分类','date':'日期'},'crs':'EPSG:4326','boundary_crs':'EPSG:4326','convention':'WGS84','boundary_convention':'WGS84','crs_confirmed':True,'map_crs':'auto'}
            else:
                req={'mode':'stats','source':'upload','mapping_confirmed':True,'csv_id':table['id'],'fields':{'y':'步行时间_分钟','group':'分组'},'record_unit':'一个虚构街道样本','plots':['half_violin']}
            config,info=build_config(req,self.app.uploads)
            self.assertGreater(info['rows'],15)
            if mode=='stats':self.assertNotIn('x',config['plots'][0])

if __name__=='__main__':unittest.main(verbosity=2)
