"""Reproduce illustrative observation metrics. No real streets/people/research data."""
from pathlib import Path
import csv
import json
import numpy as np

BASE = Path(__file__).resolve().parent
rng = np.random.default_rng(20261006)
rows = []
for group, a, b, offset in [('样例组 A', 2.7, 2.8, 0), ('样例组 B', 2.1, 3.2, 7), ('样例组 C', 3.4, 2.4, -6)]:
    for i in range(60):
        # Bounded mixtures by construction, not post hoc trimming of observations.
        x = 100 * rng.beta(a, b)
        y = .32*x + .48*(100*rng.beta(2.4, 2.2)) + 10 + offset
        rows.append({'observation_id':f'SYN-{len(rows)+1:03d}', 'sample_group':group,
                     'supply_example':round(x, 3), 'walk_example':round(y, 3),
                     'data_origin':'synthetic_demo_only'})
rows[1]['observation_id'] = rows[0]['observation_id']  # deliberate duplicate ID; retained
rows[2]['observation_id'] = ''  # deliberate absent ID; retained
rows += [
    dict(observation_id='CHECK-181',sample_group='样例组 A',supply_example='',walk_example=61.5,data_origin='synthetic_demo_only'),
    dict(observation_id='CHECK-182',sample_group='样例组 B',supply_example='unknown',walk_example=41.3,data_origin='synthetic_demo_only'),
    dict(observation_id='CHECK-183',sample_group='样例组 C',supply_example=44,walk_example='NaN',data_origin='synthetic_demo_only'),
    dict(observation_id='CHECK-184',sample_group='样例组 A',supply_example=52,walk_example='2026-09-30',data_origin='synthetic_demo_only'),
    dict(observation_id='CHECK-185',sample_group='样例组 B',supply_example='inf',walk_example='',data_origin='synthetic_demo_only'),
    dict(observation_id='CHECK-186',sample_group='',supply_example=50,walk_example=50,data_origin='synthetic_demo_only'),
]
with (BASE/'metrics.csv').open('w',encoding='utf-8-sig',newline='') as f:
    w=csv.DictWriter(f,fieldnames=list(rows[0])); w.writeheader(); w.writerows(rows)
x={'field':'supply_example','label':'供给示例分数','unit':'虚构分数，0–100'}
y={'field':'walk_example','label':'步行条件示例分数','unit':'虚构分数，0–100'}
groups=['样例组 A','样例组 B','样例组 C']
c={
  'schema_version':1,'kind':'statistical_plots','title':'从关系到分布：三种统计图式','synthetic':True,
  'style':'mismatch_paper','language':'zh','font_family':['Noto Sans CJK SC','Microsoft YaHei','DejaVu Sans'],
  'input':{'path':'metrics.csv','encoding':'utf-8-sig','record_unit':'一条虚构样本观测（每行一条；无真实街段）','id_field':'observation_id'},
  'plots':[
    {'id':'scatter','type':'scatter','title':'01  散点关系 · 每个点对应一条观测','x':x,'y':y,
     'group_field':'sample_group','group_order':groups,'transform':'raw','fit':'ols','correlation':'spearman','labels':False},
    {'id':'raincloud','type':'half_violin','title':'02  半小提琴 · 分布、箱线与原始点','value':y,
     'group_field':'sample_group','group_order':groups,'transform':'raw','jitter_seed':1729},
    {'id':'quadrant','type':'quadrant','title':'03  四象限 · 阈值分组与样本计数','x':x,'y':y,'transform':'raw',
     'thresholds':{'x':{'method':'median'},'y':{'method':'median'}},
     'quadrant_labels':{'Q1':'X 高 · Y 高','Q2':'X 高 · Y 低','Q3':'X 低 · Y 高','Q4':'X 低 · Y 低'},'labels':False},
  ]}
for style in ['premium','multimodal_paper','mismatch_paper']:
    c['style']=style
    (BASE/f'demo_{style}.json').write_text(json.dumps(c,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
c['style']='multimodal_paper'; c['title']='显式标准化示例 · 原始值仍可追溯'
for p in c['plots']:
    p['transform']='zscore'
    if p['type']=='quadrant': p['thresholds']={axis:{'method':'fixed','value':0} for axis in ['x','y']}
(BASE/'demo_zscore.json').write_text(json.dumps(c,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
print(f'Wrote {len(rows)} synthetic rows and four template configs')
