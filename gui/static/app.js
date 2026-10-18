'use strict';
const $ = id => document.getElementById(id);
const state = {
  step: 1, chart: null, mode: 'gis', source: 'upload', csv: null, boundary: null,
  info: null, uploadName: '', tablePending: false, loading: false,
  job: null, files: [], selected: null, poll: null, revision: 0,
  submittedRevision: 0, busy: false, uploadVersion: {csv: 0, boundary: 0},
  tableVersion: 0, demoVersion: 0, pollFailures: 0, maxBytes: 100 * 1024 * 1024
};
const token = document.querySelector('meta[name="poi-token"]').content;
const chartNames = {gis: '位置分布地图', scatter: '散点图 · 两个指标的关系', half_violin: '半小提琴 · 不同组的分布', quadrant: '四象限 · 高低四种情况'};
const fieldKeys = ['x', 'y', 'category', 'group', 'id', 'name', 'date'];
const palette = {
  gis: {premium: ['#BA7B45','#B85D53','#297D7F','#819476','#6D8EA7','#8E789D'], multimodal_paper: ['#496C8B','#C88450','#678F92','#8C789C','#A08A66','#B7756F'], mismatch_paper: ['#8D80B4','#83A78C','#C28C61','#BB6B62','#74A6A0','#D78D75']},
  stats: {premium: ['#2B777A','#C88743','#597BAD','#A95950','#817199','#78956D'], multimodal_paper: ['#496C8B','#C88450','#678F92','#8C789C','#738696','#BA923E'], mismatch_paper: ['#D78D75','#8EB6CC','#ACA5BD','#74A6A0','#C28C61','#8D80B4']}
};
async function api(path, body, raw = false) {
  const opts = body === undefined ? {} : {method: 'POST', headers: {'X-POI-Token': token, 'Content-Type': raw ? 'application/octet-stream' : 'application/json'}, body: raw ? body : JSON.stringify(body)};
  const response = await fetch(path, opts);
  let result;
  try { result = await response.json(); } catch { throw Error('本机服务没有返回有效结果。请确认启动窗口仍然打开'); }
  if (!response.ok) throw Error(result.error || '本机服务请求失败');
  return result;
}
function showError(error, local = false) {
  const el = $(local ? 'form-error' : 'error');
  el.textContent = error.message || String(error); el.hidden = false;
}
function clearError() {
  for (const id of ['error','form-error']) { $(id).hidden = true; $(id).textContent = ''; }
}
function dirty() {
  state.revision++;
  if (state.job && !state.busy) {
    $('status').textContent = '设置已更改；右侧仍是上次生成的图，需要重新生成';
    $('status').className = 'status pending';
  }
}
function setBusy(value) {
  state.busy = value;
  document.querySelectorAll('#config-form input,#config-form select,#config-form button,[data-step]').forEach(el => el.disabled = value);
  $('cancel').disabled = false; $('cancel').hidden = !value;
  $('generate').textContent = value ? '正在处理…' : '检查并生成图件';
  $('busy-overlay').hidden = !value;
  if (!value) updateVisibility();
}
function setLoading(value) {
  state.loading = value;
  for (const id of ['csv-file','sheet-select','header-row','apply-table','encoding','delimiter']) $(id).disabled = value || state.busy;
  document.querySelectorAll('[data-source],[data-demo]').forEach(el => el.disabled = value || state.busy);
  updateVisibility();
}
function resetMappingConfirmation() { $('mapping-confirmed').checked = false; $('extent-preview-confirmed').checked = false; updateMappingSummary(); }
function goStep(step, focus = true) {
  if (state.busy || state.loading) return;
  if (step > 1 && (!state.info || state.tablePending)) { showError(Error('先打开表格，核对工作表和表头，再继续'), true); return; }
  if (step > 2 && !state.chart) { showError(Error('先选择一种你想画的图'), true); return; }
  clearError(); state.step = step; updateVisibility();
  if (focus) { $('step-' + step + '-title').focus({preventScroll: true}); document.querySelector('.settings').scrollIntoView({block: 'start', behavior: 'auto'}); }
}
function updatePalette() {
  const el = $('swatches'); el.replaceChildren();
  for (const color of palette[state.mode][$('style').value]) { const i = document.createElement('i'); i.style.backgroundColor = color; el.append(i); }
}
function updateVisibility() {
  const gis = state.mode === 'gis', half = state.chart === 'half_violin', scatter = state.chart === 'scatter';
  const own = state.source === 'upload';
  const extentPreview = own && $('boundary-mode').value === 'extent_preview';
  document.querySelectorAll('.step-panel').forEach(el => el.hidden = el.id !== 'step-' + state.step);
  document.querySelectorAll('[data-step]').forEach(el => {
    const step = Number(el.dataset.step), active = step === state.step;
    el.classList.toggle('active', active); el.classList.toggle('done', step < state.step);
    if (active) el.setAttribute('aria-current', 'step'); else el.removeAttribute('aria-current');
    el.disabled = state.busy || state.loading || (step > 1 && (!state.info || state.tablePending)) || (step === 3 && !state.chart);
  });
  document.querySelectorAll('[data-source]').forEach(el => {
    const active = el.dataset.source === state.source; el.classList.toggle('active', active); el.setAttribute('aria-pressed', String(active));
  });
  document.querySelectorAll('[data-chart]').forEach(el => {
    const active = el.dataset.chart === state.chart; el.classList.toggle('active', active); el.setAttribute('aria-pressed', String(active));
  });
  document.querySelectorAll('[data-demo]').forEach(el => { const active = !own && el.dataset.demo === state.mode; el.classList.toggle('active', active); el.setAttribute('aria-pressed', String(active)); });
  document.querySelectorAll('.gis-only').forEach(el => el.hidden = !gis);
  document.querySelectorAll('.stats-only').forEach(el => el.hidden = gis);
  $('upload-source').hidden = !own; $('demo-source').hidden = own;
  $('table-controls').hidden = !(state.info || (own && state.csv));
  $('table-selection').hidden = !own; $('apply-table').hidden = !own;
  $('header-help').hidden = !own; $('table-state').hidden = !own;
  $('sheet-label').hidden = state.csv?.format !== 'xlsx';
  $('csv-settings').hidden = state.csv?.format === 'xlsx';
  $('next-1').disabled = state.busy || state.loading || !state.info || state.tablePending;
  $('next-2').disabled = state.busy || state.loading || !state.chart;
  $('table-toggle').disabled = !state.info || state.busy;
  $('x-label').hidden = !gis && half;
  $('group-label').hidden = gis || state.chart === 'quadrant';
  $('group-wording').textContent = half ? '按哪一列分组？（必选）' : '想按哪一列分颜色？（可不选）';
  $('group-hint').textContent = half ? '按少量类别分组，例如中心区 / 外围区；不要选每行都不同的编号或道路名。分组为空的行会从这张图排除，并写入排除台账' : '按少量类别分颜色，不选时所有点同色。分组为空的行会从这张分组图排除，并写入排除台账；不要选每行不同的编号或道路名';
  const native = $('convention').value === 'CRS_NATIVE';
  $('x-wording').textContent = gis ? (native ? '哪一列是横向位置 X？' : '哪一列是经度？') : '哪个指标放在横轴？';
  $('y-wording').textContent = gis ? (native ? '哪一列是纵向位置 Y？' : '哪一列是纬度？') : (half ? '想比较哪一列数值的分布？' : '哪个指标放在纵轴？');
  $('x-hint').textContent = gis ? (native ? '按数据说明选择 X 坐标列' : '例如 117.2。请结合数据说明确认') : '选择一个已有的数值指标，例如设施数量';
  $('y-hint').textContent = gis ? (native ? '按数据说明选择 Y 坐标列' : '例如 39.1。不要把经度和纬度选反') : (half ? '选择数值列，例如步行时间。这里不需要横轴指标' : '选择另一个数值指标，例如步行时间');
  $('mapping-confirmation').hidden = !own;
  $('boundary-choice').hidden = !own;
  $('boundary-upload').hidden = !own || extentPreview; $('demo-boundary').hidden = own;
  $('extent-preview-options').hidden = !extentPreview;
  $('crs-confirmation-wording').textContent = extentPreview ? '我已核实所选经纬度来自 WGS84（EPSG:4326）数据' : '我已核实表格与研究区轮廓的坐标系';
  $('input-crs-label').hidden = !native;
  $('boundary-crs-label').hidden = $('boundary-convention').value !== 'CRS_NATIVE';
  $('map-crs-label').hidden = $('projection-mode').value !== 'manual';
  $('kde-options').hidden = $('gis-plots').value !== 'kde';
  $('quadrant-options').hidden = gis || state.chart !== 'quadrant';
  $('fixed-thresholds').hidden = $('threshold').value !== 'fixed';
  $('scatter-options').hidden = !scatter; $('trend-summary').hidden = !scatter;
  $('x-title-label').hidden = half; $('x-unit-label').hidden = half;
  $('crs-hint').textContent = $('convention').value === 'unsupported' ? 'GCJ-02 / BD-09 不能直接制图。请先转换，并保留转换方法；不要把它勾选成 WGS84' : 'Excel 里有经纬度，不代表它就是 WGS84。请查数据说明或问提供者；软件不会自动识别';
  $('fields-intro').textContent = gis ? '选出位置所在的列。下拉框里的示例值，帮你和原表对上' : half ? '只需要一列数值和一列分组。请按每列真实含义来选' : '选出两个已有的数值指标。软件不会根据数字替你猜用途';
  $('chosen-chart-name').textContent = chartNames[state.chart] || '';
  $('selected-data').textContent = state.info ? `${own ? state.uploadName || '自己的表格' : '合成示例'} · ${Number(state.info.rows).toLocaleString()} 行 · ${state.info.headers.length} 列` : '';
  $('file-limit').textContent = `单个文件不超过 ${state.maxBytes / (1024 * 1024)} MB、200,000 行、200 列。旧版 .xls 请先另存为 .xlsx`;
  $('disclaimer').textContent = gis ? (extentPreview ? '点位范围预览只反映所选坐标的矩形范围，不代表行政区或正式研究区；不会从省界推断市界。自动投影仍需满足局部范围条件' : '地图需要明确的坐标系和真实研究区轮廓。自动投影只处理已声明 WGS84 的适用局部范围') : '统计图使用你已有的数值指标，不自动定义评价标准、显著性或因果关系';
  updatePalette(); updateMappingSummary(); updateGroupWarning();
}
function columnInfo(name) {
  const columns = state.info?.columns || [];
  return Array.isArray(columns) ? columns.find(column => column.name === name) : columns[name];
}
function uniqueLabel(column) {
  return column?.unique_count !== undefined ? `${column.distinct_truncated ? '≥' : ''}${Number(column.unique_count).toLocaleString()} 种值` : '';
}
function needsGroupPagination() {
  if (state.mode !== 'stats' || state.chart === 'quadrant' || !$('field-group').value) return false;
  const count = Number(columnInfo($('field-group').value)?.unique_count || 0);
  return count > 12 && count <= 120;
}
function updateGroupWarning() {
  const warning = $('group-warning'), name = $('field-group').value, column = columnInfo(name);
  const count = Number(column?.unique_count || 0), visible = state.mode === 'stats' && state.chart !== 'quadrant' && name && count > 12;
  $('group-pagination').hidden = !visible;
  $('paginate-confirmation').hidden = count > 120;
  $('paginate-help').textContent = count > 120 ? '最多支持 120 组。请改选分类 / 等级等少量类别；不会自动合并或丢弃类别' : '先拆成多个子图，仍多时自动分页。每页 PNG / SVG 可逐页预览，完整 PDF 包含全部页；不合并或丢弃类别';
  warning.textContent = visible ? `“${name}”共有${column.distinct_truncated ? '至少 ' : ''}${Number(column.unique_count).toLocaleString()} 组。道路名称 / 编号这样的列会拆成很多组；建议选类别 / 分类 / 等级${state.chart === 'scatter' ? '，散点图也可以不分组' : ''}。` : '';
}

