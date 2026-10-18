"""Loopback-only GUI adapter for the unchanged poi-audit-kit 0.3.0 engine.
No external services, remote fonts, telemetry, or runtime dependency installation.
"""
from __future__ import annotations
import argparse, csv, importlib.util, io, json, mimetypes, os, secrets, shutil
import subprocess, sys, threading, time, webbrowser, zipfile, socket
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

ROOT = Path(__file__).resolve().parents[1]
STATIC = Path(__file__).parent / 'static'
MAX_BYTES = 100 * 1024 * 1024
MAX_JSON_BYTES = 1024 * 1024
MAX_ROWS = 200000
ENCODINGS = {'utf-8-sig', 'gb18030'}
STYLES = {'premium', 'multimodal_paper', 'mismatch_paper'}
FONTS = ['Noto Sans CJK SC', 'Microsoft YaHei', 'SimHei', 'PingFang SC', 'Arial Unicode MS', 'DejaVu Sans']

# Keep package imports working both from launch_gui.py and standalone tests.
if str(ROOT) not in sys.path: sys.path.insert(0, str(ROOT))
if str(ROOT/'src') not in sys.path: sys.path.insert(0, str(ROOT/'src'))
from gui.table_import import InputError, read_table, inspect_xlsx, inspect_csv
from gui import table_import
from gui.upload_io import receive_upload, choose_upload_limit
from gui.coordinates import auto_utm, point_extent_preview

def table_info(path, encoding='utf-8-sig', delimiter=','):
    info=read_table(path,encoding=encoding,delimiter=delimiter,allow_nonfinite_text=True)
    if info['rows']>MAX_ROWS: raise InputError('界面最多支持 200,000 行；未抽样，请分批或使用原命令行入口')
    return info

def checked_text(v, label, default='', limit=180):
    if v is None: v=default
    if not isinstance(v,str) or '\x00' in v or len(v)>limit: raise InputError(label+'格式无效或过长')
    return v.strip()

def number(v, label, minimum=None):
    import math
    try: value=float(v)
    except (ValueError,TypeError): raise InputError(label+'必须是数值')
    if not math.isfinite(value) or (minimum is not None and value<minimum): raise InputError(label+'超出有效范围')
    return value

