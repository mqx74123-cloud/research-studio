'use strict';
const $=id=>document.getElementById(id);
const token=document.querySelector('meta[name="poi-token"]').content;
const state={table:null,source:null,busy:false,job:null,files:[],revision:0,timer:null,history:[]};
const roles={heatmap:{label:'行标签列（必须唯一）'},forest:{label:'目标 / 方法标签（必须唯一）',estimate:'估计值',lower:'区间下限',upper:'区间上限'},dumbbell:{label:'对照对象（必须唯一）',before:'第一组数值',after:'第二组数值'},multi_raincloud:{group:'分组列（最多12组）'}};
const help={heatmap:'宽表：每行一个对象，每列一个指标。重复标签需要先明确聚合；缺失单元格显示NA。',forest:'每行一个估计值及其区间，直接使用你的统计结果。不同方法请写入行标签。',dumbbell:'每行同一对象的两个数值。两列须使用相同单位和尺度。',multi_raincloud:'每行一条原始观测，选择分组列和多项指标；各指标分别处理缺失值。'};
async function api(path,body,raw=false){const response=await fetch(path,body===undefined?{}:{method:'POST',headers:{'X-POI-Token':token,'Content-Type':raw?'application/octet-stream':'application/json'},body:raw?body:JSON.stringify(body)});const data=await response.json();if(!response.ok)throw Error(data.error||'本机服务请求失败');return data;}
function error(e){$('form-error').textContent=e.message||String(e);$('form-error').hidden=false;}
function note(t){$('status').textContent=t;}
function busy(value){state.busy=value;document.querySelectorAll('#research-form input,#research-form select,#research-form button').forEach(e=>e.disabled=value);if(!value)$('sheet').disabled=state.source?.format!=='xlsx';$('cancel').disabled=false;$('cancel').hidden=!value;$('generate').textContent=value?'正在生成…':'生成图件';}
function resetResults(){if(state.timer)clearTimeout(state.timer);state.timer=null;state.job=null;state.files=[];$('figure').hidden=true;$('figure').removeAttribute('src');$('empty-preview').hidden=false;$('pages').replaceChildren();$('download-links').replaceChildren();$('file-list').replaceChildren();$('log').textContent='';}
function mappings(preserve=false){const saved={};if(preserve)for(const e of document.querySelectorAll('[data-role]'))saved[e.dataset.role]=e.value;const selected=preserve?[...document.querySelectorAll('#metrics input:checked')].map(e=>e.value):[];const chart=$('chart').value;updateTemplateTitle();$('chart-help').textContent=help[chart];const box=$('field-mappings');box.replaceChildren();for(const [key,label] of Object.entries(roles[chart])){const l=document.createElement('label');l.textContent=label;const s=document.createElement('select');s.dataset.role=key;s.id='r-'+key;s.add(new Option('请选择对应列',''));for(const name of state.table?.headers||[])s.add(new Option(name,name));s.value=saved[key]||'';l.append(s);box.append(l);}const metrics=$('metrics');metrics.replaceChildren();for(const name of state.table?.headers||[]){const l=document.createElement('label');l.className='check';const check=document.createElement('input');check.type='checkbox';check.value=name;check.checked=selected.includes(name);l.append(check,document.createTextNode(name));metrics.append(l);}$('metrics-box').hidden=!['heatmap','multi_raincloud'].includes(chart);$('color-box').hidden=chart!=='heatmap';$('interval-box').hidden=chart!=='forest';$('comparison-options').hidden=!['forest','dumbbell'].includes(chart);$('mapping-confirmed').checked=false;}
function showTable(info){state.table=info;$('table-note').textContent=`${info.rows} 行 · ${info.headers.length} 列`+(info.warnings?.length?'\n'+info.warnings.join('\n'):'');const table=document.createElement('table'),thead=document.createElement('thead'),head=document.createElement('tr');for(const h of info.headers){const th=document.createElement('th');th.textContent=h;head.append(th);}thead.append(head);table.append(thead);const body=document.createElement('tbody');for(const row of (info.sample||[]).slice(0,5)){const tr=document.createElement('tr');for(const h of info.headers){const td=document.createElement('td');td.textContent=row[h]??'';tr.append(td);}body.append(tr);}table.append(body);$('table-preview').replaceChildren(table);mappings();}
async function upload(file){if(!file)return;const rev=++state.revision;state.table=null;state.source=null;resetResults();$('table-preview').replaceChildren();$('table-note').textContent='';$('table-options').hidden=true;$('record-unit').value='';$('form-error').hidden=true;mappings();note('请选择有效的表格。');if(file.size>100*1024*1024)throw Error('文件超过100 MB');const kind=/\.xlsx$/i.test(file.name)?'xlsx':'csv';if(!/\.(xlsx|csv|tsv|txt)$/i.test(file.name))throw Error('请选择.xlsx、.csv、.tsv或.txt');busy(true);note('正在读取文件…');try{const delimiter=$('delimiter').value==='tab'?'\t':$('delimiter').value;const res=await api('/api/upload?kind='+kind+'&encoding='+encodeURIComponent($('encoding').value)+'&delimiter='+encodeURIComponent(delimiter),file,true);if(rev!==state.revision)return;state.source=res;$('header').value='1';$('sheet').replaceChildren();for(const s of res.sheets||[])$('sheet').add(new Option(s.name,s.name));$('table-options').hidden=false;$('sheet').disabled=kind!=='xlsx';if(!res.needs_selection)showTable(res);else{$('table-preview').replaceChildren();$('table-note').textContent='请选择工作表和表头行，再点击读取。';mappings();}note('文件已在本机打开。');}finally{busy(false);}}
function payload(){const fields={};for(const e of document.querySelectorAll('[data-role]'))fields[e.dataset.role]=e.value;return{mode:'research',source:'upload',csv_id:state.table?.id,chart:$('chart').value,fields,metrics:[...document.querySelectorAll('#metrics input:checked')].map(e=>e.value),mapping_confirmed:$('mapping-confirmed').checked,record_unit:$('record-unit').value,title:$('title').value,interval_label:$('interval-label').value,color_mode:$('color-mode').value,value_unit:$('value-unit').value,reference:$('reference').value,style:$('style').value,export:{width_mm:$('width').value,height_mm:$('height').value,dpi:$('dpi').value,svg_text:$('svg-text').value}};}
function outputUrl(name){return '/output/'+encodeURIComponent(state.job.id)+'/'+encodeURIComponent(name);}
function link(text,name){const a=document.createElement('a');a.textContent=text;a.href=outputUrl(name)+'?download=1';a.download=name;return a;}
function selectPage(name){$('figure').src=outputUrl(name);$('figure').hidden=false;$('empty-preview').hidden=true;const stem=name.slice(0,-4),pdf=stem.replace(/_p\d+$/,'')+'.pdf';const links=$('download-links');links.replaceChildren(link('下载PNG',name),link('下载SVG',stem+'.svg'),link('完整PDF',pdf),link('全部结果ZIP','result.zip'));const a=document.createElement('a');a.textContent='查看报告';a.href=outputUrl('report.html');a.target='_blank';a.rel='noopener';links.append(a);}
function complete(job){state.job=job;state.files=job.files||[];const pages=state.files.filter(f=>f.name.endsWith('.png'));$('pages').replaceChildren();for(const p of pages){const b=document.createElement('button');b.type='button';b.textContent=p.name;b.onclick=()=>selectPage(p.name);$('pages').append(b);}if(pages.length)selectPage(pages[0].name);$('file-list').replaceChildren();for(const f of state.files)$('file-list').append(link(f.name,f.name));$('log').textContent=job.log||'';note('已生成 '+pages.length+' 页。计数：'+JSON.stringify(job.counts));}
async function poll(){const id=state.job?.id;if(!id)return;try{const job=await api('/api/jobs/'+id);if(state.job?.id!==id)return;state.job=job;note(job.state==='running'?'正在本机计算与绘图…':'任务状态：'+job.state);if(['complete','failed','cancelled'].includes(job.state)){busy(false);state.timer=null;if(job.state==='complete'){complete(job);await refresh();}else{resetResults();error(Error(job.error||'任务已取消'));}return;}state.timer=setTimeout(poll,900);}catch(e){note('连接暂时中断，继续查询任务状态。');state.timer=setTimeout(poll,2500);}}
async function refresh(){const projects=await api('/api/projects');$('project-select').replaceChildren(new Option('请选择已保存方案',''));for(const p of projects.filter(p=>p.mode==='research'))$('project-select').add(new Option(p.name,p.id));state.history=await api('/api/history');$('history-select').replaceChildren(new Option('请选择历史结果',''));for(const j of state.history.filter(j=>j.mode==='research'&&j.state==='complete'))$('history-select').add(new Option(j.id+' · '+j.rows+'行',j.id));}
function applyProject(result){resetResults();const req=result.request;state.source={id:req.csv_id};showTable({...result.table,id:req.csv_id});$('chart').value=req.chart;mappings();for(const [role,value]of Object.entries(req.fields))if($('r-'+role))$('r-'+role).value=value;for(const e of document.querySelectorAll('#metrics input'))e.checked=req.metrics.includes(e.value);for(const [id,key]of Object.entries({'record-unit':'record_unit',title:'title','interval-label':'interval_label','color-mode':'color_mode','value-unit':'value_unit',reference:'reference',style:'style'}))$(id).value=req[key]??'';for(const[id,key]of Object.entries({width:'width_mm',height:'height_mm',dpi:'dpi','svg-text':'svg_text'}))$(id).value=req.export?.[key]??'';$('mapping-confirmed').checked=false;$('project-name').value=result.name;$('table-options').hidden=true;note('已恢复方案与数据快照，请核对后生成。');}

