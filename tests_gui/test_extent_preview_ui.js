/**
 * Dedicated point-preview / File-upload regressions. Run: node tests_gui/test_extent_preview_ui.js
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
const requests=[];let respond=()=>({dependencies:{},work:'test-work',max_bytes:75*1024*1024});
const context={document,window:{open(){}},Option:function(text,value){const el=new Element('option',{value});el.textContent=text;return el},fetch:async(path,opts)=>{requests.push([path,opts]);const body=await respond(path,opts);return {ok:true,json:async()=>body}},setTimeout:()=>1,URLSearchParams,console,assert};
vm.createContext(context);
const source=fs.readFileSync(path.join(root,'gui/static/app.js'),'utf8');
for (const [, id] of source.matchAll(/\$\('([^']+)'\)/g)) assert(ids.includes(id), 'Missing static DOM ID: ' + id);
assert(!(source + html).includes('EPSG:32650'), 'Must not hard-code a study-area projection');
vm.runInContext(source+'\nthis.test={state,$,payload,validatePayload,fillFields,chooseChart,goStep,updateVisibility,applyTable,tableSelectionChanged,updateSuggestions,needsGroupPagination,renderComplete,selectFigure,prettyName,pollJob,upload,switchSource};',context);
const t=context.test, $=t.$;
const info = {id:'preview-table', format:'csv', rows:2, headers:['lon','lat','other'], sample:[{lon:'117.2',lat:'31.8',other:'0'}], columns:[]};
function change(id) { for (const listener of $('config-form').events.change || []) listener({target:$(id)}); }
function restoreMapping() { $('field-x').value='lon'; $('field-y').value='lat'; $('mapping-confirmed').checked=true; $('record-unit').value='一个采样点'; }
async function main() {
  assert.equal(t.state.maxBytes,100*1024*1024,'Safe frontend default must match 100 MB');
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(t.state.maxBytes,75*1024*1024,'The server limit is authoritative');
  assert($('file-limit').textContent.includes('75 MB'));
  assert.equal($('boundary-mode').value,'file');
  t.state.csv=info; t.fillFields(info); await t.chooseChart('gis'); restoreMapping();
  $('convention').value='WGS84';
  assert.equal(t.payload().boundary_mode,'file');
  assert.equal(t.validatePayload(t.payload()).target,'boundary-file','File mode must never silently fall back to preview');
  assert.equal($('extent-preview-options').hidden,true);

  t.state.boundary={id:'province-only'};
  $('boundary-convention').value='unsupported';
  $('boundary-mode').value='extent_preview'; change('boundary-mode');
  assert.equal($('boundary-upload').hidden,true);
  assert.equal($('extent-preview-options').hidden,false);
  assert.equal(t.payload().boundary_id,undefined,'The previous province must not be used for the generated preview');
  assert.equal(t.payload().boundary_convention,'WGS84');
  assert.equal(t.payload().boundary_crs,'EPSG:4326');
  assert.equal(t.validatePayload(t.payload()).target,'extent-preview-confirmed');
  $('extent-preview-confirmed').checked=true;
  assert.equal(t.validatePayload(t.payload()).target,'crs-confirmed');
  $('crs-confirmed').checked=true;
  assert.equal(t.validatePayload(t.payload()),null);
  assert($('disclaimer').textContent.includes('不代表行政区'));
  assert($('crs-confirmation-wording').textContent.includes('WGS84'));

  for (const bad of ['', 'NaN', 'Infinity', '-1', '51']) {
    $('extent-padding-percent').value=bad;
    assert.equal(t.validatePayload(t.payload()).target,'extent-padding-percent');
  }
  for (const good of ['0','5','50']) {
    $('extent-padding-percent').value=good;
    assert.equal(t.validatePayload(t.payload()),null);
  }
  change('extent-padding-percent');
  assert.equal($('extent-preview-confirmed').checked,false,'Changing construction parameters resets informed confirmation');
  $('extent-preview-confirmed').checked=true;
  $('field-x').value='other'; change('field-x');
  assert.equal($('extent-preview-confirmed').checked,false,'Changing chosen columns resets preview confirmation');
  assert.equal($('mapping-confirmed').checked,false);
  restoreMapping(); $('extent-preview-confirmed').checked=true;

  $('convention').value='CRS_NATIVE'; $('crs').value='EPSG:4326';
  assert.equal(t.validatePayload(t.payload()).target,'convention','Numeric / EPSG declarations cannot substitute for confirmed WGS84');
  $('convention').value='unsupported';
  assert.equal(t.validatePayload(t.payload()).target,'convention');
  $('convention').value='WGS84'; change('convention');
  assert.equal($('crs-confirmed').checked,false);
  assert.equal($('extent-preview-confirmed').checked,false);
  $('extent-preview-confirmed').checked=true; $('crs-confirmed').checked=true;
  $('boundary-mode').value='file'; change('boundary-mode');
  assert.equal(t.payload().boundary_id,'province-only','Returning to file mode restores the actual selected file');
  assert.equal($('boundary-upload').hidden,false);
  assert.equal($('extent-preview-options').hidden,true);
  assert.equal($('extent-preview-confirmed').checked,false);
  assert.equal($('crs-confirmed').checked,false);
  $('boundary-convention').value='WGS84'; $('crs-confirmed').checked=true;
  assert.equal(t.validatePayload(t.payload()),null);

  $('boundary-mode').value='extent_preview'; t.state.source='demo'; t.updateVisibility();
  assert.equal(t.payload().boundary_mode,'file','Demo must retain its supplied synthetic boundary');
  assert.equal(t.payload().extent_preview_confirmed,false);
  assert.equal($('boundary-choice').hidden,true);
  assert.equal($('demo-boundary').hidden,false);
  t.state.source='upload'; t.state.mode='stats'; t.state.chart='scatter'; t.updateVisibility();
  assert.equal(t.payload().boundary_mode,'file','Statistical charts do not opt into geographic preview');
  t.state.mode='gis'; t.state.chart='gis';
  t.fillFields(info);
  assert.equal($('extent-preview-confirmed').checked,false,'Loading a new table resets preview confirmation');

  const largeFile={name:'large.csv',size:75*1024*1024,arrayBuffer:async()=>{throw Error('File must be streamed directly')}};
  $('csv-file').files=[largeFile]; respond=()=>({...info,id:'uploaded-large'});
  await t.upload('csv');
  assert.equal(t.state.csv.id,'uploaded-large');
  assert.strictEqual(requests.at(-1)[1].body,largeFile,'fetch must receive File rather than a materialized buffer');
  assert.equal(requests.at(-1)[1].headers['Content-Type'],'application/octet-stream');
  const before=requests.length;
  $('csv-file').files=[{name:'oversized.csv',size:75*1024*1024+1}];
  await t.upload('csv');
  assert.equal(requests.length,before,'Reject oversized files before reading or making a request');
  assert($('form-error').textContent.includes('75 MB'));
  t.state.maxBytes=100*1024*1024;
  $('csv-file').files=[{name:'under100.csv',size:90*1024*1024}];
  await t.upload('csv');
  assert.equal(t.state.csv.id,'uploaded-large');
  $('csv-file').files=[{name:'over100.csv',size:100*1024*1024+1}];
  await t.upload('csv');
  assert($('form-error').textContent.includes('100 MB'));
  console.log('PASS: preview is explicit, file mode remains default, province is unused, confirmations reset, only confirmed WGS84 allowed, padding validated, demo/stats isolated, server-controlled 100 MB default, direct File upload and oversize rejection');
}
main().catch(error=>{console.error(error);process.exitCode=1});