def build_config(req, uploaded):
    if req.get('mode') == 'research':
        from gui.research import build_research_config
        return build_research_config(req, uploaded)
    mode=req.get('mode'); demo=req.get('source')=='demo'
    if mode not in {'gis','stats'}: raise InputError('请选择 GIS 或统计模式')
    if req.get('source') not in {'demo','upload'}: raise InputError('请选择合成示例或自己的数据')
    style=req.get('style','premium')
    if style not in STYLES: raise InputError('配色方案无效')
    encoding=req.get('encoding','utf-8-sig'); delimiter=req.get('delimiter',',')
    if demo:
        path=ROOT/('examples/provider_alpha.csv' if mode=='gis' else 'examples/statistics/metrics.csv')
        encoding='utf-8-sig'; delimiter=','
    else:
        item=uploaded.get(req.get('csv_id'))
        if not item or item['kind'] not in {'csv','xlsx'}: raise InputError('请先导入 CSV 或 XLSX 数据文件')
        if not item.get('info'): raise InputError('请先选择工作表和表头行，点击确认并预览数据')
        if req.get('mapping_confirmed') is not True: raise InputError('请核对列名和示例值，再勾选已确认字段对应关系')
        path=item['path']; encoding='utf-8-sig'; delimiter=','
    info=table_info(path,encoding,delimiter) if demo else dict(item['info'])
    fields=req.get('fields',{})
    if not isinstance(fields,dict): raise InputError('字段设置格式无效')
    def field(key,required=False):
        value=fields.get(key,'')
        if required and not value:
            labels={'x':'经度 / 第一个指标','y':'纬度 / 数值指标','group':'分组'}
            raise InputError('请选择'+labels.get(key,key)+'对应的列')
        if value and value not in info['headers']: raise InputError('所选字段不存在：'+str(value))
        return value
    config={'schema_version':1,'title':checked_text(req.get('title'),'标题') or ('POI 空间图谱' if mode=='gis' else '观测数据统计图'),
            'synthetic':demo,'font_family':FONTS}
    if mode=='gis':
        unit=checked_text(req.get('record_unit'),'每行观测单位',limit=80) or ('一个虚构 POI 地点' if demo else '')
        if not unit:raise InputError('请说明每行是什么，例如“一个采样点”或“一家店铺”；核密度按每行一个点计算，不会自动按 POI总数加权')
        if any(ord(ch)<32 for ch in unit):raise InputError('每行观测单位不能包含换行或控制字符')
        config['record_unit']=unit
        if not req.get('title'):config['title']='输入记录空间分布'
        if not demo and req.get('crs_confirmed') is not True: raise InputError('请核实并勾选数据和边界的坐标系声明')
        boundary_mode='file' if demo else req.get('boundary_mode','file')
        if boundary_mode not in {'file','extent_preview'}:raise InputError('范围模式无效，请选择实际边界文件或明确的点范围预览')
        crs=checked_text(req.get('crs'),'输入 CRS'); bcrs=checked_text(req.get('boundary_crs'),'边界 CRS')
        map_crs=checked_text(req.get('map_crs','auto'),'米制投影 CRS')
        convention=req.get('convention','WGS84'); bconvention=req.get('boundary_convention','WGS84')
        if boundary_mode=='extent_preview':bcrs='EPSG:4326';bconvention='WGS84'
        if convention not in {'WGS84','CRS_NATIVE'} or bconvention not in {'WGS84','CRS_NATIVE'}:
            raise InputError('不支持直接使用 GCJ-02 / BD-09；请先转换并记录转换方法')
        if not crs or not bcrs or not map_crs: raise InputError('数据 CRS、边界 CRS 和米制投影 CRS 都需要明确填写')
        if demo: boundary=ROOT/'examples/boundary.geojson'
        elif boundary_mode=='file':
            item=uploaded.get(req.get('boundary_id'))
            if not item or item['kind']!='boundary': raise InputError('GIS 模式需要实际研究区 GeoJSON 面边界；不会用示例边界替代')
            boundary=item['path']
        mapping={k:field(k,k in {'x','y'}) for k in ['x','y','category','id','name','date']}
        mapping={k:v for k,v in mapping.items() if v}
        if len(set(mapping.values()))!=len(mapping): raise InputError('不同角色需要不同字段，请检查 X/Y、分类与 ID 等映射')
        info['boundary_mode']=boundary_mode
        if boundary_mode=='extent_preview':
            feature,provenance=point_extent_preview(path,mapping['x'],mapping['y'],convention=convention,crs=crs,
                confirmed=req.get('extent_preview_confirmed'),padding_percent=req.get('extent_padding_percent',5))
            import hashlib
            content=json.dumps(feature,ensure_ascii=False,sort_keys=True,allow_nan=False)
            boundary=Path(path).parent/('point-extent-preview-'+hashlib.sha256(content.encode()).hexdigest()[:24]+'.geojson')
            boundary.write_text(content,encoding='utf-8')
            info['boundary_note']=provenance['note'];info['boundary_provenance']=provenance
            config['title']+=' · 点范围预览（非行政/正式研究区边界）'
        else:
            info['boundary_note']='使用明确提供的边界文件；请确认它与本次研究范围一致' if not demo else '合成示例边界，仅供演示'
        if map_crs=='auto':
            map_crs,note=auto_utm(path,mapping['x'],mapping['y'],boundary,convention=convention,crs=crs,boundary_convention=bconvention,boundary_crs=bcrs)
            info['projection_note']=note; info['map_crs']=map_crs
        else:
            info['projection_note']='使用你在高级设置指定的米制投影 '+map_crs+'；原引擎将核验其格式和单位。'; info['map_crs']=map_crs
        inp={'name':'input','namespace':'local_table','path':str(path),'encoding':encoding,'delimiter':delimiter,
             'fields':mapping,'crs':crs,'coordinate_convention':convention}
        if 'date' in mapping: inp['date_format']=checked_text(req.get('date_format'), '日期格式') or '%Y-%m-%d'
        config.update(inputs=[inp],boundary={'path':str(boundary),'crs':bcrs,'coordinate_convention':bconvention},
                      map_crs=map_crs,atlas={'enabled':True,'language':'zh','style':style})
        if boundary_mode=='extent_preview':config['boundary']['role']='point_extent_preview'
        if req.get('kde'):
            config['kde']={'enabled':True,'bandwidth_m':number(req.get('bandwidth',200),'带宽',.01),
                           'cell_size_m':number(req.get('cell',50),'网格大小',.01),'total_scale':'shared','contours':False}
    else:
        unit=checked_text(req.get('record_unit'),'观测单位')
        if not unit: raise InputError('请说明每行是什么观测单位，例如“一条街段的一次测量”')
        chosen=req.get('plots',['scatter','half_violin','quadrant'])
        if not isinstance(chosen,list) or not chosen or any(x not in {'scatter','half_violin','quadrant'} for x in chosen):
            raise InputError('请至少选择一种统计图')
        needs_xy=any(x in chosen for x in ['scatter','quadrant'])
        x=field('x',needs_xy); y=field('y',True); group=field('group','half_violin' in chosen)
        if needs_xy and x==y: raise InputError('X 和 Y 请使用不同的数值字段')
        if group and any(k in chosen for k in ['scatter','half_violin']):
            with Path(path).open(encoding=encoding,newline='') as handle:
                group_values=[row.get(group,'').strip() for row in csv.DictReader(handle,delimiter=delimiter)]
            count=len(set(v for v in group_values if v)); missing=sum(not v for v in group_values)
            info['group_count']=count
            if count>120: raise InputError(f'分组列“{group}”有 {count} 个不同值，超过 120 组安全上限。可能选了编号或名称，请改选类别/分类/等级；散点也可以不分组。未自动合并或删除任何类别')
            if count>12 and req.get('high_cardinality_action')!='paginate':
                raise InputError(f'分组列“{group}”有 {count} 组，会拆成多个子图。建议改选类别/分类/等级，或明确勾选“保留全部组，拆成多个子图”；散点可以不分组')
            note=f'按“{group}”分为 {count} 组。'
            if count>12: note+='已确认拆成多个子图，仍多时自动分页：PDF 包含全部页；每页 PNG / SVG 可逐页预览下载，未删除任何分组。'
            if missing: note+=f' 分组为空的 {missing} 行会按原引擎规则从分组图排除，详见排除台账。'
            info['grouping_note']=note

        transform=req.get('transform','raw')
        if transform not in {'raw','zscore'}: raise InputError('标准化方式无效')
        if delimiter != ',': raise InputError('内部表格尚未转换成标准逗号分隔格式，请重新导入')
        config.update(kind='statistical_plots',style=style,language='zh',input={'path':str(path),'encoding':encoding,'record_unit':unit},plots=[])
        if field('id'): config['input']['id_field']=field('id')
        variable=lambda axis,name: {'field':name,'label':checked_text(req.get(axis+'_label'),axis+'标签') or name,'unit':checked_text(req.get(axis+'_unit'),axis+'单位') or '未声明单位'}
        if 'scatter' in chosen:
            fit='ols' if req.get('ols') else 'none'
            p={'id':'scatter','type':'scatter','title':'散点关系 · 每点一条观测','x':variable('x',x),'y':variable('y',y),'transform':transform,'fit':fit,'correlation':'spearman' if req.get('correlation') else 'none','labels':False}
            if group:p['group_field']=group
            config['plots'].append(p)
        if 'half_violin' in chosen:
            config['plots'].append({'id':'raincloud','type':'half_violin','title':'半小提琴 · 分布与原始观测','value':variable('y',y),'group_field':group,'transform':transform,'jitter_seed':1729})
        if 'quadrant' in chosen:
            method=req.get('threshold','median')
            if method not in {'median','mean','fixed'}: raise InputError('阈值方法无效')
            thresholds={k:{'method':method} for k in ['x','y']}
            if method=='fixed':
                for k in ['x','y']:thresholds[k]['value']=number(req.get(k+'_threshold'),k+'阈值')
            config['plots'].append({'id':'quadrant','type':'quadrant','title':'四象限 · 描述性阈值分组','x':variable('x',x),'y':variable('y',y),'transform':transform,'thresholds':thresholds,'labels':False})
    if req.get('export') is not None:
        from poi_audit.exporting import validate_export
        config['export'] = validate_export(req['export'])
    if mode == 'stats' and req.get('title'):
        for plot in config['plots']:
            plot['title'] = config['title'] if len(config['plots']) == 1 else config['title'] + ' · ' + plot['title']
    return config,info