// Never present a previous chart or its downloads as the current configuration.
function invalidateResults(){
  if(!state.busy&&(state.job||state.files.length)){
    resetResults();note('设置已更改，请重新生成图件。');
  }
}
function formEdited(event){
  if(state.busy||['project-name','project-select','history-select'].includes(event.target.id))return;
  invalidateResults();$('form-error').hidden=true;
}
$('research-form').oninput=formEdited;
$('research-form').onchange=formEdited;

$('file').onchange=()=>upload($('file').files[0]).catch(error);
$('read-table').onclick=async()=>{resetResults();$('form-error').hidden=true;busy(true);try{const res=await api('/api/table',{id:state.source?.id,sheet:$('sheet').value||undefined,header_row:Number($('header').value),encoding:$('encoding').value,delimiter:$('delimiter').value==='tab'?'\t':$('delimiter').value});showTable(res);note('已读取表格，请核对字段后生成。');}catch(e){error(e);}finally{busy(false);}};
for(const id of ['sheet','header','encoding','delimiter'])$(id).onchange=()=>{state.table=null;$('mapping-confirmed').checked=false;note('读取选项已改变，请重新读取/选择文件，确认数据后生成。');};
$('chart').onchange=()=>{invalidateResults();mappings();};
$('field-mappings').onchange=()=>{$('mapping-confirmed').checked=false;invalidateResults();};$('metrics').onchange=()=>{$('mapping-confirmed').checked=false;invalidateResults();};
$('research-form').onsubmit=async e=>{e.preventDefault();if(state.busy)return;$('form-error').hidden=true;if(!state.table){error(Error('请先读取表格'));return;}const req=payload();resetResults();busy(true);try{state.job=await api('/api/jobs',req);poll();}catch(e){busy(false);error(e);}};
$('cancel').onclick=async()=>{if(state.job)try{await api('/api/cancel/'+state.job.id,{});}catch(e){error(e);}};
$('save-project').onclick=async()=>{try{const p=await api('/api/projects/save',{name:$('project-name').value,request:payload()});note('方案已保存于本机：'+p.name);await refresh();}catch(e){error(e);}};
$('load-project').onclick=async()=>{try{applyProject(await api('/api/projects/load',{id:$('project-select').value}));}catch(e){error(e);}};
$('refresh-projects').onclick=()=>refresh().catch(error);
$('open-history').onclick=()=>{const job=state.history.find(j=>j.id===$('history-select').value);if(job){resetResults();complete(job);}};
$('load-demo').onclick=async()=>{try{const res=await fetch('/api/template/research.xlsx');if(!res.ok)throw Error('本地模板不可用');await upload(new File([await res.blob()],'research.xlsx'));$('record-unit').value='一条虚构示例观测';$('title').value='合成示例 · 非研究数据';}catch(e){error(e);}};
function updateTemplateTitle(){
  const names={heatmap:'热图 / 相关矩阵',forest:'森林图',dumbbell:'哑铃图',multi_raincloud:'多指标雨云图'};
  $('research-title').textContent=names[$('chart').value];
}
function openSelectedTemplate(){
  const selected=new URLSearchParams(window.location.search).get('chart');
  if(Object.prototype.hasOwnProperty.call(roles,selected))$('chart').value=selected;
  try{
    const saved=sessionStorage.getItem('poi-research-transfer');
    sessionStorage.removeItem('poi-research-transfer');
    if(saved){
      const transfer=JSON.parse(saved);
      if(transfer.token===token&&transfer.chart===selected&&transfer.id&&Array.isArray(transfer.info?.headers)){
        state.source={id:transfer.id,format:transfer.format||transfer.info.format};
        showTable({...transfer.info,id:transfer.id});
        $('record-unit').value=transfer.recordUnit||'';
        $('table-options').hidden=true;
        note('已带入刚才读取的表格，请选择这张图对应的列。');
      }
    }
  }catch(e){error(Error('未能恢复刚才的表格，请在此页面重新导入。'));}
  mappings();
}
openSelectedTemplate();refresh().catch(error);