function sampleValues(name) {
  const values = [];
  for (const value of columnInfo(name)?.examples || []) {
    if (value !== null && value !== undefined && String(value).trim() !== '') values.push(String(value).slice(0, 24));
    if (values.length === 2) return values;
  }
  for (const row of state.info?.sample || []) {
    const value = row[name];
    if (value === undefined || value === null || String(value).trim() === '') continue;
    const text = String(value).slice(0, 24);
    if (!values.includes(text)) values.push(text);
    if (values.length === 2) break;
  }
  return values;
}
function getSuggestion(key) {
  const names = state.mode === 'gis'
    ? {x: ['经度','longitude','lon','lng'], y: ['纬度','latitude','lat'], category: ['分类','类别','类型','店铺类型','设施类别','type','category']}
    : {x: ['x','x值','横轴'], y: ['y','y值','纵轴'], group: ['分组','组别','类别','类型','sample_group','group']};
  return (state.info?.headers || []).find(h => (names[key] || []).includes(h.trim().toLowerCase())) || '';
}
function updateSuggestions() {
  for (const key of ['x','y','category','group']) {
    const button = $('suggest-' + key), suggestion = getSuggestion(key);
    button.hidden = state.source !== 'upload' || !suggestion || $('field-' + key).value === suggestion;
    button.dataset.value = suggestion;
    button.textContent = suggestion ? `列名建议：${suggestion} · 点此选用，仍需核对` : '';
  }
}
function fillFields(info, preserve = false) {
  state.info = info; $('paginate-groups').checked = false;
  const headers = info?.headers || [];
  for (const el of document.querySelectorAll('.field-select')) {
    const previous = preserve ? el.value : '';
    el.replaceChildren(); el.add(new Option(el.id === 'field-group' && state.chart === 'half_violin' ? '选择分组列' : '请选择对应的列', ''));
    for (const name of headers) {
      const samples = sampleValues(name);
      const distinct = el.id === 'field-group' ? uniqueLabel(columnInfo(name)) : '';
      el.add(new Option(name + (distinct ? ` · ${distinct}` : '') + (samples.length ? `（示例：${samples.join('、')}）` : '（预览中为空）'), name));
    }
    if (headers.includes(previous)) el.value = previous;
  }
  if (info && state.source === 'demo') {
    const mapping = state.mode === 'gis' ? {x:'lon',y:'lat',category:'type',id:'poi_id',name:'name',date:'observed'} : {x:'supply_example',y:'walk_example',group:'sample_group',id:'observation_id'};
    if (state.chart === 'half_violin') delete mapping.x;
    if (state.chart === 'quadrant') delete mapping.group;
    for (const key of fieldKeys) $('field-' + key).value = headers.includes(mapping[key]) ? mapping[key] : '';
  }
  $('row-count').textContent = info ? `${Number(info.rows).toLocaleString()} 行 · ${headers.length} 列` : '';
  $('loaded-name').textContent = state.source === 'upload' ? state.uploadName || '已打开的表格' : info?.filename || '合成示例数据';
  const warnings = Array.isArray(info?.warnings) ? info.warnings : [];
  $('table-warnings').hidden = !warnings.length; $('table-warnings').textContent = warnings.join('\n');
  resetMappingConfirmation(); updateSuggestions(); renderTable(); updateVisibility();
}
function tableElement(headers, rows, raw = false) {
  const table = document.createElement('table'), thead = document.createElement('thead'), headRow = document.createElement('tr');
  for (const header of headers) { const th = document.createElement('th'); th.textContent = header; headRow.append(th); }
  thead.append(headRow); table.append(thead);
  const body = document.createElement('tbody');
  rows.forEach((row, i) => {
    const tr = document.createElement('tr');
    if (raw && i + 1 === Number($('header-row').value)) tr.className = 'header-row';
    row.forEach((value, j) => { const td = document.createElement('td'); td.textContent = value === null || value === undefined ? '' : String(value); if (raw && j === 0) td.className = 'row-number'; tr.append(td); });
    body.append(tr);
  });
  table.append(body); return table;
}
function renderTable() {
  for (const id of ['inline-table','sample-table']) {
    const wrap = $(id); wrap.replaceChildren();
    if (state.info) wrap.append(tableElement(state.info.headers, (state.info.sample || []).slice(0, 5).map(row => state.info.headers.map(h => row[h]))));
  }
  $('parsed-preview-box').hidden = !state.info;
  renderRawTable();
}
function renderRawTable() {
  const info = state.csv, selected = info?.sheets?.find(sheet => (typeof sheet === 'string' ? sheet : sheet.name) === $('sheet-select').value);
  const preview = selected?.preview || selected?.raw_preview || info?.raw_preview || info?.raw_sample || [];
  const rows = Array.isArray(preview) ? preview : preview?.rows || [];
  const arrays = rows.map(row => Array.isArray(row) ? row : (Array.isArray(row?.values) ? row.values : []));
  const wrap = $('raw-table'); wrap.replaceChildren();
  $('raw-preview-box').hidden = state.source !== 'upload' || !arrays.length;
  if (!arrays.length) return;
  const length = Math.max(...arrays.map(row => row.length));
  wrap.append(tableElement(['行号', ...Array.from({length}, (_, i) => `第 ${i + 1} 列`)], arrays.map((row, i) => [i + 1, ...Array.from({length}, (_, j) => row[j] ?? '')]), true));
}
function updateMappingSummary() {
  const mappings = [];
  const keys = state.mode === 'gis' ? ['x','y','category'] : state.chart === 'half_violin' ? ['y','group'] : state.chart === 'quadrant' ? ['x','y'] : ['x','y','group'];
  const names = state.mode === 'gis' ? {x:'位置 X / 经度',y:'位置 Y / 纬度',category:'分类'} : {x:'横轴',y:state.chart === 'half_violin' ? '分布数值' : '纵轴',group:'分组'};
  for (const key of keys) if ($('field-' + key).value) mappings.push(`${names[key]} ← ${$('field-' + key).value}`);
  $('mapping-summary').textContent = mappings.length ? '你选择的是：\n' + mappings.join('； ') : '选好上面的列后，再核对一次';
}
function clearResults() {
  state.job = null; state.files = []; state.selected = null;
  $('figure').hidden = true; $('figure').removeAttribute('src'); $('empty-preview').hidden = false; $('zoom').hidden = true;
  $('status').textContent = '从左边打开表格开始，三步生成第一张图'; $('status').className = 'status';
  $('caption').textContent = '还没有生成图件'; $('figure-tabs').replaceChildren();
  $('downloads').hidden = true; $('export-label').hidden = false; $('output-actions').hidden = true;
  $('evidence').hidden = true; $('grouping-result').hidden = true; $('counts').textContent = ''; $('file-list').replaceChildren(); $('log').textContent = ''; clearError();
}
function hideFailedResult() {
  state.files = []; state.selected = null;
  $('figure').hidden = true; $('figure').removeAttribute('src'); $('empty-preview').hidden = false; $('zoom').hidden = true;
  $('figure-tabs').replaceChildren(); $('downloads').hidden = true; $('output-actions').hidden = true;
  $('caption').textContent = '本次未生成图件'; $('file-list').replaceChildren();
  for (const id of ['download-png','download-svg','download-pdf','report-link','bundle-link']) $(id).removeAttribute('href');
}
async function loadDemo() {
  const version = ++state.demoVersion, mode = state.mode;
  setLoading(true);
  try {
    const info = await api('/api/demo?mode=' + mode);
    if (version !== state.demoVersion || state.source !== 'demo' || state.mode !== mode) return;
    $('demo-name').textContent = info.filename || '合成示例'; fillFields(info);
    $('record-unit').value = mode === 'gis' ? '一个虚构 POI 地点' : '一条虚构样本观测';
    $('convention').value = 'WGS84'; $('boundary-convention').value = 'WGS84';
    $('projection-mode').value = 'auto'; state.tablePending = false;
    if (!state.job) {
      $('figure').src = '/preview/' + mode; $('figure').hidden = false; $('empty-preview').hidden = true; $('zoom').hidden = false;
      $('figure').alt = '随包合成示例，尚未按当前设置生成'; $('status').textContent = '右侧是随包合成示例，尚未按当前设置生成';
      $('caption').textContent = '虚构数据 · 示例可能包含多张图，生成时只使用当前选择';
    }
  } catch (error) { showError(error, true); } finally { if (version === state.demoVersion) setLoading(false); }
}
async function switchSource(source) {
  if (state.busy || state.loading || state.source === source) return;
  state.source = source; clearError(); state.tablePending = false; resetMappingConfirmation(); $('crs-confirmed').checked = false;
  if (source === 'upload') {
    state.demoVersion++; $('record-unit').value = ''; $('convention').value = ''; $('boundary-convention').value = '';
    $('crs').value = ''; $('boundary-crs').value = ''; $('projection-mode').value = 'auto'; $('map-crs').value = '';
    state.tablePending = Boolean(state.csv && (state.csv.needs_selection || !state.csv.headers));
    fillFields(state.csv?.headers && !state.tablePending ? state.csv : null);
    if (!state.job) clearResults();
  } else { if (!state.chart) state.chart = 'gis'; state.mode = state.chart === 'gis' ? 'gis' : 'stats'; await loadDemo(); }
  dirty(); updateVisibility();
}
async function chooseChart(chart) {
  if (state.busy || state.loading || state.chart === chart) return;
  const oldMode = state.mode;
  state.chart = chart; state.mode = chart === 'gis' ? 'gis' : 'stats';
  if (oldMode !== state.mode) {
    for (const key of ['x','y','category','group','name','date']) $('field-' + key).value = '';
    $('x-title').value = ''; $('y-title').value = ''; $('x-unit').value = ''; $('y-unit').value = '';
  } else if (chart === 'half_violin') { $('field-x').value = ''; $('x-title').value = ''; $('x-unit').value = ''; }
  if (chart === 'quadrant') $('field-group').value = '';
  $('ols').checked = false; $('correlation').checked = false; $('paginate-groups').checked = false;
  resetMappingConfirmation(); updateSuggestions(); updateVisibility(); dirty();
  if (state.source === 'demo') await loadDemo();
}
async function chooseDemo(mode) {
  if (state.busy || state.loading) return;
  state.chart = mode === 'gis' ? 'gis' : 'scatter'; state.mode = mode; state.source = 'demo';
  clearError(); await loadDemo(); dirty(); updateVisibility();
}
function configureTableControls(info) {
  $('sheet-select').replaceChildren();
  for (const sheet of info.sheets || []) {
    const name = typeof sheet === 'string' ? sheet : sheet.name;
    $('sheet-select').add(new Option(name + (sheet.state && sheet.state !== 'visible' ? '（隐藏工作表）' : ''), name));
  }
  if (info.sheet) $('sheet-select').value = info.sheet;
  $('header-row').value = info.header_row || 1;
  $('header-row').max = '100';
}
async function upload(kind) {
  clearError();
  const input = $(kind === 'csv' ? 'csv-file' : 'boundary-file'), file = input.files[0];
  if (!file) return;
  if (file.size > state.maxBytes || (kind === 'csv' && !/\.(xlsx|csv|tsv|txt)$/i.test(file.name))) {
    if (kind === 'csv') { state.csv = null; state.uploadName = file.name; state.tablePending = true; fillFields(null); }
    else { state.boundary = null; $('boundary-state').textContent = '研究区轮廓没有读取成功'; $('crs-confirmed').checked = false; }
    showError(Error(file.size > state.maxBytes ? `文件超过 ${state.maxBytes / (1024 * 1024)} MB，请缩小文件后再试` : '请选择 .xlsx 或 CSV 文件。旧版 .xls 请先另存为 .xlsx'), true); return;
  }
  const version = ++state.uploadVersion[kind], source = state.source;
  const current = () => version === state.uploadVersion[kind] && source === state.source;
  if (kind === 'csv') {
    state.csv = null; state.uploadName = file.name; state.tablePending = true; state.tableVersion++;
    $('record-unit').value = ''; $('convention').value = ''; $('crs').value = ''; $('projection-mode').value = 'auto'; $('map-crs').value = ''; $('crs-confirmed').checked = false;
    fillFields(null); $('table-state').textContent = '正在读取文件…';
    if (/\.tsv$/i.test(file.name)) $('delimiter').value = 'tab';
    setLoading(true);
  } else { state.boundary = null; $('boundary-state').textContent = '正在读取研究区轮廓…'; $('boundary-convention').value = ''; $('boundary-crs').value = ''; $('crs-confirmed').checked = false; input.disabled = true; }
  const format = kind === 'csv' && /\.xlsx$/i.test(file.name) ? 'xlsx' : kind;
  const query = new URLSearchParams({kind: format, encoding: $('encoding').value, delimiter: $('delimiter').value === 'tab' ? '\t' : $('delimiter').value, header_row: '1'});
  try {
    const info = await api('/api/upload?' + query, file, true);
    if (!current()) return;
    if (kind === 'csv') {
      state.csv = {...info, format: info.format || format}; configureTableControls(state.csv);
      state.tablePending = !info.headers; fillFields(info.headers ? state.csv : null);
      $('table-state').textContent = info.headers ? '已读取，请核对下方预览' : '选择工作表和表头行，再点击读取';
      if (info.selection_error) { $('table-warnings').textContent = info.selection_error; $('table-warnings').hidden = false; }
    } else { state.boundary = info; $('boundary-state').textContent = '已读取 ' + file.name + ' · 生成前会检查是否是有效面'; }
    dirty();
  } catch (error) {
    if (current()) { showError(error, true); if (kind === 'boundary') $('boundary-state').textContent = '研究区轮廓没能读取，请重新选择'; else $('table-state').textContent = '文件没有读取成功'; }
  } finally { if (current()) { if (kind === 'csv') setLoading(false); else input.disabled = state.busy; } }
}
function tableSelectionChanged() {
  if (!state.csv || state.source !== 'upload') return;
  state.tablePending = true; state.csv.needs_selection = true; state.tableVersion++; fillFields(null);
  $('table-state').textContent = '选择已变更，点击“读取”更新预览';
  renderRawTable(); dirty(); updateVisibility();
}
async function applyTable() {
  if (state.busy || state.loading || !state.csv) return;
  clearError();
  const header = Number($('header-row').value);
  if (!Number.isInteger(header) || header < 1 || header > 100) { showError(Error('表头行号请填写 1 到 100 之间的整数'), true); return; }
  const id = state.csv.id, version = ++state.tableVersion;
  state.tablePending = true; fillFields(null); setLoading(true); $('table-state').textContent = '正在按所选表头读取…';
  try {
    const info = await api('/api/table', {id, sheet: $('sheet-select').value || undefined, header_row: header});
    if (version !== state.tableVersion || state.csv?.id !== id || state.source !== 'upload') return;
    state.csv = {...state.csv, ...info, id: info.id || id, needs_selection: false}; state.tablePending = false;
    fillFields(state.csv); $('table-state').textContent = '已更新，请核对下方预览'; dirty();
  } catch (error) { if (version === state.tableVersion) { showError(error, true); $('table-state').textContent = '请检查所选工作表和表头行'; } }
  finally { if (version === state.tableVersion) setLoading(false); }
}
function payload() {
  const boundaryMode = state.mode === 'gis' && state.source === 'upload' ? $('boundary-mode').value : 'file';
  const extentPreview = boundaryMode === 'extent_preview';
  const fields = {};
  for (const key of fieldKeys) fields[key] = $('field-' + key).value;
  if (state.mode === 'stats') { for (const key of ['category','name','date']) fields[key] = ''; if (state.chart === 'half_violin') fields.x = ''; if (state.chart === 'quadrant') fields.group = ''; }
  else fields.group = '';
  return {
    mode: state.mode, source: state.source, csv_id: state.csv?.id, boundary_id: extentPreview ? undefined : state.boundary?.id,
    boundary_mode: boundaryMode, extent_preview_confirmed: extentPreview && $('extent-preview-confirmed').checked,
    extent_padding_percent: $('extent-padding-percent').value,
    encoding: $('encoding').value, delimiter: $('delimiter').value === 'tab' ? '\t' : $('delimiter').value,
    fields, high_cardinality_action: needsGroupPagination() && $('paginate-groups').checked ? 'paginate' : '', mapping_confirmed: $('mapping-confirmed').checked, style: $('style').value, title: $('title').value,
    crs: $('convention').value === 'WGS84' ? 'EPSG:4326' : $('crs').value, convention: $('convention').value,
    boundary_crs: extentPreview || $('boundary-convention').value === 'WGS84' ? 'EPSG:4326' : $('boundary-crs').value,
    boundary_convention: extentPreview ? 'WGS84' : $('boundary-convention').value,
    map_crs: $('projection-mode').value === 'auto' ? 'auto' : $('map-crs').value,
    crs_confirmed: $('crs-confirmed').checked, date_format: $('date-format').value,
    kde: $('gis-plots').value === 'kde', bandwidth: $('bandwidth').value, cell: $('cell').value,
    record_unit: $('record-unit').value.trim(), transform: $('transform').value,
    x_label: $('x-title').value, y_label: $('y-title').value, x_unit: $('x-unit').value, y_unit: $('y-unit').value,
    plots: state.mode === 'stats' ? [state.chart] : [],
    ols: state.chart === 'scatter' && $('ols').checked, correlation: state.chart === 'scatter' && $('correlation').checked,
    threshold: $('threshold').value, x_threshold: $('x-threshold').value, y_threshold: $('y-threshold').value
  };
}
function validatePayload(req) {
  if (!state.info || state.tablePending || (state.source === 'upload' && !state.csv)) return {step: 1, text: '先打开表格，核对工作表和表头，再继续'};
  if (!state.chart) return {step: 2, text: '请选择一种你想画的图'};
  if (!req.record_unit) return {step: 3, target: 'record-unit', text: '先告诉我每一行代表什么，例如“一家店铺”或“一个采样点”'};
  const needed = req.mode === 'gis' || state.chart !== 'half_violin' ? ['x','y'] : ['y','group'];
  const wording = req.mode === 'gis' ? {x:'经度 / X 坐标', y:'纬度 / Y 坐标'} : {x:'横轴指标', y:state.chart === 'half_violin' ? '分布数值' : '纵轴指标', group:'分组'};
  for (const key of needed) if (!req.fields[key]) return {step:3,target:'field-' + key,text:`请选择${wording[key]}对应的列`};
  if (req.fields.x && req.fields.x === req.fields.y) return {step:3,target:'field-y',text:'横向和纵向请选不同的列'};
  if (state.chart === 'half_violin' && req.fields.group === req.fields.y) return {step:3,target:'field-group',text:'分组列和数值列请选不同的列'};
  if (req.mode === 'stats' && state.chart !== 'quadrant' && Number(columnInfo(req.fields.group)?.unique_count || 0) > 120) return {step:3,target:'field-group',text:'这列超过 120 组，不能直接作为分组。请改选分类 / 等级等少量类别；散点图也可以不选分组'};
  if (needsGroupPagination() && !req.high_cardinality_action) return {step:3,target:'paginate-groups',text:'分组超过 12 组。请改选类别列，或勾选“保留全部组，拆成多个子图”；组数仍多时会分页，散点图也可以不选分组'};
  if (req.source === 'upload' && !req.mapping_confirmed) return {step:3,target:'mapping-confirmed',text:'请先对照示例值，勾选确认这些列的含义选对了'};
  if (req.mode === 'gis') {
    if (!['file','extent_preview'].includes(req.boundary_mode)) return {step:3,target:'boundary-mode',text:'请选择使用真实轮廓文件，或明确选择点位范围预览'};
    const extentPreview = req.source === 'upload' && req.boundary_mode === 'extent_preview';
    if (!req.convention) return {step:3,target:'convention',text:'请先确认表格坐标系。软件不会根据经纬度数字猜测它来自哪里'};
    if (req.convention === 'unsupported' || (req.source === 'upload' && req.boundary_convention === 'unsupported')) return {step:3,target:'convention',text:'GCJ-02 / BD-09 不能直接使用。请先转换并记录转换方法，不要把它声明成 WGS84'};
    if (req.convention === 'CRS_NATIVE' && !req.crs.trim()) return {step:3,target:'crs',text:'请填写数据说明中标明的 EPSG 编号'};
    if (req.source === 'upload') {
      if (extentPreview) {
        if (req.convention !== 'WGS84' || req.crs !== 'EPSG:4326') return {step:3,target:'convention',text:'点位范围预览只支持已确认的 WGS84（EPSG:4326）经纬度，不能根据数字猜测或转换坐标系'};
        if (!req.extent_preview_confirmed) return {step:3,target:'extent-preview-confirmed',text:'请明确确认只做点位范围预览；矩形不能代表行政区或正式研究区边界'};
        const padding = Number(req.extent_padding_percent);
        if (String(req.extent_padding_percent).trim() === '' || !Number.isFinite(padding) || padding < 0 || padding > 50) return {step:3,target:'extent-padding-percent',text:'矩形每侧留白比例请填写 0 到 50 之间的数值（%）；每侧至少保留 0.001°'};
      } else {
        if (!req.boundary_id) return {step:3,target:'boundary-file',text:'还需要研究区轮廓：请选择真实的 GeoJSON 面文件；暂时没有时可明确选择只做点位范围预览'};
        if (!req.boundary_convention) return {step:3,target:'boundary-convention',text:'请确认研究区轮廓文件的坐标系'};
        if (req.boundary_convention === 'CRS_NATIVE' && !req.boundary_crs.trim()) return {step:3,target:'boundary-crs',text:'请填写研究区轮廓的 EPSG 编号'};
      }
      if (!req.crs_confirmed) return {step:3,target:'crs-confirmed',text:extentPreview ? '请核实并勾选所选经纬度来自 WGS84（EPSG:4326）数据' : '请核对并勾选表格与研究区轮廓的坐标系声明'};
    }
    if (req.map_crs === 'auto' && (req.convention !== 'WGS84' || req.boundary_convention !== 'WGS84')) return {step:3,target:'projection-mode',text:'自动投影仅适用于已声明 WGS84 的表格和轮廓。请在“地图进阶选项”中填写适用的米制投影'};
    if (!req.map_crs.trim()) return {step:3,target:'map-crs',text:'请填写适用于研究区的米制投影，或选择自动投影'};
    if (req.kde && (![req.bandwidth,req.cell].every(x => x !== '' && Number.isFinite(Number(x)) && Number(x) > 0))) return {step:3,target:'bandwidth',text:'核密度的带宽和网格边长都要大于 0'};
  }
  if (state.chart === 'quadrant' && req.threshold === 'fixed') {
    for (const axis of ['x','y']) if (req[axis + '_threshold'] === '' || !Number.isFinite(Number(req[axis + '_threshold']))) return {step:3,target:axis + '-threshold',text:'请填写横轴和纵轴的高低分界值'};
  }
  return null;
}
function prettyName(name) {
  const statsPage = name.match(/^(stats_.+)_p(\d+)$/);
  const base = statsPage ? statsPage[1] : name;
  let label = base.replace(/^atlas_01_spatial_p(\d+)$/, '空间图谱 · 第 $1 页').replace(/^atlas_02_audit_p(\d+)$/, '审计图谱 · 第 $1 页').replace(/^atlas_03_density_p(\d+)$/, '核密度 · 第 $1 页').replace('category_map', '分类点图').replace('audit_overview', '数据检查总览').replace('stats_scatter', '散点关系').replace('stats_raincloud', '半小提琴').replace('stats_quadrant', '四象限');
  if (statsPage) label += ` · 第${Number(statsPage[2])}页（多个子图）`;
  else if (base.startsWith('stats_') && state.files.some(file => file.name.startsWith(base + '_p') && file.name.endsWith('.png'))) label += ' · 第1页（多个子图）';
  return label;
}

