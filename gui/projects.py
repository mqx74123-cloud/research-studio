"""Local immutable project snapshots and restartable job history."""
import copy
import json
from pathlib import Path
import re
import secrets
import shutil
import time
from .table_import import InputError

def _json(path):
    return json.loads(path.read_text(encoding='utf-8'))

def _write(path, value):
    temp = path.with_suffix('.pending')
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')
    temp.replace(path)

def persist_job(job):
    value = {k:v for k,v in job.items() if k not in {'folder','process','cancel'}}
    _write(job['folder']/'status.json', value)

def restore_jobs(app):
    root = app.work/'jobs'
    if not root.exists(): return
    for folder in root.iterdir():
        if not folder.is_dir() or not re.fullmatch(r'\d{8}-\d{6}-[a-f0-9]{6}',folder.name): continue
        try:
            value=_json(folder/'status.json')
            if value.get('id') != folder.name: continue
            if value.get('state')=='complete' and not (folder/'output'/'manifest.json').is_file(): continue
            if value.get('state') in {'queued','running','validating'}:
                value.update(state='failed',error='上次服务在任务完成前关闭，请重新生成',files=[])
            value.update(folder=folder,process=None,cancel=False)
            app.jobs[folder.name]=value
        except (OSError,ValueError,TypeError):
            continue

def list_projects(app):
    root=app.work/'projects'
    if not root.exists(): return []
    result=[]
    for path in root.glob('*/project.json'):
        try:
            obj=_json(path)
            result.append({k:obj[k] for k in ['id','name','created_at','mode']})
        except (OSError,ValueError,KeyError,TypeError): continue
    return sorted(result,key=lambda v:v['created_at'],reverse=True)[:100]

def save_project(app, body):
    from .server import build_config
    req=body.get('request')
    if not isinstance(req,dict): raise InputError('没有可保存的制图配置')
    build_config(req,app.uploads)  # Require a complete, valid mapping.
    view=body.get('view',{})
    if not isinstance(view,dict): raise InputError('界面设置无效')
    name=body.get('name') or req.get('title') or '未命名方案'
    if not isinstance(name,str) or not name.strip() or len(name)>180: raise InputError('方案名称应为1–180字')
    pid=secrets.token_hex(12)
    folder=app.work/'projects'/pid
    folder.mkdir(parents=True)
    snapshots={}
    for key in ['csv_id','boundary_id']:
        uid=req.get(key)
        if not uid: continue
        item=app.uploads.get(uid)
        if not item: raise InputError('数据会话失效，请重新导入')
        record={k:v for k,v in item.items() if k not in {'path','original_path'}}
        for field in ['path','original_path']:
            if field not in item: continue
            src=Path(item[field]); name_in_snapshot=key+'-'+field+src.suffix
            shutil.copy2(src,folder/name_in_snapshot)
            record[field]=name_in_snapshot
        snapshots[key]=record
    document=dict(id=pid,name=name.strip(),created_at=time.time(),mode=req['mode'],
                  request=copy.deepcopy(req),view=view,snapshots=snapshots)
    _write(folder/'project.json',document)
    return {k:document[k] for k in ['id','name','created_at','mode']}

def load_project(app, body):
    pid=body.get('id','')
    if not isinstance(pid,str) or not re.fullmatch(r'[a-f0-9]{24}',pid): raise InputError('方案编号无效')
    folder=(app.work/'projects'/pid).resolve()
    try: document=_json(folder/'project.json')
    except (OSError,ValueError): raise InputError('方案不存在或文件已损坏')
    req=copy.deepcopy(document['request'])
    for key,record in document['snapshots'].items():
        item=copy.deepcopy(record)
        for field in ['path','original_path']:
            if field not in item: continue
            path=(folder/item[field]).resolve()
            if not path.is_relative_to(folder) or not path.is_file(): raise InputError('方案中的数据快照缺失')
            item[field]=path
        uid=secrets.token_hex(12)
        app.uploads[uid]=item; req[key]=uid
    return dict(id=pid,name=document['name'],request=req,view=document.get('view',{}),
                table=app.uploads.get(req.get('csv_id'),{}).get('info'))
