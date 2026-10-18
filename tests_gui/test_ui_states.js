/**
 * Dependency-free UI state regression tests. Run: node tests_gui/test_ui_states.js
 * This is a small DOM double, not a browser, layout, accessibility or OS test.
 * The synthetic fixtures exercise the table-import API's documented metadata.
 */
'use strict';
const fs = require('fs'), vm = require('vm'), assert = require('assert'), path = require('path');
const root = path.resolve(__dirname, '..');
const html = fs.readFileSync(path.join(root, 'gui/static/index.html'), 'utf8');
const tree = [], stack = [], voidTags = new Set(['meta','link','input','br','hr','img','source','area','base','col','embed','param','track','wbr']);
for (const match of html.matchAll(/<(\/?)([a-zA-Z][\w-]*)\b([^>]*?)\/?\s*>/g)) {
  const [, closing, rawTag, attributes] = match, tag = rawTag.toLowerCase();
  if (closing) {
    for (let index = stack.length - 1; index >= 0; index--) if (tree[stack[index]].tag === tag) { stack.length = index; break; }
    continue;
  }
  const attrs = {};
  for (const attr of attributes.matchAll(/([^\s=\/]+)(?:\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s"'=<>]+)))?/g)) attrs[attr[1]] = attr[2] ?? attr[3] ?? attr[4] ?? '';
  const index = tree.length;
  tree.push({tag, attrs, parent: stack.length ? stack[stack.length - 1] : null});
  if (!voidTags.has(tag)) stack.push(index);
}
const ids = tree.map(node => node.attrs.id).filter(Boolean);
assert.equal(new Set(ids).size, ids.length, 'HTML IDs must be unique');
assert(!tree.some(node => /^(?:https?:)?\/\//.test(node.attrs.src || node.attrs.href || '')), 'The UI must not load external resources');
class Element {
  constructor(tag,attrs={}) { this.tagName=tag.toUpperCase();this.attrs={...attrs};this.children=[];this.dataset={};this.style={};this.events={};this.hidden='hidden' in attrs;this.disabled='disabled'in attrs;this.checked='checked'in attrs;this.files=[];this._value=attrs.value||'';this.textContent='';this.id=attrs.id||'';this.type=attrs.type||'';this.content=attrs.content||'';this._classes=new Set((attrs.class||'').split(/\s+/).filter(Boolean));this.classList={contains:s=>this._classes.has(s),toggle:(s,value)=>{if(value===undefined)value=!this._classes.has(s);value?this._classes.add(s):this._classes.delete(s)}};for(const[k,v]of Object.entries(attrs)){if(k.startsWith('data-'))this.dataset[k.slice(5).replace(/-([a-z])/g,(_,c)=>c.toUpperCase())]=v} }
  get value(){return this._value}
  set value(v){v=String(v);this._value=this.tagName==='SELECT'&&!this.children.some(c=>c.value===v)?'':v}
  set className(v){this._classes=new Set(v.split(/\s+/))}
  get className(){return [...this._classes].join(' ')}
  append(...kids){for(const k of kids){this.children.push(k);k.parentElement=this;if(this.tagName==='SELECT'&&this.children.length===1)this._value=k.value}}
  replaceChildren(...kids){this.children=[];if(this.tagName==='SELECT')this._value='';this.append(...kids)}
  add(option){this.append(option)}
  setAttribute(k,v){this.attrs[k]=String(v)}
  removeAttribute(k){delete this.attrs[k]}
  getAttribute(k){return this.attrs[k]??null}
  addEventListener(k,fn){(this.events[k]??=[]).push(fn)}
  focus(){}scrollIntoView(){}showModal(){this.open=true}close(){this.open=false}
}
const all=tree.map(t=>new Element(t.tag,t.attrs)); tree.forEach((t,i)=>{if(t.parent!==null)all[t.parent].append(all[i])});
for(const el of all.filter(e=>e.tagName==='SELECT'))el._value=el.children.find(x=>'selected'in x.attrs)?.value||el.children[0]?.value||'';
function matches(el,selector){if(selector.includes(' ')){const p=selector.split(' ');if(!matches(el,p.pop()))return false;let parent=el.parentElement;while(parent){if(matches(parent,p.join(' ')))return true;parent=parent.parentElement}return false}if(selector.startsWith('#'))return el.id===selector.slice(1);if(selector.startsWith('.'))return el._classes.has(selector.slice(1));if(selector.startsWith('['))return selector.slice(1,-1) in el.attrs;return el.tagName===selector.toUpperCase()}
function allElements(){const found=[];function visit(el){found.push(el);for(const c of el.children)visit(c)}for(const el of all.filter(e=>!e.parentElement))visit(el);return found}
const document={getElementById:id=>all.find(e=>e.id===id)||null,querySelectorAll:selector=>allElements().filter(e=>selector.split(',').some(s=>matches(e,s))),querySelector:selector=>selector.startsWith('meta')?all.find(e=>e.tagName==='META'&&e.attrs.name==='poi-token'):allElements().find(e=>matches(e,selector)),createElement:tag=>new Element(tag)};
const requests=[];let respond=()=>({dependencies:{},work:'test-work'});
const context={document,window:{open(){}},Option:function(text,value){const el=new Element('option',{value});el.textContent=text;return el},fetch:async(path,opts)=>{requests.push([path,opts]);const body=await respond(path,opts);return {ok:true,json:async()=>body}},setTimeout:()=>1,URLSearchParams,console,assert};
vm.createContext(context);
const source=fs.readFileSync(path.join(root,'gui/static/app.js'),'utf8');
for (const [, id] of source.matchAll(/\$\('([^']+)'\)/g)) assert(ids.includes(id), 'Missing static DOM ID: ' + id);
assert(!(source + html).includes('EPSG:32650'), 'Must not hard-code a study-area projection');
vm.runInContext(source+'\nthis.test={state,$,payload,validatePayload,fillFields,chooseChart,goStep,updateVisibility,applyTable,tableSelectionChanged,updateSuggestions,needsGroupPagination,renderComplete,selectFigure,prettyName,pollJob,upload,switchSource};',context);
const t=context.test, $=t.$;
const info={id:'table-a',format:'xlsx',rows:40,headers:['经度','纬度','数值一','数值二','分组','道路名','编号'],sample:[{'经度':'117.2','纬度':'39.1','数值一':0,'数值二':'3.5','分组':'中心区','道路名':'A路','编号':'001'}],columns:[{name:'分组',unique_count:3,examples:['中心区','外围区']},{name:'道路名',unique_count:40},{name:'编号',unique_count:1001,distinct_truncated:true}],sheets:[{name:'数据',state:'visible',preview:[['标题'],['数值一','数值二'],[0,3.5]]}],sheet:'数据',header_row:2};
async function main(){
await Promise.resolve();await Promise.resolve();
assert.equal(t.state.source,'upload');assert.equal(t.state.info,null);assert.equal($('step-1').hidden,false);assert.equal($('convention').value,'');assert.equal($('next-1').disabled,true);assert.equal($('figure').hidden,true);
t.state.csv=info;t.state.uploadName='我的数据.xlsx';t.fillFields(info);assert.equal($('field-x').value,'');assert.equal($('field-y').value,'');assert.equal($('mapping-confirmed').checked,false);assert.equal($('suggest-x').dataset.value,'经度');assert($('field-y').children.some(o=>o.textContent.includes('示例：0')));assert($('field-group').children.some(o=>o.textContent.includes('40 种值')));
t.goStep(2,false);assert.equal($('step-1').hidden,true);assert.equal($('step-2').hidden,false);await t.chooseChart('half_violin');assert.equal(t.state.csv.id,'table-a');assert.equal(t.state.source,'upload');t.goStep(3,false);assert.equal($('x-label').hidden,true);assert.equal($('group-label').hidden,false);assert.equal($('scatter-options').hidden,true);assert.equal($('quadrant-options').hidden,true);$('record-unit').value='一个采样点';$('field-y').value='数值二';$('field-group').value='分组';$('mapping-confirmed').checked=true;assert.equal(t.validatePayload(t.payload()),null);assert.equal(t.payload().fields.x,'');assert.deepEqual(Array.from(t.payload().plots),['half_violin']);
$('field-group').value='道路名';t.updateVisibility();assert.equal($('group-pagination').hidden,false);assert.equal($('paginate-confirmation').hidden,false);assert.equal(t.validatePayload(t.payload()).target,'paginate-groups');$('paginate-groups').checked=true;assert.equal(t.payload().high_cardinality_action,'paginate');assert.equal(t.validatePayload(t.payload()),null);
$('field-group').value='编号';t.updateVisibility();assert.equal($('paginate-confirmation').hidden,true);assert.equal(t.validatePayload(t.payload()).target,'field-group');assert.equal(t.payload().high_cardinality_action,'');
await t.chooseChart('scatter');assert.equal(t.state.csv.id,'table-a');assert.equal($('x-label').hidden,false);$('field-x').value='数值一';$('field-y').value='数值二';$('field-group').value='';$('mapping-confirmed').checked=true;assert.equal(t.validatePayload(t.payload()),null);assert.equal(t.payload().ols,false);assert.equal(t.payload().correlation,false);
await t.chooseChart('quadrant');assert.equal($('group-label').hidden,true);assert.equal($('quadrant-options').hidden,false);$('mapping-confirmed').checked=true;$('threshold').value='fixed';assert.equal(t.validatePayload(t.payload()).target,'x-threshold');$('x-threshold').value=0;$('y-threshold').value=2;assert.equal(t.validatePayload(t.payload()),null);
await t.chooseChart('gis');assert.equal(t.state.csv.id,'table-a');assert.equal($('field-x').value,'');assert.equal($('field-y').value,'');assert.equal($('field-group').value,'');$('field-x').value='经度';$('field-y').value='纬度';$('mapping-confirmed').checked=true;assert.equal(t.validatePayload(t.payload()).target,'convention');$('convention').value='WGS84';assert.equal(t.payload().map_crs,'auto');assert.equal(t.validatePayload(t.payload()).target,'boundary-file');t.state.boundary={id:'bound'};$('boundary-convention').value='WGS84';$('crs-confirmed').checked=true;assert.equal(t.validatePayload(t.payload()),null);
$('sheet-select').add(new context.Option('数据','数据'));$('sheet-select').value='数据';$('header-row').value=2;t.tableSelectionChanged();assert.equal(t.state.info,null);assert.equal(t.state.tablePending,true);assert.equal($('mapping-confirmed').checked,false);respond=()=>({...info,id:'normalized-b'});await t.applyTable();assert.equal(t.state.csv.id,'normalized-b');assert.equal(t.payload().csv_id,'normalized-b');assert.equal(t.state.tablePending,false);assert.equal($('field-x').value,'');
t.renderComplete({id:'job',source:'upload',rows:40,files:[{name:'stats_scatter.png'},{name:'stats_scatter.pdf'}],grouping_note:'分为3组',group_count:3,projection_note:'',counts:{}});assert.equal($('downloads').hidden,false);assert(!$('grouping-result').textContent.includes('仅为第一页'));assert.equal($('download-svg').hidden,true);assert($('download-pdf').href.includes('/output/job/stats_scatter.pdf'));
t.renderComplete({id:'job2',source:'upload',rows:40,files:[],grouping_note:'分页',group_count:40,projection_note:'',counts:{}});assert($('grouping-result').textContent.includes('每页 PNG / SVG'));assert(!$('grouping-result').textContent.includes('仅为第一页'));
t.renderComplete({id:'pages',source:'upload',rows:40,files:['stats_scatter.png','stats_scatter.svg','stats_scatter.pdf','stats_scatter_p02.png','stats_scatter_p02.svg'].map(name=>({name})),grouping_note:'多子图分页',group_count:40,counts:{}});assert(t.prettyName('stats_scatter_p02').includes('第2页（多个子图）'));assert(t.prettyName('stats_scatter').includes('第1页'));t.selectFigure('stats_scatter_p02');assert($('download-png').href.includes('stats_scatter_p02.png'));assert($('download-svg').href.includes('stats_scatter_p02.svg'));assert($('download-pdf').href.includes('stats_scatter.pdf'));assert.equal($('download-pdf').hidden,false);assert.equal($('download-pdf').textContent,'完整 PDF');

const realInfo={format:'xlsx',sheet:'数据',header_row:1,rows:21,headers:['记录编号','分组','服务数量','步行时间_分钟'],sample:[{'记录编号':'001','分组':'虚构组甲','服务数量':'4','步行时间_分钟':'18.5'}],columns:[{name:'记录编号',type:'text',examples:['001','002'],unique_count:21,distinct_truncated:false},{name:'分组',type:'text',examples:['虚构组甲','虚构组乙'],unique_count:3,distinct_truncated:false}]};t.state.source='upload';t.state.mode='stats';t.state.chart='half_violin';t.state.csv={...realInfo,id:'real'};t.fillFields(t.state.csv);assert($('field-group').children.some(o=>o.textContent.includes('种值')));assert.equal($('field-y').value,'');assert.equal($('field-group').value,'');
respond=(path,opts)=>path.startsWith('/api/upload')?{id:'raw-xlsx',format:'xlsx',needs_selection:true,sheets:[{name:'数据',state:'visible',preview:[['说明'],['横值','纵值'],[1,2]]}],sheet:'数据',header_row:1,raw_preview:[['说明'],['横值','纵值'],[1,2]]}:{};
$('csv-file').files=[{name:'中文多表.xlsx',size:100,arrayBuffer:async()=>new Uint8Array([1]).buffer}];await t.upload('csv');assert.equal(t.state.csv.id,'raw-xlsx');assert.equal(t.state.info,null);assert.equal(t.state.tablePending,true);assert.equal($('raw-preview-box').hidden,false);assert.equal($('sheet-label').hidden,false);assert.equal($('next-1').disabled,true);assert(requests.at(-1)[0].includes('kind=xlsx'));assert.equal(requests.at(-1)[1].headers['X-POI-Token'],'__TOKEN__');
$('header-row').value=2;respond=()=>({...info,id:'xlsx-picked',header_row:2});await t.applyTable();assert.equal(t.state.csv.id,'xlsx-picked');assert.equal(t.state.tablePending,false);assert.equal($('next-1').disabled,false);assert.equal(t.state.info.headers[0],'经度');
respond=(path)=>path.startsWith('/api/demo')?{headers:['supply_example','walk_example','sample_group','observation_id'],sample:[{supply_example:1,walk_example:2,sample_group:'A'}],rows:3,filename:'合成示例.csv'}:{};
await t.switchSource('demo');assert.equal(t.state.source,'demo');assert.equal(t.state.csv.id,'xlsx-picked');assert.equal($('convention').value,'WGS84');await t.switchSource('upload');assert.equal(t.state.source,'upload');assert.equal(t.state.csv.id,'xlsx-picked');assert.equal($('convention').value,'');assert.equal($('mapping-confirmed').checked,false);assert.equal($('record-unit').value,'');assert.equal($('field-y').value,'');
$('header-row').value=3;t.tableSelectionChanged();await t.switchSource('demo');await t.switchSource('upload');assert.equal(t.state.info,null);assert.equal(t.state.tablePending,true);assert.equal($('next-1').disabled,true);

respond=()=>({id:'csv-needs-header',format:'csv',needs_selection:true,selection_error:'第 1 行不是有效表头，请选实际列名行',raw_preview:[['调查说明'],['分组','数值'],['中心区','3']],header_row:1});
$('csv-file').files=[{name:'中文表头.csv',size:100,arrayBuffer:async()=>new Uint8Array([1]).buffer}];await t.upload('csv');assert.equal(t.state.csv.id,'csv-needs-header');assert.equal(t.state.info,null);assert.equal(t.state.tablePending,true);assert.equal($('table-controls').hidden,false);assert.equal($('raw-preview-box').hidden,false);assert.equal($('sheet-label').hidden,true);assert.equal($('table-warnings').hidden,false);assert($('table-warnings').textContent.includes('表头'));assert.equal($('next-1').disabled,true);
$('header-row').value=2;t.tableSelectionChanged();respond=()=>({id:'csv-normalized',format:'csv',header_row:2,headers:['分组','数值'],rows:1,sample:[{'分组':'中心区','数值':'3'}],columns:[{name:'分组',unique_count:1}]});await t.applyTable();assert.equal(t.state.csv.id,'csv-normalized');assert.equal(t.state.tablePending,false);assert.equal($('next-1').disabled,false);assert.equal($('field-y').value,'');assert.equal($('table-warnings').hidden,true);
console.log('PASS: CSV invalid-first-header retains upload id/raw preview, then explicit header selection recovers and resets mappings');
$('csv-file').files=[{name:'oversized.xlsx',size:101*1024*1024}];await t.upload('csv');assert.equal(t.state.csv,null);assert.equal(t.state.info,null);assert.equal($('form-error').hidden,false);
$('csv-file').files=[{name:'old.xls',size:100}];await t.upload('csv');assert.equal(t.state.csv,null);assert($('form-error').textContent.includes('.xls'));
t.state.boundary={id:'old-boundary'};$('boundary-convention').value='WGS84';$('crs-confirmed').checked=true;$('boundary-file').files=[{name:'boundary.geojson',size:100,arrayBuffer:async()=>new Uint8Array([1]).buffer}];respond=()=>({id:'new-boundary',type:'Polygon'});await t.upload('boundary');assert.equal(t.state.boundary.id,'new-boundary');assert.equal($('boundary-convention').value,'');assert.equal($('crs-confirmed').checked,false);

t.state.job={id:'pending'};t.state.busy=true;respond=()=>({id:'pending',state:'running'});await t.pollJob();assert.equal(t.state.busy,true);assert.equal($('status').textContent,'正在生成图件与可复核结果');
respond=()=>{throw Error('temporary disconnect')};await t.pollJob();assert.equal(t.state.busy,true);assert.equal(t.state.pollFailures,1);assert($('status').textContent.includes('状态暂时未知'));
respond=()=>({id:'pending',state:'complete',source:'upload',rows:3,files:[{name:'stats_scatter.png'},{name:'stats_scatter.pdf'},{name:'stats_scatter.svg'}],counts:{},group_count:3,grouping_note:'按组分为3组'});await t.pollJob();assert.equal(t.state.busy,false);assert.equal(t.state.pollFailures,0);assert.equal($('cancel').hidden,true);assert.equal($('error').hidden,true);assert.equal($('output-actions').hidden,false);
t.state.job={id:'cancel-me'};t.state.busy=true;respond=(path)=>{assert.equal(path,'/api/cancel/cancel-me');return {ok:true}};await $('cancel').onclick();assert.equal($('cancel').disabled,true);assert.equal(requests.at(-1)[1].method,'POST');respond=()=>({id:'cancel-me',state:'cancelled',log:'Cancelled by user'});await t.pollJob();assert.equal(t.state.busy,false);assert($('status').textContent.includes('任务已取消'));assert.equal($('error').hidden,true);
for (const staleSrc of ['/preview/gis', '/output/old-job/atlas_01_spatial_p01.png']) {
  $('figure').setAttribute('src',staleSrc); $('figure').hidden=false; $('downloads').hidden=false; $('output-actions').hidden=false;
  for(const id of ['download-png','download-svg','download-pdf','report-link','bundle-link']) $(id).setAttribute('href','/output/old-job/stale');
  t.state.job={id:'failed-now'}; t.state.busy=true;
  respond=()=>({id:'failed-now',state:'failed',error:'所选坐标列含非有限数值，请重新核对',log:'RAW TRACEBACK MUST STAY IN DETAILS',files:[],counts:{invalid_coordinates:1}});
  await t.pollJob();
  assert.equal(t.state.busy,false); assert.equal($('figure').hidden,true); assert.equal($('figure').getAttribute('src'),null); assert.equal($('downloads').hidden,true); assert.equal($('output-actions').hidden,true);
  for(const id of ['download-png','download-svg','download-pdf','report-link','bundle-link']) assert.equal($(id).getAttribute('href'),null);
  assert.equal($('error').textContent,'所选坐标列含非有限数值，请重新核对'); assert.equal($('log').textContent,'RAW TRACEBACK MUST STAY IN DETAILS'); assert.equal($('evidence').hidden,false); assert.equal(t.state.files.length,0);
}
console.log('PASS: failed jobs clear old demo/generated images, remove stale download links, and keep raw logs out of the friendly error');
console.log('PASS: ongoing poll, transient connection retry, verified completion, cancel POST and terminal cancelled state');
console.log('PASS: synthetic XLSX template-shaped metadata, raw XLSX sheet/header selection, returned normalized id, POST token, demo/upload round trip, pending header selection preserved, oversize/XLS rejection, boundary coordinate reset');
console.log('PASS: initial own-data, step gates, no auto-mapping, zero samples, distinct counts, half-violin/scatter/quadrant relevance, required confirmations, >12 pagination/>120 block, source/table retention, unknown CRS, immutable table selection id, and result page switching, per-page PNG/SVG, full-PDF links and pagination notices');
}
main().catch(e=>{console.error(e);process.exitCode=1});
