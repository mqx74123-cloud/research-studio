'use strict';
// Shared controls extend the established import/mapping workflow.
const originalPayload = payload;
payload = function () {
  return {...originalPayload(), export: {width_mm:$('pub-width').value,height_mm:$('pub-height').value,dpi:$('pub-dpi').value,svg_text:$('pub-svg').value}};
};
let savedHistory=[];
const originalVisibility=updateVisibility;
updateVisibility=function(){originalVisibility();if(state.loadedSnapshot&&state.source==='upload'){for(const id of ['table-selection','apply-table','header-help','table-state','csv-settings'])$(id).hidden=true;}};
$('csv-file').addEventListener('change',()=>{state.loadedSnapshot=false;});
async function refreshSaved() {
  const projects=await api('/api/projects');
  $('saved-projects').replaceChildren(new Option('请选择已保存方案',''));
  for(const p of projects.filter(p=>p.mode!=='research')) $('saved-projects').add(new Option(p.name,p.id));
  savedHistory=await api('/api/history');
  $('saved-history').replaceChildren(new Option('请选择历史结果',''));
  for(const j of savedHistory.filter(j=>j.mode!=='research'&&j.state==='complete')) $('saved-history').add(new Option(j.id+' · '+j.rows+'行',j.id));
}
$('save-project').onclick=async()=>{
  if(state.busy||state.loading)return;
  try {
    const request=payload(), invalid=validatePayload(request);
    if(invalid) throw Error(invalid.text);
    const controls={};
    document.querySelectorAll('#config-form input,#config-form select').forEach(e=>{if(e.id&&e.type!=='file')controls[e.id]=e.type==='checkbox'?e.checked:e.value;});
    const p=await api('/api/projects/save',{name:$('project-name').value,request,view:{chart:state.chart,uploadName:state.uploadName,controls}});
    $('project-message').textContent='已保存方案与数据快照：'+p.name;await refreshSaved();
  }catch(e){showError(e);}
};
$('load-project').onclick=async()=>{
  if(state.busy||state.loading)return;
  try {
    const result=await api('/api/projects/load',{id:$('saved-projects').value}),r=result.request,v=result.view||{};
    clearResults();state.mode=r.mode;state.source=r.source;state.chart=v.chart||(r.mode==='gis'?'gis':r.plots[0]);
    state.uploadName=v.uploadName||result.name;state.csv=r.csv_id?{id:r.csv_id}:null;state.boundary=r.boundary_id?{id:r.boundary_id}:null;
    state.loadedSnapshot=r.source==='upload';
    state.tablePending=false;state.loading=false;
    if(r.source==='demo')await loadDemo();else fillFields({...result.table,id:r.csv_id});
    for(const[id,value]of Object.entries(v.controls||{})){const e=$(id);if(e&&e.type!=='file'){if(e.type==='checkbox')e.checked=value;else e.value=value;}}
    for(const[key,value]of Object.entries(r.fields))if($('field-'+key))$('field-'+key).value=value;
    $('mapping-confirmed').checked=false;$('crs-confirmed').checked=false;$('extent-preview-confirmed').checked=false;
    $('boundary-state').textContent=state.boundary?'已恢复研究区边界快照':'还没选择研究区轮廓';
    // A loaded project is an immutable selection; changing sheets requires importing the source again.
    $('table-selection').hidden=true;$('apply-table').hidden=true;
    $('project-name').value=result.name;goStep(3);dirty();
    $('project-message').textContent='已恢复数据快照与设置，请核对字段及坐标声明后生成。';
  }catch(e){showError(e);}
};
$('refresh-saved').onclick=()=>refreshSaved().catch(showError);
$('open-history').onclick=()=>{if(state.busy||state.loading)return;const job=savedHistory.find(j=>j.id===$('saved-history').value);if(job){clearResults();renderComplete(job);}};
const originalRenderComplete=renderComplete;
renderComplete=function(job){originalRenderComplete(job);refreshSaved().catch(showError);};
refreshSaved().catch(showError);

// Move the selected, normalized table between pages without re-reading its sheet.
for(const button of document.querySelectorAll('[data-research-chart]')){
  button.addEventListener('click',()=>{
    if(state.busy||state.loading){showError(Error('请等待当前导入或出图完成，再切换图形'));return;}
    if(state.source==='upload'&&state.csv&&state.tablePending){showError(Error('请先按所选工作表和表头读取数据，再切换图形'));return;}
    try{
      sessionStorage.removeItem('poi-research-transfer');
      if(state.source==='upload'&&state.csv&&state.info){
        sessionStorage.setItem('poi-research-transfer',JSON.stringify({token,chart:button.dataset.researchChart,id:state.csv.id,format:state.csv.format,info:state.info,recordUnit:$('record-unit').value}));
      }
      window.location.assign('/research?chart='+encodeURIComponent(button.dataset.researchChart));
    }catch(e){showError(Error('浏览器未能暂存表格，请允许此本地页面使用会话存储后重试。'));}
  });
}
