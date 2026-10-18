"""Regenerate only synthetic inputs. No real places or user data are used."""
import csv
import json
import random
from pathlib import Path

ROOT = Path(__file__).resolve().parent
random.seed(71)
HEADERS = ['poi_id', 'name', 'type', 'lon', 'lat', 'observed', 'note']
rows = []
categories = ['Cafe', 'Market', 'Clinic', 'School', 'Park', 'Culture']
for i in range(120):
    # Synthetic point cloud, deliberately wholly inside the simple study outline.
    lon = 116.405 + random.random() * .07
    lat = 39.905 + random.random() * .07
    rows.append([f'A{i:03d}', f'Synthetic place {i+1:03d}', categories[i % 6],
                 f'{lon:.6f}', f'{lat:.6f}', '2026-09-15', 'synthetic'])
rows += [list(rows[0])]  # exact raw duplicate
r = list(rows[1]); r[-1] = 'alternate extra note'; rows.append(r)  # stable ID duplicate
rows += [ ['CONFLICT', 'Synthetic disputed place', 'Cafe', '116.43', '39.93', '2026-09-15', 'v1'],
          ['CONFLICT', 'Synthetic disputed place', 'Cafe', '116.44', '39.94', '2026-09-15', 'v2'],
          ['BAD1', 'Synthetic missing longitude', 'Cafe', '', '39.94', '', ''],
          ['BAD2', 'Synthetic nonnumeric', 'Cafe', 'east', '39.94', '', ''],
          ['BAD3', 'Synthetic impossible latitude', 'Cafe', '116.43', '95', '', ''],
          ['BAD4', 'Synthetic infinite', 'Cafe', 'inf', '39.94', '', ''],
          ['OUT1', 'Synthetic outside', 'Park', '116.52', '39.94', '2026-09-15', ''],
          ['HOLE', 'Synthetic in hole', 'Park', '116.449', '39.949', '2026-09-15', ''],
          ['', 'Synthetic missing optional ID', 'Culture', '116.465', '39.915', '', ''],
          ['DATE1', 'Synthetic old record', 'Market', '116.415', '39.925', '2020-01-01', ''],
          ['DATE2', 'Synthetic invalid date', 'School', '116.425', '39.965', '31/31/2026', ''],
          ['DATE3', 'Synthetic future record', 'Clinic', '116.435', '39.915', '2027-01-01', ''],
          ['UNKNOWN', 'Synthetic unmapped', 'Other label', '116.455', '39.965', '2026-09-15', ''],
          ['EDGE', 'Synthetic boundary edge', 'Park', '116.4', '39.94', '2026-09-15', ''],
          ['COLOC', 'Synthetic separate shop', 'Market', rows[2][3], rows[2][4], '2026-09-15', ''] ]
# Move any generated baseline point out of the artificial hole deterministically.
for r in rows:
    try:
        if 116.445 < float(r[3]) < 116.455 and 39.945 < float(r[4]) < 39.955 and r[0] != 'HOLE':
            r[3] = '116.462000'
    except ValueError:
        pass
with (ROOT/'provider_alpha.csv').open('w', encoding='utf-8-sig', newline='') as f:
    w=csv.writer(f); w.writerow(HEADERS); w.writerows(rows)
beta = [
    ['A000', 'Synthetic beta ID reuse', 'Cafe', '116.468', '39.968', '2026-09-15'],
    ['B001', rows[3][1], rows[3][2], rows[3][3], rows[3][4], '2026-09-15'],
    ['B002', 'Synthetic beta library', 'Culture', '116.42', '39.955', '2026-09-15'],
]
with (ROOT/'provider_beta.csv').open('w', encoding='utf-8-sig', newline='') as f:
    w=csv.writer(f); w.writerow(['record', 'label', 'kind', 'x', 'y', 'date']); w.writerows(beta)
boundary={'type':'Feature','properties':{'synthetic':True,'name':'Synthetic study area'},'geometry':{
    'type':'Polygon','coordinates':[
        [[116.4,39.9],[116.48,39.9],[116.48,39.98],[116.4,39.98],[116.4,39.9]],
        [[116.445,39.945],[116.445,39.955],[116.455,39.955],[116.455,39.945],[116.445,39.945]] ]}}
(ROOT/'boundary.geojson').write_text(json.dumps(boundary,indent=2)+'\n',encoding='utf-8')
config={
    'schema_version':1, 'title':'A traceable picture of place', 'synthetic':True,
    'inputs':[
        {'name':'alpha','namespace':'provider_alpha','path':'provider_alpha.csv','encoding':'utf-8-sig',
         'fields':dict(zip(['id','name','category','x','y','date'],HEADERS[:6])),
         'crs':'EPSG:4326','coordinate_convention':'WGS84','date_format':'%Y-%m-%d'},
        {'name':'beta','namespace':'provider_beta','path':'provider_beta.csv','encoding':'utf-8-sig',
         'fields':dict(zip(['id','name','category','x','y','date'],['record','label','kind','x','y','date'])),
         'crs':'EPSG:4326','coordinate_convention':'WGS84','date_format':'%Y-%m-%d'}],
    'boundary':{'path':'boundary.geojson','crs':'EPSG:4326','coordinate_convention':'WGS84'},
    'category_mapping':{'Cafe':'Food & drink','Market':'Retail','Clinic':'Health','School':'Education','Park':'Open space','Culture':'Culture'},
    'unknown_category':'Unmapped','reference_date':'2026-09-30','stale_after_days':365,
    'candidate_decimals':6, 'map_crs':'EPSG:32650','font_family':['DejaVu Sans']}
(ROOT/'demo.json').write_text(json.dumps(config,indent=2)+'\n',encoding='utf-8')
print('Synthetic CSV and boundary fixtures regenerated')