function outputUrl(name) { return '/output/' + encodeURIComponent(state.job.id) + '/' + encodeURIComponent(name); }
function selectFigure(stem) {
  state.selected = stem; $('figure').src = outputUrl(stem + '.png'); $('figure').alt = prettyName(stem) + ' · 本次实际生成图件';
  $('figure').hidden = false; $('empty-preview').hidden = true; $('zoom').hidden = false;
  document.querySelectorAll('#figure-tabs button').forEach(el => el.classList.toggle('active', el.dataset.stem === stem));
  for (const ext of ['png','svg','pdf']) {
    const a = $('download-' + ext);
    const fileStem = ext === 'pdf' && stem.startsWith('stats_') ? stem.replace(/_p\d+$/, '') : stem;
    const filename = fileStem + '.' + ext;
    a.href = outputUrl(filename) + '?download=1'; a.setAttribute('download', filename);
    a.textContent = ext === 'pdf' && stem.startsWith('stats_') ? '完整 PDF' : ext.toUpperCase();
    a.hidden = !state.files.some(file => file.name === filename);
  }
}
function renderComplete(job) {
  state.job = job; state.files = job.files || [];
  const stems = state.files.filter(f => f.name.endsWith('.png')).map(f => f.name.slice(0,-4));
  const tabs = $('figure-tabs'); tabs.replaceChildren();
  for (const stem of stems) { const b = document.createElement('button'); b.type = 'button'; b.dataset.stem = stem; b.textContent = prettyName(stem); b.onclick = () => selectFigure(stem); tabs.append(b); }
  if (stems.length) selectFigure(stems[0]);
  $('downloads').hidden = !stems.length; $('export-label').hidden = Boolean(stems.length); $('output-actions').hidden = false;
  $('report-link').href = outputUrl('report.html'); $('bundle-link').href = outputUrl('result.zip'); $('bundle-link').setAttribute('download', 'poi-' + job.id + '.zip');
  $('caption').textContent = (job.source === 'demo' ? '合成示例 · 不是研究结论' : '自己的数据 · 请先检查报告与排除台账') + ' · ' + Number(job.rows).toLocaleString() + ' 行输入' + (job.record_unit ? ' · 每行：' + job.record_unit : '');
  $('grouping-result').hidden = !job.grouping_note;
  $('grouping-result').textContent = job.grouping_note ? String(job.grouping_note) + (Number(job.group_count) > 12 ? '\n已拆成多个子图；每页 PNG / SVG 可逐页预览，完整 PDF 包含全部页' : '') : '';
  const notes = [job.boundary_note, job.projection_note, job.grouping_note, ...(Array.isArray(job.import_warnings) ? job.import_warnings : []), ...(Array.isArray(job.display_warnings) ? job.display_warnings : [])].filter(Boolean);
  $('status').textContent = '已实际生成 · ' + stems.length + ' 张图 · ' + state.files.length + ' 个输出文件' + (notes.length ? '\n' + notes.join('\n') : '');
  $('status').className = 'status success'; $('evidence').hidden = false;
  $('counts').textContent = '逐项计数：' + JSON.stringify(job.counts) + (job.map_crs ? '\n制图投影：' + job.map_crs : '');
  const list = $('file-list'); list.replaceChildren();
  for (const file of state.files) { const a = document.createElement('a'); a.textContent = file.name; a.href = outputUrl(file.name) + '?download=1'; a.download = file.name; list.append(a); }
  $('log').textContent = (notes.length ? notes.join('\n') + '\n\n' : '') + (job.log || '');
}
async function pollJob() {
  if (!state.job) return;
  const id = state.job.id;
  try {
    const job = await api('/api/jobs/' + id);
    if (state.job?.id !== id) return;
    state.job = job;
    if (state.pollFailures) { clearError(); state.pollFailures = 0; }
    const labels = {queued:'等待本机引擎',validating:'正在核查数据、列选择与参数',running:'正在生成图件与可复核结果'};
    $('busy-text').textContent = labels[job.state] || '整理结果'; $('status').textContent = labels[job.state] || '';
    if (['complete','failed','cancelled'].includes(job.state)) {
      setBusy(false); state.poll = null;
      if (job.state === 'complete') renderComplete(job);
      else {
        hideFailedResult();
        $('status').textContent = job.state === 'cancelled' ? '任务已取消，可以调整设置再来一次' : '这次没有生成有效结果，请根据提示修改';
        $('status').className = 'status pending';
        if (job.state === 'failed') showError(Error(job.error || '任务失败，请展开运行详情查看原因'));
        $('counts').textContent = job.counts ? '本次审计计数：' + JSON.stringify(job.counts) : '';
        const notes = Array.isArray(job.display_warnings) ? job.display_warnings : [];
        $('grouping-result').hidden = !notes.length; $('grouping-result').textContent = notes.join('\n');
        $('evidence').hidden = false; $('log').textContent = job.log || '';
      }
      return;
    }
    state.poll = setTimeout(pollJob, 850);
  } catch (error) {
    state.pollFailures++; showError(Error('连接暂时中断，正在自动重新查询。请确认本机启动窗口仍打开。\n' + error.message));
    $('status').textContent = '任务状态暂时未知，仍在重新查询；不会把它标记为成功'; state.poll = setTimeout(pollJob, 3000);
  }
}
async function generate(event) {
  event.preventDefault();
  if (state.busy || state.loading) return;
  clearError(); const req = payload(), invalid = validatePayload(req);
  if (invalid) {
    goStep(invalid.step, false); showError(Error(invalid.text), true);
    if (invalid.target) { const target = $(invalid.target); for (let el = target.parentElement; el; el = el.parentElement) if (el.tagName === 'DETAILS') el.open = true; target.focus(); }
    return;
  }
  clearResults(); state.submittedRevision = state.revision; setBusy(true);
  $('status').textContent = '已提交，准备检查数据…';
  try { state.job = await api('/api/jobs', req); state.pollFailures = 0; pollJob(); }
  catch (error) { setBusy(false); showError(error, true); $('status').textContent = '请根据左侧提示修改，再重新生成'; }
}
for (const el of document.querySelectorAll('[data-step]')) el.onclick = () => goStep(Number(el.dataset.step));
for (const el of document.querySelectorAll('[data-back]')) el.onclick = () => goStep(Number(el.dataset.back));
for (const el of document.querySelectorAll('[data-source]')) el.onclick = () => switchSource(el.dataset.source);
for (const el of document.querySelectorAll('[data-chart]')) el.onclick = () => chooseChart(el.dataset.chart);
for (const el of document.querySelectorAll('[data-demo]')) el.onclick = () => chooseDemo(el.dataset.demo);
for (const el of document.querySelectorAll('[data-suggest]')) el.onclick = () => { $('field-' + el.dataset.suggest).value = el.dataset.value; if (el.dataset.suggest === 'group') $('paginate-groups').checked = false; resetMappingConfirmation(); updateVisibility(); updateSuggestions(); dirty(); };
$('next-1').onclick = () => goStep(2); $('next-2').onclick = () => goStep(3);
$('csv-file').onchange = () => upload('csv'); $('boundary-file').onchange = () => upload('boundary');
$('sheet-select').onchange = tableSelectionChanged; $('header-row').oninput = tableSelectionChanged; $('apply-table').onclick = applyTable;
for (const id of ['encoding','delimiter']) $(id).onchange = () => { if ($('csv-file').files.length && !/\.xlsx$/i.test($('csv-file').files[0].name)) upload('csv'); };
$('config-form').addEventListener('change', event => {
  if (event.target.classList?.contains('field-select')) { resetMappingConfirmation(); updateSuggestions(); }
  if (event.target.id === 'field-group') $('paginate-groups').checked = false;
  if (['convention','boundary-convention','crs','boundary-crs','boundary-mode'].includes(event.target.id)) $('crs-confirmed').checked = false;
  if (['convention','crs','boundary-mode','extent-padding-percent'].includes(event.target.id)) $('extent-preview-confirmed').checked = false;
  if (event.target.id === 'convention') updateSuggestions();
  updateVisibility(); dirty();
});
$('config-form').addEventListener('input', dirty); $('config-form').onsubmit = generate;
$('cancel').onclick = async () => { if (state.job) { $('cancel').disabled = true; try { await api('/api/cancel/' + state.job.id, {}); } catch (error) { showError(error); $('cancel').disabled = false; } } };
$('help-open').onclick = () => $('help-dialog').showModal();
$('table-toggle').onclick = () => $('table-dialog').showModal();
for (const el of document.querySelectorAll('[data-close]')) el.onclick = () => $(el.dataset.close).close();
for (const dialog of document.querySelectorAll('dialog')) dialog.addEventListener('click', event => { if (event.target === dialog) { const r = dialog.getBoundingClientRect(); if (event.clientX < r.left || event.clientX > r.right || event.clientY < r.top || event.clientY > r.bottom) dialog.close(); } });
$('zoom').onclick = () => { if (!$('figure').hidden && $('figure').getAttribute('src')) window.open($('figure').src, '_blank', 'noopener'); };
(async () => {
  fillFields(null); updateVisibility();
  try {
    const info = await api('/api/info');
    if (Number.isSafeInteger(info.max_bytes) && info.max_bytes > 0) state.maxBytes = info.max_bytes;
    updateVisibility();
    if (info.active_job) {
      state.job = info.active_job; state.mode = info.active_job.mode; state.source = info.active_job.source;
      state.chart = state.mode === 'gis' ? 'gis' : null;
      $('status').textContent = '检测到本机仍在运行的任务，继续查询进度'; updateVisibility(); setBusy(true); pollJob();
    }
    const missing = Object.entries(info.dependencies || {}).filter(([, present]) => !present).map(([name]) => name);
    $('work-location').textContent = '当前输入与结果保存在本机：' + info.work;
    if (missing.length) { $('setup-warning').hidden = false; $('setup-warning').textContent = '缺少依赖：' + missing.join('、') + '。相关读取或生成会失败。请关闭界面后运行“首次安装依赖-Windows.bat”，或阅读说明；不会自动安装。'; }
  } catch (error) { showError(error); }
})();