class App:
    def __init__(self,work):
        self.work=work.resolve(); self.work.mkdir(parents=True,exist_ok=True)
        self.token=secrets.token_urlsafe(32); self.uploads={}; self.jobs={}; self.lock=threading.Lock()
        from gui.projects import restore_jobs
        restore_jobs(self)
    def upload(self,kind,data,encoding='utf-8-sig',delimiter=',',header_row=1,allow_selection=False,consume=False):
        if kind not in {'csv','xlsx','boundary'}: raise InputError('支持 CSV、XLSX 和 GeoJSON 面边界；旧版 XLS / XLSM 请另存为 XLSX')
        size=data.stat().st_size if isinstance(data,Path) and data.is_file() else (len(data) if isinstance(data,bytes) else 0)
        if not 0<size<=MAX_BYTES: raise InputError(f'文件为空或超过当前 {MAX_BYTES//(1024*1024)} MB 上限')
        uid=secrets.token_hex(12); ext={'csv':'.csv','xlsx':'.xlsx','boundary':'.geojson'}[kind]
        path=self.work/'inputs'/(uid+ext); path.parent.mkdir(exist_ok=True)
        normalized=self.work/'inputs'/(uid+'-normalized.csv')
        created=False
        try:
            with path.open('xb') as target:
                created=True
                if isinstance(data,Path):
                    with data.open('rb') as source: shutil.copyfileobj(source,target,length=1024*1024)
                else: target.write(data)
            if consume and isinstance(data,Path): data.unlink()
            if kind=='xlsx':
                info=inspect_xlsx(path)
                self.uploads[uid]={'path':path,'original_path':path,'kind':kind,'info':None,'encoding':encoding,'delimiter':delimiter}
                return {'id':uid,'format':'xlsx','needs_selection':True,**info}
            elif kind=='csv':
                inspected=inspect_csv(path,encoding=encoding,delimiter=delimiter)
                try:
                    info=read_table(path,encoding=encoding,delimiter=delimiter,header_row=header_row,destination=normalized)
                    if info['rows']>MAX_ROWS: raise InputError('界面最多支持 200,000 行')
                except InputError as error:
                    if not allow_selection or inspected['rows']<2: raise
                    self.uploads[uid]={'path':path,'original_path':path,'kind':kind,'info':None,'encoding':encoding,'delimiter':delimiter}
                    return {'id':uid,**inspected,'needs_selection':True,'selection_error':str(error)}
                self.uploads[uid]={'path':normalized,'original_path':path,'kind':kind,'info':info,'encoding':encoding,'delimiter':delimiter}
                info={**inspected,**info}
            else:
                obj=json.loads(path.read_text(encoding='utf-8-sig'))
                if not isinstance(obj,dict) or obj.get('type') not in {'FeatureCollection','Feature','Polygon','MultiPolygon'}:
                    raise InputError('研究区轮廓必须是 GeoJSON 面文件（Polygon / MultiPolygon）')
                info={'type':obj['type']}
                self.uploads[uid]={'path':path,'kind':kind}
        except Exception:
            if created: path.unlink(missing_ok=True); normalized.unlink(missing_ok=True)
            raise
        return {'id':uid,**{k:v for k,v in info.items() if k!='csv_path'}}
    def select_table(self,req):
        item=self.uploads.get(req.get('id'))
        if not item or item['kind'] not in {'csv','xlsx'}: raise InputError('请重新选择要读取的表格文件')
        # Each selection has an immutable ID. A late request cannot silently
        # replace a newer sheet/header selection used by another job.
        uid=secrets.token_hex(12); destination=self.work/'inputs'/(uid+'-normalized.csv')
        try:
            info=read_table(item['original_path'],encoding=req.get('encoding',item.get('encoding','utf-8-sig')),delimiter=req.get('delimiter',item.get('delimiter',',')),sheet=req.get('sheet'),header_row=req.get('header_row',1),destination=destination)
            if info['rows']>MAX_ROWS: raise InputError('界面最多支持 200,000 行')
        except Exception:
            destination.unlink(missing_ok=True); raise
        self.uploads[uid]={**item,'path':destination,'info':info,'encoding':req.get('encoding',item.get('encoding','utf-8-sig')),'delimiter':req.get('delimiter',item.get('delimiter',','))}
        return {'id':uid,**{k:v for k,v in info.items() if k!='csv_path'}}
    def submit(self,req):
        config,info=build_config(req,self.uploads)
        with self.lock:
            if any(j['state'] in {'queued','validating','running'} for j in self.jobs.values()): raise InputError('已有任务正在运行，请等待完成或先取消')
            jid=time.strftime('%Y%m%d-%H%M%S')+'-'+secrets.token_hex(3)
            folder=self.work/'jobs'/jid; folder.mkdir(parents=True)
            # Self-contained input/config snapshot; exports can be rerun after moving.
            specs=config['inputs']+[config['boundary']] if req['mode']=='gis' else [config['input']]
            inpdir=folder/'input'; inpdir.mkdir()
            for i,spec in enumerate(specs):
                source=Path(spec['path']); target=inpdir/(f'{i:02d}'+source.suffix)
                shutil.copy2(source,target); spec['path']=str(target.relative_to(folder))
            import_report={k:v for k,v in info.items() if k not in {'csv_path','sample'}}
            import_report['record_unit']=config.get('record_unit',checked_text(req.get('record_unit'),'每行含义'))
            import_report['source']='synthetic_demo' if req['source']=='demo' else 'user_upload'
            if req['source']=='upload':
                original=self.uploads[req['csv_id']]['original_path']
                shutil.copy2(original,inpdir/('original'+original.suffix))
            (folder/'import_report.json').write_text(json.dumps(import_report,ensure_ascii=False,indent=2),encoding='utf-8')
            (folder/'config.json').write_text(json.dumps(config,ensure_ascii=False,indent=2),encoding='utf-8')
            job={'id':jid,'state':'queued','mode':req['mode'],'source':req['source'],'rows':info['rows'],'created_at':time.time(),'folder':folder,'process':None,'cancel':False,'error':'','files':[],'counts':None}
            job.update(import_warnings=info.get('warnings',[]),projection_note=info.get('projection_note',''),map_crs=info.get('map_crs'),grouping_note=info.get('grouping_note',''),group_count=info.get('group_count'),boundary_mode=info.get('boundary_mode'),boundary_note=info.get('boundary_note',''),record_unit=config.get('record_unit',import_report['record_unit']))
            (folder/'request.json').write_text(json.dumps(req,ensure_ascii=False,indent=2),encoding='utf-8')
            self.jobs[jid]=job
            from gui.projects import persist_job
            persist_job(job)
            threading.Thread(target=self.execute,args=(job,),daemon=True).start()
            return self.public(job)
    def execute(self,job):
        folder=job['folder']; env=os.environ.copy()
        env['PYTHONPATH']=str(ROOT/'src')+os.pathsep+env.get('PYTHONPATH','')
        env['PROJ_NETWORK']='OFF'
        env['MPLBACKEND']='Agg'; env['MPLCONFIGDIR']=str(self.work/'font-cache'); env['PYTHONIOENCODING']='utf-8'; env['PYTHONUTF8']='1'
        stages=[('validating','validate' if job['mode']=='gis' else 'stats-validate'),('running','run' if job['mode']=='gis' else 'stats')]
        if job['mode']=='research': stages=[('running','research')]
        try:
            with (folder/'run.log').open('w',encoding='utf-8') as log:
                for state,command in stages:
                    if job['cancel']: raise InputError('已取消任务')
                    job['state']=state
                    args=[sys.executable,'-m','poi_audit',command,'--config',str(folder/'config.json')]
                    if command=='research': args=[sys.executable,'-m','gui.research','--config',str(folder/'config.json')]
                    if command=='validate':args+=['--for-render']
                    if state=='running':args+=['--output',str(folder/'output')]
                    p=subprocess.Popen(args,stdout=log,stderr=subprocess.STDOUT,env=env,cwd=ROOT)
                    job['process']=p
                    while p.poll() is None:
                        if job['cancel']:p.terminate()
                        time.sleep(.15)
                    job['process']=None
                    if job['cancel']:raise InputError('已取消任务；没有把部分结果标记为成功')
                    log.flush()
                    diagnostics=self.log_diagnostics(folder/'run.log')
                    if diagnostics.get('counts') is not None:job['counts']=diagnostics['counts']
                    if diagnostics.get('preflight') is not None:job['preflight']=diagnostics['preflight']
                    job['display_warnings']=diagnostics['warnings']
                    if p.returncode: raise InputError(diagnostics['error'] or '计算未完成；请展开运行详情查看原因')
            manifest=json.loads((folder/'output'/'manifest.json').read_text(encoding='utf-8'))
            job['counts']=manifest.get('counts'); job['warnings']=job.get('import_warnings',[])+manifest.get('warnings',[])
            job['files']=[{'name':p.name,'size':p.stat().st_size} for p in sorted((folder/'output').iterdir()) if p.is_file()]
            bundle=folder/'result.zip'
            with zipfile.ZipFile(bundle,'w',zipfile.ZIP_DEFLATED) as z:
                for p in sorted(folder.rglob('*')):
                    if job['cancel']: raise InputError('已取消任务，停止打包结果')
                    if p.is_file() and p!=bundle:z.write(p,arcname=str(p.relative_to(folder)))
            with self.lock:
                if job['cancel']: raise InputError('已取消任务，结果未标记为成功')
                job['state']='complete'
        except Exception as e:
            job['state']='cancelled' if job['cancel'] else 'failed'; job['error']=str(e); job['files']=[]
        finally:
            job['finished_at']=time.time()
            from gui.projects import persist_job
            persist_job(job)
    @staticmethod
    def log_diagnostics(path):
        text=path.read_text(encoding='utf-8',errors='replace') if path.exists() else ''
        result={'error':'','warnings':[],'counts':None,'preflight':None}
        error_lines=[]; in_error=False
        for line in text.splitlines():
            if line.startswith('{'):
                try:
                    data=json.loads(line)
                    if isinstance(data,dict):
                        if 'counts' in data:result['counts']=data['counts']
                        if data.get('preflight') is not None:result['preflight']=data['preflight']
                except ValueError:pass
            if line.startswith('ERROR:'):
                in_error=True; error_lines=[line.removeprefix('ERROR:').strip()]
            elif in_error and not line.startswith(('findfont:', '{')):
                error_lines.append(line)
        result['error']='\n'.join(error_lines).strip()[-5000:]
        if 'findfont:' in text:
            result['warnings'].append('字体提示：系统已回退到可用字体或字重。这类提示影响外观，不是坐标投影失败的原因')
        report=result.get('preflight')
        if report:result['warnings']+=report.get('warnings',[])
        return result
    def public(self,job):
        result={k:v for k,v in job.items() if k not in {'folder','process','cancel'}}
        log=job['folder']/'run.log'
        if log.exists():result['log']=log.read_text(encoding='utf-8',errors='replace')[-12000:]
        return result

