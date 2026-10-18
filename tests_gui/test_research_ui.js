/** Offline regression checks using a small DOM double; browser QA is separate. */
'use strict';
const fs=require('fs'),vm=require('vm'),assert=require('assert'),path=require('path');
const root=path.resolve(__dirname,'..');
function boot(page='research',options={}){
const html=fs.readFileSync(path.join(root,'gui/static/'+(page==='research'?'research':'index')+'.html'),'utf8');
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
function matches(el,selector){if(selector.endsWith(':checked'))return el.checked&&matches(el,selector.slice(0,-8));if(selector.includes(' ')){const p=selector.split(' ');if(!matches(el,p.pop()))return false;let parent=el.parentElement;while(parent){if(matches(parent,p.join(' ')))return true;parent=parent.parentElement}return false}if(selector.startsWith('#'))return el.id===selector.slice(1);if(selector.startsWith('.'))return el._classes.has(selector.slice(1));if(selector.startsWith('['))return selector.slice(1,-1) in el.attrs || (selector.startsWith('[data-') && selector.slice(6,-1).replace(/-([a-z])/g,(_,c)=>c.toUpperCase()) in el.dataset);return el.tagName===selector.toUpperCase()}
function allElements(){const found=[];function visit(el){found.push(el);for(const c of el.children)visit(c)}for(const el of all.filter(e=>!e.parentElement))visit(el);return found}
const document={getElementById:id=>allElements().find(e=>e.id===id)||null,querySelectorAll:selector=>allElements().filter(e=>selector.split(',').some(s=>matches(e,s))),querySelector:selector=>selector.startsWith('meta')?all.find(e=>e.tagName==='META'&&e.attrs.name==='poi-token'):allElements().find(e=>matches(e,selector)),createElement:tag=>new Element(tag),createTextNode:text=>{const e=new Element('text');e.textContent=text;return e}};

const storage=new Map(options.transfer?[['poi-research-transfer',JSON.stringify(options.transfer)]]:[]),requests=[],navigation=[];
let respond=(url)=>url==='/api/info'?{dependencies:{},work:'test'}:[];
const context={document,window:{location:{search:options.search||'',assign:url=>navigation.push(url)},open(){}},sessionStorage:{getItem:k=>storage.get(k)||null,setItem:(k,v)=>storage.set(k,v),removeItem:k=>storage.delete(k)},Option:function(text,value){const e=new Element('option',{value});e.textContent=text;return e},fetch:async(url,opts)=>{requests.push([url,opts]);return{ok:true,json:async()=>["/api/projects","/api/history"].includes(url)?[]:respond(url,opts)}},setTimeout:()=>1,clearTimeout(){},URLSearchParams,console};
vm.createContext(context);
for(const file of page==='research'?['research.js']:['app.js','enhancements.js'])vm.runInContext(fs.readFileSync(path.join(root,'gui/static',file),'utf8'),context);
vm.runInContext(page==='research'?'this.test={state,$,upload,showTable,complete,payload};':'this.test={state,$,fillFields};',context);
return {...context.test,document,storage,navigation,requests,respond:fn=>{respond=fn}};
}
const info={id:'normalized-sheet-2',format:'xlsx',headers:['对象','估计值','下限','上限'],rows:2,sample:[{'对象':'A','估计值':'2','下限':'1','上限':'3'}],sheet:'第二工作表',header_row:3};
const job={id:'old-forest',files:[{name:'research_forest.png'},{name:'research_forest.svg'},{name:'research_forest.pdf'}],counts:{input_rows:2}};
const cases=[];
function test(name,fn){cases.push([name,fn]);}
test('switching chart clears previous result and links',()=>{
 const t=boot();t.showTable(info);t.complete(job);t.$('chart').value='dumbbell';t.$('chart').onchange();
 assert(t.$('figure').hidden,'previous forest preview must not appear under dumbbell');assert.equal(t.$('download-links').children.length,0);assert.equal(t.state.job,null);assert(t.$('status').textContent.includes('重新生成'));
});
test('editing title clears result immediately, before blur',()=>{
 const t=boot();t.showTable(info);t.complete(job);t.$('title').value='new title';t.$('research-form').oninput?.({target:t.$('title')});
 assert(t.$('figure').hidden);assert.equal(t.$('download-links').children.length,0);
});
test('changing fields clears result and confirmation',()=>{
 const t=boot();t.showTable(info);t.complete(job);t.$('mapping-confirmed').checked=true;t.$('field-mappings').onchange();
 assert.equal(t.$('mapping-confirmed').checked,false);assert(t.$('figure').hidden);assert.equal(t.state.job,null);
});
test('successful table read clears obsolete read warning and error',async()=>{
 const t=boot();t.state.source={id:'workbook',format:'xlsx'};t.$('sheet').onchange();t.$('form-error').hidden=false;t.respond(()=>info);
 await t.$('read-table').onclick();assert.equal(t.state.table.id,info.id);assert(!t.$('status').textContent.includes('重新读取'));assert(t.$('form-error').hidden);
});
test('rejected replacement file cannot keep old table or exports',async()=>{
 const t=boot();t.showTable(info);t.complete(job);let caught=false;try{await t.upload({name:'bad.xls',size:1});}catch{caught=true;}
 assert(caught);assert.equal(t.state.table,null);assert(t.$('figure').hidden);assert.equal(t.$('download-links').children.length,0);
});
test('four entry cards preserve normalized table id and metadata',()=>{
 for(const chart of ['heatmap','forest','dumbbell','multi_raincloud']){
  const t=boot('index');t.state.source='upload';t.state.csv=info;t.fillFields(info);t.$('record-unit').value='一条观测';
  const card=t.document.querySelectorAll('[data-research-chart]').find(e=>e.dataset.researchChart===chart);card.events.click[0]();
  assert.equal(t.navigation[0],'/research?chart='+chart);const saved=JSON.parse(t.storage.get('poi-research-transfer'));assert.equal(saved.id,info.id);assert.equal(saved.info.header_row,3);assert.equal(saved.info.sheet,'第二工作表');
  const target=boot('research',{search:'?chart='+chart,transfer:saved});assert.equal(target.state.table.id,info.id);assert.equal(target.$('chart').value,chart);assert.equal(target.$('record-unit').value,'一条观测');assert.equal(target.storage.size,0);
 }
});
test('pending table and busy job block navigation',()=>{
 for(const flag of ['tablePending','busy','loading']){const t=boot('index');t.state.source='upload';t.state.csv=info;t.fillFields(info);t.state[flag]=true;t.document.querySelectorAll('[data-research-chart]')[0].events.click[0]();assert.equal(t.navigation.length,0);}
});
test('expired session or different chart never restores the wrong data',()=>{
 for(const transfer of [{token:'old',chart:'forest',id:info.id,info},{token:'__TOKEN__',chart:'heatmap',id:info.id,info}]){const t=boot('research',{search:'?chart=forest',transfer});assert.equal(t.state.table,null);assert.equal(t.storage.size,0);}
});
(async()=>{let failures=0;for(const[name,fn]of cases){try{await fn();console.log('PASS: '+name)}catch(e){failures++;console.error('FAIL: '+name+' — '+e.message)}}console.log(`${cases.length-failures}/${cases.length} checks passed`);process.exitCode=failures?1:0;})();