class Handler(BaseHTTPRequestHandler):
    server_version='POIStudio/0.6.1-rc.1'
    def log_message(self,fmt,*args): pass
    @property
    def app(self): return self.server.app
    def send(self,status,data,ctype='application/json; charset=utf-8',extra=None,report=False):
        if isinstance(data,(dict,list)):data=json.dumps(data,ensure_ascii=False).encode()
        if isinstance(data,str):data=data.encode()
        self.send_response(status); self.send_header('Content-Type',ctype); self.send_header('Content-Length',str(len(data)))
        self.send_header('Cache-Control','no-store'); self.send_header('X-Content-Type-Options','nosniff');self.send_header('Referrer-Policy','no-referrer')
        self.send_header('Content-Security-Policy',"default-src 'self'; script-src 'self'; style-src "+("'self' 'unsafe-inline'" if report else "'self'")+"; img-src 'self' data:; connect-src 'self'; frame-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'")
        if extra:
            for k,v in extra.items():self.send_header(k,v)
        self.end_headers()
        try:self.wfile.write(data)
        except (BrokenPipeError,ConnectionResetError):pass
        if status>=400 and self.command=='POST':
            # Rejecting before reading a body can cause Windows to reset TCP,
            # hiding the useful error response. Send FIN first, then drain only
            # a bounded amount/time; never accept or process the rejected body.
            try:
                self.wfile.flush()
                self.connection.shutdown(socket.SHUT_WR)
                self.connection.settimeout(.15)
                remaining=65536; deadline=time.monotonic()+.15
                while remaining>0 and time.monotonic()<deadline:
                    chunk=self.connection.recv(min(8192,remaining))
                    if not chunk:break
                    remaining-=len(chunk)
            except OSError:pass
    def authorized(self,post=False):
        host=self.headers.get('Host','')
        expected=f'127.0.0.1:{self.server.server_port}'
        if host!=expected:return False
        cookies=self.headers.get('Cookie','').split(';')
        valid=any(c.strip()=='poi_session='+self.app.token for c in cookies)
        if post:
            valid=valid and self.headers.get('X-POI-Token')==self.app.token and self.headers.get('Origin')=='http://'+expected
        return valid
    def do_GET(self):
        parsed=urlparse(self.path); path=unquote(parsed.path)
        if parsed.path=='/' and parse_qs(parsed.query).get('token')==[self.app.token] and self.headers.get('Host')==f'127.0.0.1:{self.server.server_port}':
            return self.send(302,b'',extra={'Location':'/','Set-Cookie':f'poi_session={self.app.token}; HttpOnly; SameSite=Strict; Path=/'})
        if not self.authorized():return self.send(403,{'error':'请使用启动窗口中的完整地址打开界面'})
        try:
            if path=='/research':
                data=(STATIC/'research.html').read_text(encoding='utf-8').replace('__TOKEN__',self.app.token)
                return self.send(200,data,'text/html; charset=utf-8')
            if path=='/':
                data=(STATIC/'index.html').read_text(encoding='utf-8').replace('__TOKEN__',self.app.token)
                return self.send(200,data,'text/html; charset=utf-8')
            if path.startswith('/static/'):
                return self.file(STATIC,path.removeprefix('/static/'))
            if path=='/api/info':
                return self.send(200,{'version':'0.6.1-rc.1','engine':'0.3.0','dependencies':{x:importlib.util.find_spec(x) is not None for x in ['numpy','matplotlib','shapely','pyproj','openpyxl']},'work':str(self.app.work),'max_bytes':MAX_BYTES,'max_rows':MAX_ROWS,'active_job':next(({'id':j['id'],'mode':j['mode'],'source':j['source']} for j in self.app.jobs.values() if j['state'] in {'queued','validating','running'}),None)})
            if path=='/api/projects':
                from gui.projects import list_projects
                return self.send(200,list_projects(self.app))
            if path=='/api/history':
                return self.send(200,[self.app.public(j) for j in sorted(self.app.jobs.values(),key=lambda j:j.get('created_at',0),reverse=True)][:100])
            if path.startswith('/api/template/'):
                filename=path.rsplit('/',1)[-1]
                if filename not in {'gis.xlsx','stats.xlsx','boundary.geojson','research.xlsx'}: raise InputError('模板不存在')
                return self.file(ROOT/'examples' if filename=='boundary.geojson' else ROOT/'examples'/'templates',filename,True)
            if path=='/api/demo':
                mode=parse_qs(parsed.query).get('mode',['gis'])[0]
                if mode not in {'gis','stats'}:raise InputError('模式无效')
                p=ROOT/('examples/provider_alpha.csv' if mode=='gis' else 'examples/statistics/metrics.csv')
                return self.send(200,{'filename':p.name,**table_info(p)})
            if path.startswith('/api/jobs/'):
                job=self.app.jobs.get(path.rsplit('/',1)[-1])
                if not job:raise InputError('任务不存在；重启后请在本地 gui-work/jobs 查看历史结果')
                return self.send(200,self.app.public(job))
            if path.startswith('/preview/'):
                names={'gis':('small_atlas_example_output','atlas_01_spatial_p01.png'),'stats':('statistics_example_output','stats_scatter.png')}
                a,b=names.get(path.rsplit('/',1)[-1],('',''))
                if not a:raise InputError('预览不存在')
                return self.file(ROOT/a,b)
            if path.startswith('/output/'):
                parts=path.split('/',3)
                if len(parts)!=4 or parts[2] not in self.app.jobs:raise InputError('任务不存在')
                job=self.app.jobs[parts[2]]
                if job['state']!='complete':raise InputError('任务尚未完成')
                if parts[3]=='result.zip':return self.file(job['folder'],'result.zip',True)
                return self.file(job['folder']/'output',parts[3],parse_qs(parsed.query).get('download')==['1'])
            return self.send(404,{'error':'页面不存在'})
        except (InputError, OSError, ValueError) as e:self.send(400,{'error':str(e)})
    def file(self,base,name,download=False):
        if not name or '\\' in name:raise InputError('文件路径无效')
        p=(base/name).resolve()
        if not p.is_relative_to(base.resolve()) or not p.is_file():raise InputError('文件不存在')
        extra={}
        if download or p.suffix in {'.csv','.json','.npz','.zip'}:extra['Content-Disposition']='attachment'
        self.send(200,p.read_bytes(),mimetypes.guess_type(str(p))[0] or 'application/octet-stream',extra,report=p.suffix=='.html')
    def do_POST(self):
        if not self.authorized(True):return self.send(403,{'error':'拒绝跨站请求或会话已失效，请从启动器重新打开'})
        try:
            self.close_connection=True
            lengths=self.headers.get_all('Content-Length',[])
            if self.headers.get('Transfer-Encoding') or len(lengths)!=1:
                raise InputError('请求长度声明无效，请重新选择文件')
            size=int(lengths[0]); parsed=urlparse(self.path)
            if parsed.path=='/api/upload':
                q=parse_qs(parsed.query)
                self.connection.settimeout(90)
                path=receive_upload(self.rfile,size,self.app.work/'inputs',MAX_BYTES)
                try:
                    result=self.app.upload(q.get('kind',['csv'])[0],path,q.get('encoding',['utf-8-sig'])[0],q.get('delimiter',[','])[0],int(q.get('header_row',[1])[0]),allow_selection=True,consume=True)
                finally: path.unlink(missing_ok=True)
                return self.send(200,result)
            if not 0<size<=min(MAX_BYTES,MAX_JSON_BYTES):raise InputError('设置请求为空或超过 1 MB')
            raw=self.rfile.read(size)
            if len(raw)!=size: raise InputError('设置请求没有传输完整，请重试')
            req=json.loads(raw)
            if not isinstance(req,dict):raise InputError('请求格式无效')
            if parsed.path=='/api/projects/save':
                from gui.projects import save_project
                return self.send(200,save_project(self.app,req))
            if parsed.path=='/api/projects/load':
                from gui.projects import load_project
                return self.send(200,load_project(self.app,req))
            if parsed.path=='/api/table':return self.send(200,self.app.select_table(req))
            if parsed.path=='/api/jobs':return self.send(202,self.app.submit(req))
            if parsed.path.startswith('/api/cancel/'):
                job=self.app.jobs.get(parsed.path.rsplit('/',1)[-1])
                if not job:raise InputError('任务不存在')
                with self.app.lock:
                    if job['state'] in {'queued','validating','running'}:job['cancel']=True
                return self.send(200,self.app.public(job))
            return self.send(404,{'error':'接口不存在'})
        except (InputError,ValueError,OSError,KeyError,TypeError) as e:self.send(400,{'error':str(e)})


def main(argv=None):
    global MAX_BYTES
    parser=argparse.ArgumentParser();parser.add_argument('--no-browser',action='store_true');parser.add_argument('--port',type=int,default=8765);parser.add_argument('--work',type=Path,default=ROOT/'gui-work')
    parser.add_argument('--max-file-mb',default=100,help='File upload limit in MiB, 1–200 (default: 100)')
    args=parser.parse_args(argv)
    try: MAX_BYTES=choose_upload_limit(args.max_file_mb)
    except InputError as error: parser.error(str(error))
    table_import.MAX_BYTES=MAX_BYTES
    app=App(args.work)
    server=None
    for port in range(args.port,args.port+20):
        try:server=ThreadingHTTPServer(('127.0.0.1',port),Handler);break
        except OSError:continue
    if server is None:raise SystemExit('No free loopback port; close the previous POI Studio window and retry.')
    server.app=app;server.daemon_threads=True
    url=f'http://127.0.0.1:{server.server_port}/?token={app.token}'
    print('POI Studio 0.6.1-rc.1 | engine 0.3.0\nLocal-only. Close this terminal to stop.\n'+url+'\nResults: '+str(app.work),flush=True)
    if not args.no_browser:threading.Timer(.5,lambda:webbrowser.open(url)).start()
    try:server.serve_forever()
    except KeyboardInterrupt:pass
    finally:
        for job in app.jobs.values():
            job['cancel']=True
            p=job.get('process')
            if p and p.poll() is None:p.terminate()
        server.server_close()
if __name__=='__main__':main()
