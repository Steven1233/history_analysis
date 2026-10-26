/* ==========================================================================
   历史相似检索系统 · 前端逻辑（原生 JS，无构建步骤）
   ========================================================================== */
'use strict';

const $  = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => Array.from(r.querySelectorAll(s));

const VIEW_META = {
  dashboard: ['数据看板', '数据源覆盖与系统概况'],
  sources:   ['数据源', '登记 ERA5 要素目录、GMCP 降水目录与预处理产物'],
  preprocess:['数据预处理', 'GMCP 逐小时降水 → 北京时间 24/12/6 小时累计产品'],
  config:    ['检索配置', '统一参数模型：GMCP 累计降水为第一优先目标变量'],
  run:       ['执行进程', '统一检索流水线的实时进度'],
  result:    ['结果产品', '相似时刻集合、累计降水概率产品与图表'],
  ops:       ['操作记录', '任务历史与操作留痕'],
};

const STAGES = [
  ['samples', '解析样本'],
  ['read', '直读切片'],
  ['features', '特征处理'],
  ['similarity', '相似度排名'],
  ['precip', '累计降水检索'],
  ['rain', '降水统计'],
  ['figures', '生成图表'],
];

const state = {
  meta: null, dashboard: null, sources: null,
  vars: [],                 // [{key, el, level, role}]
  task: null,               // 当前任务 id
  progress: null,
  result: null,
  figs: [],                 // [[name, url]]，灯箱切换用
  figIdx: 0,
  region: null,             // {view, w, h} 圈选底图换算基准
  timer: null,
  tick: 0,
  lastStage: null,
  busy: false,
  prepMeta: null,           // 预处理页面静态字典
  prepJob: null,            // 当前预处理任务 id
  prepTimer: null,
};
const TASK_KEY = 'histsim.task';       // 当前任务持久化键（F5 后自动续跟踪）
const PREP_KEY = 'histsim.preprocess'; // 当前预处理任务持久化键
const COLLAPSE_KEY = 'histsim.collapsed';  // 配置分区折叠状态
const PRECIP_KEY = 'gmcp_precip';      // 基本要素选择区的第一要素：GMCP 累计降水

/* ── 通用工具 ─────────────────────────────────────────────────────── */
function esc(v) {
  return String(v == null ? '' : v)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
}
const num = v => (v == null ? '—' : Number(v).toLocaleString('zh-CN'));
const fx  = (v, d = 2) => (v == null || Number.isNaN(Number(v)) ? '—' : Number(v).toFixed(d));
const pct = v => (v == null ? '—' : (Number(v) * 100).toFixed(1) + '%');

let toastTimer = null;
function toast(msg, kind = '') {
  const el = $('#toast');
  el.className = 'toast ' + kind;
  el.innerHTML = msg;
  el.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { el.hidden = true; }, kind === 'bad' ? 9000 : 4200);
}

async function api(path, opts) {
  const res = await fetch(path, Object.assign({ headers: { 'Content-Type': 'application/json' } }, opts));
  let data;
  try { data = await res.json(); } catch (e) { throw new Error(`响应不是 JSON（HTTP ${res.status}）`); }
  if (!res.ok || data.ok === false) throw new Error(data.error || `HTTP ${res.status}`);
  return data;
}
const post = (path, body) => api(path, { method: 'POST', body: JSON.stringify(body || {}) });

/* ── 导航 ─────────────────────────────────────────────────────────── */
function go(view) {
  $$('.nav-item').forEach(b => b.classList.toggle('active', b.dataset.view === view));
  $$('.view').forEach(v => v.classList.toggle('active', v.id === 'view-' + view));
  const [t, s] = VIEW_META[view];
  $('#pageTitle').textContent = t;
  $('#pageSub').textContent = s;
  renderWizard(view);
  if (view === 'dashboard') loadDashboard();
  if (view === 'sources') loadSources();
  if (view === 'preprocess') loadPreprocess();
  if (view === 'run') refreshRun();
  if (view === 'result') fillResultPicker();  // 直接点导航进结果页时也要填充任务下拉
  if (view === 'ops') loadOps();
}
$$('.nav-item').forEach(b => b.addEventListener('click', () => go(b.dataset.view)));

/* ── 主流程步骤条（仅进度提示，与左侧导航并存） ──────────────────── */
const WIZ = [['preprocess', '数据预处理'], ['sources', '数据源'],
             ['config', '检索配置'], ['run', '执行进程'], ['result', '结果产品']];
function renderWizard(view) {
  const bar = $('#wizBar');
  if (!bar) return;
  if (!WIZ.some(w => w[0] === view)) { bar.hidden = true; return; }
  bar.hidden = false;
  const idx = WIZ.findIndex(w => w[0] === view);
  bar.innerHTML = WIZ.map(([k, label], i) => {
    const cls = idx >= 0 && i < idx ? 'done' : (i === idx ? 'active' : '');
    return `<button class="wstep ${cls}" data-view="${k}"><span class="n">${i + 1}</span>${label}</button>`;
  }).join('<span class="wsep">›</span>');
  $$('#wizBar .wstep').forEach(b => b.addEventListener('click', () => go(b.dataset.view)));
}
/* 各页底部「下一步」按钮 */
$$('[data-next]').forEach(b => b.addEventListener('click', () => go(b.dataset.next)));

/* ══════════════════════════ 1 · 数据看板 ══════════════════════════ */
async function loadDashboard() {
  try {
    const d = await api('/api/dashboard');
    state.dashboard = d;
    renderTopStats();
    renderKPIs(d);
    renderCoverage(d);
    renderMini('#dashTasks', (d.recent_tasks || []).map(t => ({
      t: t.created_at.replace('T', ' ').slice(5, 16),
      m: `${t.config_name || '—'} · 目标 ${t.target_date || '—'}`,
      r: `<span class="badge ${t.status === '成功' ? 'ok' : t.status === '失败' ? 'bad' : ''}">${esc(t.status)}</span>`,
    })), '还没有检索任务。');
    renderMini('#dashOps', (d.recent_ops || []).map(o => ({
      t: o.ts.replace('T', ' ').slice(5, 16),
      m: `${esc(o.op)} · ${esc(o.detail || o.target || '')}`,
      r: `<span class="badge ${o.result === '成功' ? 'ok' : o.result === '失败' ? 'bad' : ''}">${esc(o.result || '')}</span>`,
    })), '还没有操作记录。');
  } catch (e) { toast('看板加载失败：' + e.message, 'bad'); }
}

function renderTopStats() {
  const d = state.dashboard, m = state.meta;
  if (!d) return;
  const ys = d.years || {};
  const disk = d.disk || {};
  $('#topStats').innerHTML = `
    <div class="stat-pill"><span>数据源</span><b>${num(d.counts.n_era5)}</b><span>/ ${num((d.counts.n_gmcp || 0) + (d.counts.n_gmcp_rain || 0))}</span></div>
    <div class="stat-pill"><span>数据文件</span><b>${num(d.counts.n_files)}</b></div>
    <div class="stat-pill"><span>覆盖年份</span><b>${ys.from ? ys.from + '–' + ys.to : '—'}</b></div>
    ${disk.free_gb != null ? `<div class="stat-pill ok"><span>磁盘剩余</span><b>${disk.free_gb}</b><span>GB</span></div>` : ''}`;
  $('#railFoot').innerHTML =
    `<b>程序数据</b><br>${esc(d.data_dir)}<br><br>` +
    `<b>版本</b> v${esc(m ? m.version : '—')}`;
}

function renderKPIs(d) {
  const c = d.counts, ys = d.years || {};
  const nEl = Object.keys(d.coverage || {}).length;
  $('#dashKpis').innerHTML = [
    ['ERA5 要素目录', num(c.n_era5), `已登记 ${nEl} 类要素可检索`, ''],
    ['GMCP 降水目录', num((c.n_gmcp || 0) + (c.n_gmcp_rain || 0)),
      `逐小时源 ${num(c.n_gmcp || 0)} · 累计降水产品 ${num(c.n_gmcp_rain || 0)}`, 'info'],
    ['数据文件总量', num(c.n_files), '不搬移、不预处理，按需直读', 'accent'],
    ['覆盖时间跨度', ys.from ? `${ys.to - ys.from + 1} 年` : '—',
      ys.from ? `${ys.from} – ${ys.to} · 共 ${num((ys.list || []).length)} 个年份` : '尚未登记数据源', 'warn'],
  ].map(([l, v, n, k]) => `<div class="kpi ${k}"><div class="k-label">${l}</div>
      <div class="k-value">${v}</div><div class="k-note">${n}</div></div>`).join('');
}

function renderCoverage(d) {
  const cov = d.coverage || {};
  const elName = k => {
    const e = (d.elements || []).find(x => x.key === k);
    if (e) return e.name;
    const p = (d.precip_windows || []).find(x => x.key === k);
    return p ? p.name : k;
  };
  const rows = [];
  rows.push('<div class="cov-row"><div class="cov-label cov-head sp">要素 · 年份</div>' +
    Array.from({ length: 12 }, (_, i) => `<div class="cov-head">${i + 1}</div>`).join('') + '</div>');
  for (const [el, years] of Object.entries(cov)) {
    for (const [y, months] of Object.entries(years)) {
      const cells = Array.from({ length: 12 }, (_, i) => {
        const m = months[String(i + 1)];
        if (!m || !m.n) return '<div class="cov-cell" title="无数据"></div>';
        const a = 0.1 + 0.62 * m.pct;
        return `<div class="cov-cell" style="background:rgba(242,166,59,${a.toFixed(2)});
          border-color:rgba(242,166,59,.42);color:${m.pct > .55 ? '#2A1C07' : 'var(--amber)'}"
          title="${y} 年 ${i + 1} 月：${m.n} 个文件 / 覆盖 ${(m.pct * 100).toFixed(0)}%">
          ${m.pct >= .99 ? '●' : (m.pct * 100).toFixed(0)}</div>`;
      }).join('');
      rows.push(`<div class="cov-row"><div class="cov-label">${esc(elName(el))} <b>${y}</b></div>${cells}</div>`);
    }
  }
  $('#coverageMatrix').innerHTML = rows.length > 1 ? rows.join('')
    : '<div class="empty">尚未登记数据源，请到「数据源」页扫描目录。</div>';
}

function renderMini(sel, items, emptyText) {
  const el = $(sel);
  if (!items.length) { el.innerHTML = `<div class="empty">${emptyText}</div>`; return; }
  el.innerHTML = items.map(i => `<div class="mini-item"><span class="t">${esc(i.t)}</span>
    <span class="m">${i.m}</span><span class="r">${i.r}</span></div>`).join('');
}

/* ══════════════════════════ 2 · 数据源 ══════════════════════════ */
async function loadSources() {
  try {
    const s = await api('/api/sources');
    state.sources = s;
    renderSources();
  } catch (e) { toast('数据源加载失败：' + e.message, 'bad'); }
}

function renderSources() {
  const s = state.sources;
  if (!s) return;
  $('#srcCount').textContent = s.sources.length;
  const elName = k => {
    const e = (state.meta?.elements || []).find(x => x.key === k);
    if (e) return e.name;
    const p = (state.meta?.precip_windows || []).find(x => x.key === k);
    return p ? p.name : k;
  };
  if (!s.sources.length) {
    $('#srcTable').innerHTML = '<div class="empty">还没有登记任何数据源。</div>';
    return;
  }
  const rows = s.sources.map(x => {
    const months = [...new Set(x.coverage.map(c => c.month))].sort((a, b) => a - b);
    const years = [...new Set(x.coverage.map(c => c.year))].sort();
    const nf = x.coverage.reduce((a, c) => a + c.n_files, 0);
    return `<tr>
      <td><span class="badge ${x.kind === 'era5' ? 'info' : 'amber'}">${esc(x.kind.toUpperCase())}</span></td>
      <td>${esc(elName(x.element))}<span class="mono" style="color:var(--ink-mute)"> · ${esc(x.element)}</span></td>
      <td class="mono" style="max-width:420px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap"
          title="${esc(x.root)}">${esc(x.root)}</td>
      <td class="num">${num(nf)}</td>
      <td class="mono">${years.length ? years[0] + '–' + years[years.length - 1] : '—'}</td>
      <td class="mono">${months.join(',') || '—'}</td>
      <td><span class="badge ${x.status === '有效' ? 'ok' : 'bad'}">${esc(x.status)}</span></td>
      <td><button class="btn tiny" data-del="${x.id}">移除</button></td>
    </tr>`;
  }).join('');
  $('#srcTable').innerHTML = `<div class="table-wrap"><table><thead><tr>
    <th>类型</th><th>要素</th><th>目录</th><th class="num">文件数</th>
    <th>年份</th><th>月份</th><th>状态</th><th></th></tr></thead><tbody>${rows}</tbody></table></div>`;
  $$('#srcTable [data-del]').forEach(b => b.addEventListener('click', async () => {
    if (!confirm('确认移除该数据源登记？（不会删除磁盘文件）')) return;
    try { await post('/api/sources/remove', { id: Number(b.dataset.del) }); toast('已移除登记', 'ok'); loadSources(); }
    catch (e) { toast('移除失败：' + e.message, 'bad'); }
  }));
}

$('#btnScan').addEventListener('click', async () => {
  const root = $('#srcRoot').value.trim();
  if (!root) { toast('请先填写数据源根目录', 'bad'); return; }
  const btn = $('#btnScan');
  btn.disabled = true;
  $('#scanHint').innerHTML = '<span class="spinner"></span>正在扫描目录并统计覆盖，20 万级文件目录需要一点时间…';
  try {
    const rep = await post('/api/sources/register', { root, depth: Number($('#srcDepth').value) });
    const n = rep.registered.length;
    $('#scanHint').innerHTML = `扫描完成：登记 <b style="color:var(--amber)">${n}</b> 项`
      + (rep.skipped.length ? `，跳过 ${rep.skipped.length} 个未识别目录` : '')
      + (rep.errors.length ? `，${rep.errors.length} 个错误` : '');
    toast(`扫描完成，新增/更新 ${n} 项登记`, n ? 'ok' : ''); 
    state.sources = rep.sources; renderSources(); loadDashboard();
  } catch (e) {
    $('#scanHint').textContent = '扫描失败：' + e.message;
    toast('扫描失败：' + e.message, 'bad');
  } finally { btn.disabled = false; }
});

/* ══════════════════════ 3 · 数据预处理 ══════════════════════ */
function prepCollect() {
  return {
    source_root: $('#prepSrc').value.trim(),
    output_root: $('#prepOut').value.trim(),
    year_from: Number($('#prepYFrom').value),
    year_to: Number($('#prepYTo').value),
    groups: chipValues('#prepGroups'),
    overwrite: $('#prepOverwrite').checked,
    workers: Number($('#prepWorkers').value) || null,
  };
}

async function loadPreprocess() {
  if (!state.prepMeta) {
    try {
      const m = await api('/api/preprocess/meta');
      state.prepMeta = m;
      const d = m.defaults || {};
      if (!$('#prepOut').value) $('#prepOut').value = d.output_root || '';
      if (!$('#prepYFrom').value) $('#prepYFrom').value = d.year_from || 2000;
      if (!$('#prepYTo').value) $('#prepYTo').value = d.year_to || 2024;
      if (d.workers) $('#prepWorkers').placeholder = `自动（${d.workers}）`;
      chipset('#prepGroups', m.groups.map(g => g.key), k => {
        const g = m.groups.find(x => x.key === k); return g ? g.name : k;
      }, d.groups || m.groups.map(g => g.key));
      $('#prepGroups').addEventListener('click', () => updatePrepHint());
      updatePrepHint();
    } catch (e) { toast('预处理信息加载失败：' + e.message, 'bad'); return; }
  }
  if (state.prepJob) startPrepPolling();
}

function updatePrepHint() {
  const m = state.prepMeta;
  if (!m) return;
  const groups = new Set(chipValues('#prepGroups'));
  const prods = (m.products || []).filter(p => groups.has(p.group));
  $('#prepScanHint').innerHTML = prods.length
    ? `将产出 ${prods.length} 类产品：` + prods.map(p =>
        `<code>${esc(p.filename_example)}</code>`).join('、')
    : '请至少选择一个产品分组。';
}

$('#btnPrepScan').addEventListener('click', async () => {
  const p = prepCollect();
  if (!p.source_root) { toast('请先填写 GMCP 逐小时源目录', 'bad'); return; }
  const btn = $('#btnPrepScan'); btn.disabled = true;
  $('#prepScanHint').innerHTML = '<span class="spinner"></span>正在扫描源目录覆盖…';
  try {
    const r = await post('/api/preprocess/scan', p);
    const years = Object.keys(r.years || {});
    const months = [...new Set(years.flatMap(y => Object.keys(r.years[y])))].sort((a, b) => a - b);
    $('#prepScanHint').innerHTML = r.ok
      ? `源目录可用：<b style="color:var(--amber)">${num(r.n_files)}</b> 个小时文件 · 年份 ${years[0] || '—'}–${years[years.length - 1] || '—'} · 月份 ${months.join(',') || '—'}`
        + (r.axes ? ` · 网格 ${r.axes.n_lat}×${r.axes.n_lon}` : '')
        + (r.warning ? `<br><span style="color:var(--warn)">${esc(r.warning)}</span>` : '')
      : `未发现可用小时文件：${esc(r.warning || '')}`;
    if (!r.ok) toast('源目录未发现 GMCP 小时文件', 'bad');
  } catch (e) {
    $('#prepScanHint').textContent = '扫描失败：' + e.message;
    toast('扫描失败：' + e.message, 'bad');
  } finally { btn.disabled = false; }
});

$('#btnPrepStart').addEventListener('click', async () => {
  const p = prepCollect();
  if (!p.source_root) { toast('请先填写 GMCP 逐小时源目录', 'bad'); return; }
  if (!p.groups.length) { toast('请至少选择一个产品分组', 'bad'); return; }
  if (!p.output_root) { toast('请填写预处理输出目录', 'bad'); return; }
  const btn = $('#btnPrepStart'); btn.disabled = true;
  try {
    const r = await post('/api/preprocess/start', p);
    state.prepJob = r.job_id;
    try { localStorage.setItem(PREP_KEY, r.job_id); } catch (e) { /* 忽略 */ }
    $('#prepLog').innerHTML = '';
    $('#prepResult').hidden = true;
    $('#btnPrepRegister').disabled = true;
    $('#prepJobId').textContent = r.job_id;
    toast(`预处理任务已启动：<span class="mono">${esc(r.job_id)}</span>`, 'ok');
    startPrepPolling();
  } catch (e) {
    toast('启动失败：' + e.message, 'bad');
  } finally { btn.disabled = false; }
});

function stopPrepPolling() {
  clearInterval(state.prepTimer);
  state.prepTimer = null;
}

function startPrepPolling() {
  stopPrepPolling();
  const tick = async () => {
    if (!state.prepJob) return;
    try {
      const p = await api('/api/preprocess/progress?job_id=' + encodeURIComponent(state.prepJob));
      renderPrepProgress(p);
      if (p.state !== 'running') {
        stopPrepPolling();
        try { localStorage.removeItem(PREP_KEY); } catch (e) { /* 忽略 */ }
        $('#btnPrepCancel').disabled = true;
        $('#btnPrepStart').disabled = false;
        if (p.state === 'done') {
          $('#btnPrepRegister').disabled = false;
          renderPrepResult(p);
          toast(`预处理完成：写出 ${num((p.result || {}).n_written || 0)} 个产品`, 'ok');
          loadDashboard();
        } else if (p.state === 'cancelled') {
          toast('预处理已取消');
        } else {
          toast('预处理失败：' + esc(p.error), 'bad');
        }
      }
    } catch (e) {
      stopPrepPolling();
      toast('预处理进度查询失败：' + e.message, 'bad');
    }
  };
  state.prepTimer = setInterval(tick, 900);
  tick();
}

function renderPrepProgress(p) {
  $('#prepJobId').textContent = p.job_id;
  $('#prepBar').style.width = Math.min(100, p.pct) + '%';
  $('#prepPct').textContent = p.pct + '%';
  $('#prepMsg').textContent = p.msg || p.label || '';
  $('#prepElapsed').textContent = `已用 ${p.elapsed_s}s`;
  $('#prepHeadNote').innerHTML = `<span class="badge amber">${esc(p.label)}</span>` +
    (p.total ? ` <span class="mono">${num(p.done)} / ${num(p.total)}</span>` : '') +
    ` · 源目录 <span class="mono">${esc((p.params || {}).source_root || '')}</span>`;
  $('#btnPrepCancel').disabled = p.state !== 'running';
  const el = $('#prepLog');
  const near = el.scrollHeight - el.scrollTop - el.clientHeight < 40;
  el.innerHTML = (p.log || []).map(l =>
    `<div class="ln ${l.stage === 'error' ? 'err' : l.stage === 'done' ? 'ok' : ''}">
      <span class="ts">${l.t}s</span><span>${esc(l.label)}</span></div>`).join('');
  if (near) el.scrollTop = el.scrollHeight;
}

function renderPrepResult(p) {
  const r = p.result || {};
  const box = $('#prepResult');
  box.hidden = false;
  const keys = (r.products || []).map(k => {
    const prod = (state.prepMeta.products || []).find(x => x.key === k);
    return prod ? prod.label : k;
  });
  box.innerHTML = `<div class="panel-head"><h3>预处理结果</h3>
    <p>输出目录 <code>${esc(r.output_root || '')}</code> · 可直接登记为检索数据源。</p></div>
    <div class="kpis">
      <div class="kpi accent"><div class="k-label">写出产品</div><div class="k-value">${num(r.n_written)}<small>个</small></div>
        <div class="k-note">${r.year_from}–${r.year_to} · 共 ${num(r.n_expected)} 个产品时次</div></div>
      <div class="kpi"><div class="k-label">已有跳过</div><div class="k-value">${num(r.n_skipped)}</div>
        <div class="k-note">未覆盖已有文件</div></div>
      <div class="kpi warn"><div class="k-label">缺测未写</div><div class="k-value">${num(r.n_incomplete)}</div>
        <div class="k-note">小时文件不齐的日期</div></div>
      <div class="kpi"><div class="k-label">写出失败</div><div class="k-value">${num(r.n_errors)}</div>
        <div class="k-note">磁盘/权限等错误</div></div>
    </div>
    <div class="hint">产品：${keys.map(esc).join('、') || '—'}
      ${(r.error_samples || []).length ? `<br>异常样例：<span class="mono">${r.error_samples.slice(0, 4).map(esc).join(' / ')}</span>` : ''}</div>`;
}

$('#btnPrepCancel').addEventListener('click', async () => {
  if (!state.prepJob) return;
  try { await post('/api/preprocess/cancel', { job_id: state.prepJob }); toast('已发送取消请求'); }
  catch (e) { toast('取消失败：' + e.message, 'bad'); }
});

$('#btnPrepRegister').addEventListener('click', async () => {
  const root = $('#prepOut').value.trim();
  if (!root) { toast('请先填写预处理输出目录', 'bad'); return; }
  const btn = $('#btnPrepRegister'); btn.disabled = true;
  try {
    const r = await post('/api/preprocess/register', { output_root: root });
    toast(`已登记 ${r.registered.length} 项预处理数据源`, 'ok');
    $('#prepNote').innerHTML = `登记 ${r.registered.length} 项，可在「数据源」页查看。`;
    state.sources = r.sources || null;
    loadDashboard();
  } catch (e) {
    toast('登记失败：' + e.message, 'bad');
    btn.disabled = false;
  }
});

/* ══════════════════════════ 4 · 检索配置 ══════════════════════════ */
function chipset(container, values, labeler, selected) {
  const set = new Set(selected.map(String));
  $(container).innerHTML = values.map(v => {
    const vs = String(v);
    return `<button class="chip ${set.has(vs) ? 'on' : ''}" data-value="${vs}">${esc(labeler(v))}</button>`;
  }).join('');
  const sync = () => syncVarChips();
  $$('.chip', $(container)).forEach(c => c.addEventListener('click', () => {
    c.classList.toggle('on');
    if (container === '#chipElements' || container === '#chipLevels') sync();
    if (container === '#chipElements') syncPrecipMode();
  }));
}
const chipValues = sel => $$('.chip.on', $(sel)).map(c => c.dataset.value);

/* 基本要素选择区：GMCP 累计降水始终作为第一要素排在最前 */
function elementChips() {
  return [PRECIP_KEY, ...state.meta.elements.map(e => e.key)];
}
function elementLabel(k) {
  if (k === PRECIP_KEY) {
    const p = state.meta.precip_element;
    return p ? p.name : 'GMCP 累计降水';
  }
  const e = state.meta.elements.find(x => x.key === k);
  return e ? e.name : k;
}
const isPrecipOn = () => chipValues('#chipElements').includes(PRECIP_KEY);

async function initConfig() {
  const m = state.meta;
  const lv = m.builtin_keys[0];
  $('#cfgTemplate').innerHTML = m.builtin_keys.map(k =>
    `<option value="${k}">${esc(m.templates[k].name)}（${k}）</option>`).join('') +
    '<option value="">自定义（不套用模板）</option>';
  $('#cfgWeightMode').innerHTML = m.weight_modes.map(w => `<option value="${w.key}">${esc(w.name)}</option>`).join('');
  $('#cfgSimilarity').innerHTML = m.similarities.map(w => `<option value="${w.key}">${esc(w.name)}</option>`).join('');
  $('#cfgSampleMode').innerHTML = m.sample_modes.map(w => `<option value="${w.key}">${esc(w.name)}</option>`).join('');
  $('#cfgFeatureProcess').innerHTML = m.feature_processes.map(w => `<option value="${w.key}">${esc(w.name)}</option>`).join('');
  $('#simHint').innerHTML = '欧氏 / 余弦 / 相关距离不使用距离权重；<b>不处理</b>时量纲大的要素（如 rh 百分比）会主导相似度。'
    + `<br>公式帮助：${esc(m.formula_help)}`;
  chipset('#chipMonths', [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12], v => v + ' 月', [4, 5, 6]);
  initCollapse();
  chipset('#chipTimes', m.times, v => String(v).padStart(2, '0') + 'UTC', [0]);
  chipset('#chipElements', elementChips(), elementLabel,
    [PRECIP_KEY, 'u', 'v', 'rh']);
  chipset('#chipLevels', m.levels, v => v, [925, 500]);
  chipset('#chipPrecipWindows', (m.precip_windows || []).map(p => p.key), k => {
    const p = (m.precip_windows || []).find(x => x.key === k); return p ? p.name : k;
  }, m.default_precip_windows || []);
  $('#chipPrecipWindows').addEventListener('click', () => { syncPrecipMode(); updateTemplateDiff(); });
  $('#cfgPrecipTopn').addEventListener('input', updateTemplateDiff);
  $('#cfgPrecipThresh').addEventListener('input', updateTemplateDiff);
  $('#btnAllInter').addEventListener('click', () => {
    state.vars.forEach(v => { v.role = 'inter'; }); syncVarChips(); applyRolesToGroups();
  });
  $('#btnClearRole').addEventListener('click', () => {
    state.vars.forEach(v => { v.role = ''; }); syncVarChips();
  });
  $('#cfgSampleMode').addEventListener('change', () => { syncSampleMode(); updateTemplateDiff(); });
  $('#cfgSimilarity').addEventListener('change', () => { syncFormulaRow(); updateTemplateDiff(); });
  ['#cfgLat', '#cfgLon', '#cfgSpan'].forEach(id =>
    $(id).addEventListener('input', () => { syncRegionRect(); updateTemplateDiff(); }));
  $('#view-config').addEventListener('input', updateTemplateDiff);
  $('#view-config').addEventListener('click', updateTemplateDiff);
  applyTemplate(lv);
  syncVarChips();
  syncSampleMode();
  syncFormulaRow();
  updateTemplateDiff();
}

/* 折叠面板：仅配置页的 6 个分区，折叠状态记忆到 localStorage */
function initCollapse() {
  let saved = {};
  try { saved = JSON.parse(localStorage.getItem(COLLAPSE_KEY) || '{}'); } catch (e) { saved = {}; }
  $$('#view-config .grid2 > .panel').forEach((p, i) => {
    const head = $('.panel-head', p);
    if (!head) return;
    p.classList.add('collapsible');
    const key = 'p' + i;
    if (saved[key]) p.classList.add('collapsed');
    head.addEventListener('click', e => {
      if (e.target.closest('button,input,select,label,a')) return;
      p.classList.toggle('collapsed');
      saved[key] = p.classList.contains('collapsed');
      try { localStorage.setItem(COLLAPSE_KEY, JSON.stringify(saved)); } catch (err) { /* 忽略 */ }
    });
  });
}

/* 样本选取方式：同期模式忽略月份选择，把月份芯片置灰 */
function syncSampleMode() {
  const same = $('#cfgSampleMode').value === 'same_period';
  $('#chipMonths').closest('.chipset-block').classList.toggle('disabled', same);
  $('#chipMonths').classList.toggle('disabled', same);
  $('#sampleModeHint').innerHTML = same
    ? '同期模式：以目标日月日为锚点，各年份前后 ±x 天取样，<b>忽略月份选择</b>，并自动跳过目标年。'
    : '日历模式：按所选年份 × 月份 × 当月每一天取样；月份芯片在此模式下生效。';
}

/* 自定义相似度公式行：仅选 custom 时显示 */
function syncFormulaRow() {
  $('#formulaRow').hidden = $('#cfgSimilarity').value !== 'custom';
}

/* 累计降水作为第一要素：未勾选时置灰窗口选择 */
function syncPrecipMode() {
  const on = isPrecipOn();
  $('#precipWinBlock').classList.toggle('disabled', !on);
  $('#chipPrecipWindows').classList.toggle('disabled', !on);
  $('#cfgPrecipTopn').disabled = !on;
  $('#cfgPrecipThresh').disabled = !on;
  const picked = chipValues('#chipPrecipWindows');
  const era5 = chipValues('#chipElements').filter(k => k !== PRECIP_KEY);
  let hint;
  if (!on) {
    hint = era5.length
      ? '未选累计降水：仅使用 ERA5 要素检索。'
      : '请至少选择一个要素（GMCP 累计降水或 ERA5 要素）。';
  } else if (!picked.length) {
    hint = '已选 GMCP 累计降水但未选择窗口：请至少勾选一个累计降水窗口。';
  } else {
    hint = `已选 ${picked.length} 个窗口；每个窗口独立检索 TopN，并生成平均/中位数/≥阈值概率产品。`;
    if (!era5.length) hint += ' <b>当前仅选累计降水：按单要素执行完整流程，不做交集。</b>';
  }
  $('#precipHint').innerHTML = hint;
}

/* 模板差异计数：相对内置模板默认值改了多少项 */
function updateTemplateDiff() {
  const el = $('#tmplDiff');
  if (!el) return;
  const t = state.meta.templates[$('#cfgTemplate').value];
  if (!t) { el.textContent = '未套用模板：全部参数以当前表单为准。'; return; }
  const c = collect();
  const pairs = [
    ['center_lat', c.center_lat], ['center_lon', c.center_lon], ['half_span_deg', c.half_span_deg],
    ['year_from', c.year_from], ['year_to', c.year_to], ['inter_topn', c.inter_topn],
    ['direct_topn', c.direct_topn], ['feature_process', c.feature_process],
    ['similarity', c.similarity], ['sample_mode', c.sample_mode], ['period_days', c.period_days],
    ['precip_enabled', c.precip_enabled], ['precip_topn', c.precip_topn],
    ['precip_windows', (c.precip_windows || []).join(',')],
  ];
  const n = pairs.filter(([k, v]) => String(t[k]) !== String(v)).length;
  el.textContent = n ? `已修改 ${n} 项相对模板默认值（其余沿用 ${t.name}）。` : '与模板默认值一致。';
}

function applyTemplate(key) {
  const t = state.meta.templates[key];
  if (!t) return;
  const set = (id, v) => { const el = $(id); if (v != null) el.value = v; };
  set('#cfgName', t.name); set('#cfgLat', t.center_lat); set('#cfgLon', t.center_lon);
  set('#cfgSpan', t.half_span_deg); set('#cfgPtLat', t.point_lat); set('#cfgPtLon', t.point_lon);
  set('#cfgExcl', t.exclude_days); set('#cfgYFrom', t.year_from); set('#cfgYTo', t.year_to);
  set('#cfgGauss', t.gauss_r0_km); set('#cfgInterTopn', t.inter_topn); set('#cfgDirectTopn', t.direct_topn);
  set('#cfgMinHits', t.partial_min_hits);
  set('#cfgRainWin', t.rain_window.join(',')); set('#cfgRainWin24', t.rain_window_24h.join(','));
  set('#cfgThresh', t.rain_thresholds.join(','));
  set('#cfgZones', (t.zones || []).map(z => z[0] + ',' + z[1]).join('; '));
  $('#cfgWeightMode').value = t.weight_mode; $('#cfgSimilarity').value = t.similarity;
  set('#cfgSampleMode', t.sample_mode); set('#cfgPeriodDays', t.period_days);
  set('#cfgFeatureProcess', t.feature_process);
  set('#cfgSimilarityFormula', t.similarity_formula || '');
  $('#cfgPlotDirect').checked = !!t.plot_direct_fields;
  $('#cfgEnable24').checked = !!t.enable_24h;
  $('#cfgPartial').checked = !!t.partial_intersection;
  set('#cfgPrecipTopn', t.precip_topn);
  set('#cfgPrecipThresh', (t.precip_thresholds || []).join(','));
  const m = state.meta;
  chipset('#chipMonths', [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12], v => v + ' 月', t.months);
  chipset('#chipTimes', m.times, v => String(v).padStart(2, '0') + 'UTC', t.times);
  const els = [...new Set(t.variables.map(v => v[0]))];
  const lvs = [...new Set(t.variables.map(v => v[1]))];
  chipset('#chipElements', elementChips(), elementLabel,
    [...(t.precip_enabled ? [PRECIP_KEY] : []), ...els]);
  chipset('#chipLevels', m.levels, v => v, lvs);
  chipset('#chipPrecipWindows', (m.precip_windows || []).map(p => p.key), k => {
    const p = (m.precip_windows || []).find(x => x.key === k); return p ? p.name : k;
  }, t.precip_windows || []);
  syncVarChips(t.variables, t.inter_group, t.direct_group);
  const d = state.dashboard;
  if (!$('#cfgDate').value && d && d.years.to) $('#cfgDate').value = `${d.years.to}-06-30`;
  syncSampleMode(); syncFormulaRow(); syncPrecipMode(); syncRegionRect(); updateTemplateDiff();
}

/** 依据勾选的要素×层次重建变量卡片，尽量保留已有角色分配。 */
function syncVarChips(presetVars, presetInter, presetDirect) {
  const roleOf = {};
  state.vars.forEach(v => { roleOf[v.key] = v.role; });
  if (presetVars) {
    presetVars.forEach(([e, l]) => {
      const k = e + l;
      if (presetInter && presetInter.includes(k)) roleOf[k] = 'inter';
      else if (presetDirect && presetDirect.includes(k)) roleOf[k] = 'direct';
    });
  }
  const els = chipValues('#chipElements').filter(k => k !== PRECIP_KEY);
  const lvs = chipValues('#chipLevels').map(Number);
  const next = [];
  for (const l of lvs) for (const e of els) next.push({ key: e + l, el: e, level: l, role: roleOf[e + l] || '' });
  if (next.length === 1 && !next[0].role) next[0].role = 'inter';  // 单要素默认走完整流程
  state.vars = next;
  renderVarChips();
}

function renderVarChips() {
  const m = state.meta;
  const elName = k => { const e = m.elements.find(x => x.key === k); return e ? e.name : k; };
  const precipOn = isPrecipOn();
  $('#varCount').textContent = state.vars.length + (precipOn ? 1 : 0);
  const precipChip = precipOn
    ? `<span class="vchip role-inter" title="独立完整检索流程：以累计降水场为目标变量，不参与交集">
        <span class="dot"></span>GMCP 累计降水 <b>第一要素</b></span>`
    : '';
  const roleChips = state.vars.map((v, i) =>
    `<button class="vchip ${v.role ? 'role-' + v.role : ''}" data-i="${i}"
      title="点击切换：不参与 → 交集组 → 直取组">
      <span class="dot"></span>${esc(elName(v.el))} <b>${v.level}</b></button>`).join('');
  $('#varChips').innerHTML = (precipChip + roleChips)
    || '<div class="empty" style="padding:12px">请先勾选要素与气压层。</div>';
  $$('#varChips .vchip[data-i]').forEach(b => b.addEventListener('click', () => {
    const v = state.vars[Number(b.dataset.i)];
    v.role = v.role === '' ? 'inter' : v.role === 'inter' ? 'direct' : '';
    renderVarChips();
  }));
  $('#varRoleHint').innerHTML = (state.vars.length === 1 && !precipOn)
    ? '仅一个要素：自动按该要素 TopN 执行完整流程，<b>不使用交集</b>。'
    : (precipOn && !state.vars.length
      ? '仅 GMCP 累计降水：按单要素执行完整流程，<b>不使用交集</b>。'
      : '');
}

function applyRolesToGroups() { renderVarChips(); }

function parsePairs(txt) {
  return String(txt || '').split(';').map(s => s.trim()).filter(Boolean).map(s => {
    const [km, w] = s.split(',').map(x => Number(x.trim()));
    return [km, w];
  }).filter(p => p.length === 2 && !p.some(Number.isNaN));
}
const parseInts = txt => String(txt || '').split(',').map(s => Number(s.trim()))
  .filter(n => !Number.isNaN(n));
const parseFloats = txt => String(txt || '').split(',').map(s => Number(s.trim()))
  .filter(n => !Number.isNaN(n));

function collect() {
  let inter = state.vars.filter(v => v.role === 'inter').map(v => v.key);
  let direct = state.vars.filter(v => v.role === 'direct').map(v => v.key);
  // 单一要素：未指定角色时默认走完整流程（后端同样会归一化）
  if (state.vars.length === 1 && !inter.length && !direct.length) inter = [state.vars[0].key];
  return {
    template: $('#cfgTemplate').value || null,
    name: $('#cfgName').value.trim() || '自定义配置',
    target_date: $('#cfgDate').value,
    center_lat: Number($('#cfgLat').value), center_lon: Number($('#cfgLon').value),
    half_span_deg: Number($('#cfgSpan').value),
    point_lat: Number($('#cfgPtLat').value), point_lon: Number($('#cfgPtLon').value),
    year_from: Number($('#cfgYFrom').value), year_to: Number($('#cfgYTo').value),
    months: chipValues('#chipMonths').map(Number),
    times: chipValues('#chipTimes').map(Number),
    variables: state.vars.map(v => [v.el, v.level]),
    weight_mode: $('#cfgWeightMode').value,
    zones: parsePairs($('#cfgZones').value),
    gauss_r0_km: Number($('#cfgGauss').value),
    feature_process: $('#cfgFeatureProcess').value,
    similarity: $('#cfgSimilarity').value,
    similarity_formula: $('#cfgSimilarityFormula').value.trim(),
    sample_mode: $('#cfgSampleMode').value,
    period_days: Number($('#cfgPeriodDays').value),
    plot_direct_fields: $('#cfgPlotDirect').checked,
    exclude_days: Number($('#cfgExcl').value),
    inter_group: inter, inter_topn: Number($('#cfgInterTopn').value),
    direct_group: direct, direct_topn: Number($('#cfgDirectTopn').value),
    partial_intersection: $('#cfgPartial').checked,
    partial_min_hits: Number($('#cfgMinHits').value),
    rain_window: parseInts($('#cfgRainWin').value),
    rain_window_24h: parseInts($('#cfgRainWin24').value),
    rain_thresholds: parseFloats($('#cfgThresh').value),
    enable_24h: $('#cfgEnable24').checked,
    precip_enabled: isPrecipOn(),
    precip_windows: chipValues('#chipPrecipWindows'),
    precip_topn: Number($('#cfgPrecipTopn').value),
    precip_thresholds: parseFloats($('#cfgPrecipThresh').value),
  };
}

/* 就地校验：错误信息挂到对应字段下方，同时保留 toast */
function fieldError(id, msg) {
  const el = $('#' + id);
  if (!el) return;
  const field = el.closest('.field') || el.parentElement || el;
  let err = field.querySelector(':scope > .field-err');
  if (!err) { err = document.createElement('span'); err.className = 'field-err'; field.appendChild(err); }
  err.textContent = msg;
}
function clearFieldErrors() { $$('#view-config .field-err').forEach(e => e.remove()); }

function cfgGuard() {
  const c = collect();
  const hasPrecip = c.precip_enabled && c.precip_windows.length;
  if (!c.target_date) return { id: 'cfgDate', msg: '请选择目标日期（同期模式为必填）' };
  if (!c.variables.length && !hasPrecip) return { id: 'chipElements', msg: '请至少勾选一个要素（GMCP 累计降水为第一要素，也可选 ERA5 要素）' };
  if (!c.inter_group.length && !c.direct_group.length && !hasPrecip) return { id: 'varChips', msg: '请为变量指定交集组或直取组（单一要素会自动执行完整流程）' };
  if (c.precip_enabled && !c.precip_windows.length) return { id: 'chipPrecipWindows', msg: '已勾选 GMCP 累计降水，请至少选择一个窗口' };
  if (c.precip_enabled && !c.precip_thresholds.length) return { id: 'cfgPrecipThresh', msg: '请填写累计降水概率阈值' };
  if (c.sample_mode === 'calendar' && !c.months.length) return { id: 'chipMonths', msg: '请至少选择一个月份' };
  if (!c.times.length) return { id: 'chipTimes', msg: '请至少选择一个检索时次' };
  if (c.similarity === 'custom' && !c.similarity_formula) return { id: 'cfgSimilarityFormula', msg: '自定义相似度需填写公式，例如 sqrt(sum(w*d*d))' };
  return null;
}

/* 后端校验文案 → 就近字段（用于 /api/preview 的 errors） */
const ERR_RULES = [
  [/目标日期/, 'cfgDate'], [/样本选取方式/, 'cfgSampleMode'], [/同期前后/, 'cfgPeriodDays'],
  [/特征处理/, 'cfgFeatureProcess'], [/公式/, 'cfgSimilarityFormula'], [/相似度/, 'cfgSimilarity'],
  [/月份/, 'chipMonths'], [/时次/, 'chipTimes'], [/中心/, 'cfgLat'], [/半边长/, 'cfgSpan'],
  [/TopN/i, 'cfgInterTopn'], [/年份/, 'cfgYFrom'], [/距离分区|分区/, 'cfgZones'], [/降水阈值/, 'cfgThresh'],
  [/累计降水窗口/, 'chipPrecipWindows'], [/累计降水相似 TopN/, 'cfgPrecipTopn'],
  [/累计降水概率阈值/, 'cfgPrecipThresh'],
];
function showApiErrors(errors) {
  (errors || []).forEach(msg => {
    const rule = ERR_RULES.find(([re]) => re.test(msg));
    if (rule) fieldError(rule[1], msg);
  });
}

$('#cfgTemplate').addEventListener('change', e => {
  if (e.target.value) { applyTemplate(e.target.value); toast('已套用模板，可在其基础上覆盖任意参数'); }
});

$('#btnPreview').addEventListener('click', async () => {
  clearFieldErrors();
  const bad = cfgGuard();
  if (bad) { fieldError(bad.id, bad.msg); toast(bad.msg, 'bad'); return; }
  const btn = $('#btnPreview'); btn.disabled = true;
  const panel = $('#previewPanel');
  panel.hidden = false;
  panel.innerHTML = '<div class="panel-head"><h3><span class="spinner"></span>代价预估中…</h3><p>正在按所选样本方式逐日核对各要素文件是否齐备。</p></div>';
  try {
    const p = await post('/api/preview', { config: collect() });
    if (!p.ok) {
      panel.innerHTML = `<div class="panel-head"><h3>配置校验未通过</h3></div>` +
        p.errors.map(e => `<div class="mini-item"><span class="m" style="color:var(--bad)">${esc(e)}</span></div>`).join('');
      showApiErrors(p.errors);
      toast('配置校验未通过', 'bad');
      return;
    }
    const c = p.config;
    const precipCard = c.precip_enabled ? `
        <div class="kpi ${p.precip_ready ? 'accent' : 'warn'}"><div class="k-label">累计降水候选 / 产品文件</div>
          <div class="k-value">${num(p.n_precip_samples)}<small>日 / ${num(p.n_precip_files)} 个</small></div>
          <div class="k-note">${num(p.n_precip_windows)} 个窗口 · ${p.precip_ready ? '目标日产品齐备' : '缺少预处理产物，请先登记'}</div></div>` : '';
    panel.innerHTML = `<div class="panel-head"><h3>代价预估</h3>
      <p>配置指纹 <code>${esc(c.fingerprint)}</code> · 相同指纹的重复检索可直接复用切片缓存。</p></div>
      <div class="kpis">
        <div class="kpi"><div class="k-label">候选历史样本</div><div class="k-value">${num(p.n_samples)}<small>个</small></div>
          <div class="k-note">${p.sample_range ? p.sample_range[0] + ' ~ ' + p.sample_range[1] : '无齐备样本'}</div></div>
        <div class="kpi info"><div class="k-label">待读 ERA5 文件</div><div class="k-value">${num(p.n_era5_files)}<small>个</small></div>
          <div class="k-note">要素×层次 ${c.variable_keys.length} · 时次 ${c.times.length} · 每要素 ${num(p.n_era5_files_per_variable)} 日</div></div>
        ${precipCard}
        <div class="kpi ${p.ready ? 'accent' : 'warn'}"><div class="k-label">数据覆盖度</div><div class="k-value">${p.coverage_pct}<small>%</small></div>
          <div class="k-note">${p.ready ? '数据就绪' : '存在 ' + num(p.n_gaps) + ' 个要素·年月缺口'}</div></div>
        <div class="kpi warn"><div class="k-label">交集 TopN</div><div class="k-value">${num(c.inter_topn)}<small>/ ${num(p.n_samples)}</small></div>
          <div class="k-note">占比 ${p.n_samples ? (100 * c.inter_topn / p.n_samples).toFixed(1) : '0'}% · 命中的 >=1 个要素进入各自 TopN</div></div>
      </div>`;
    if (!p.n_samples) toast('所选年份/月份范围没有齐备的历史样本，请调整时间域或补充数据源', 'bad');
  } catch (e) {
    panel.innerHTML = `<div class="panel-head"><h3>预估失败</h3></div><div class="hint">${esc(e.message)}</div>`;
    toast('预估失败：' + e.message, 'bad');
  } finally { btn.disabled = false; }
});

$('#btnStart').addEventListener('click', async () => {
  clearFieldErrors();
  const bad = cfgGuard();
  if (bad) { fieldError(bad.id, bad.msg); toast(bad.msg, 'bad'); return; }
  if (state.busy) { toast('已有任务在运行中', 'bad'); return; }
  const btn = $('#btnStart');
  btn.disabled = true; $('#cfgNote').innerHTML = '<span class="spinner"></span>正在启动…';
  try {
    const r = await post('/api/search/start', { config: collect() });
    saveTask(r.task_id);
    state.progress = null; state.lastStage = null;
    $('#runLog').innerHTML = '';
    go('run');
    startPolling();
    toast(`任务已启动：<span class="mono">${esc(r.task_id)}</span>`, 'ok');
  } catch (e) {
    toast('启动失败：' + e.message, 'bad');
  } finally { btn.disabled = false; $('#cfgNote').textContent = ''; }
});

/* ═══════════ 3.1 · 地图圈选检索区域（第 5 项） ═══════════════════ */
const clamp01 = v => Math.max(0, Math.min(1, v));

/* 按配置值反算像素并重绘方框（所见即所得：画出的就是实际生效的正方形） */
function syncRegionRect() {
  const rect = $('#regionRect'), info = $('#regionInfo');
  if (!rect) return;
  const v = state.region && state.region.view;
  const lat = Number($('#cfgLat').value), lon = Number($('#cfgLon').value), s = Number($('#cfgSpan').value);
  if (!v || !Number.isFinite(lat) || !Number.isFinite(lon) || !Number.isFinite(s) || s <= 0) {
    rect.hidden = true; info.textContent = v ? '未圈选（可直接编辑经纬度，或拖拽底图）' : '底图加载中…';
    return;
  }
  const nx0 = clamp01((lon - s - v[0]) / (v[1] - v[0]));
  const nx1 = clamp01((lon + s - v[0]) / (v[1] - v[0]));
  const ny0 = clamp01((v[3] - (lat + s)) / (v[3] - v[2]));
  const ny1 = clamp01((v[3] - (lat - s)) / (v[3] - v[2]));
  rect.hidden = false;
  rect.style.left = (nx0 * 100) + '%';
  rect.style.top = (ny0 * 100) + '%';
  rect.style.width = ((nx1 - nx0) * 100) + '%';
  rect.style.height = ((ny1 - ny0) * 100) + '%';
  info.innerHTML = `中心 ${fx(lat)}°N / ${fx(lon)}°E · 半边长 ${fx(s, 2)}°（≈${Math.round(s * 111)} km）`;
}

function initRegionPicker() {
  const overlay = $('#regionOverlay');
  if (!overlay) return;
  api('/api/region/bounds').then(b => {
    state.region = { view: b.view, w: b.width, h: b.height };
    syncRegionRect();
  }).catch(e => {
    $('#regionInfo').textContent = '底图加载失败：' + e.message;
  });

  // 把鼠标位置换算为底图内的归一化坐标 (0..1)，再换算为经纬度
  const frac = e => {
    const r = overlay.getBoundingClientRect();
    return [clamp01((e.clientX - r.left) / r.width), clamp01((e.clientY - r.top) / r.height)];
  };
  const toLL = (nx, ny) => {
    const v = state.region.view;
    return [v[3] - ny * (v[3] - v[2]), v[0] + nx * (v[1] - v[0])];  // [lat, lon]
  };
  const drawBox = (a, b, c, d) => {   // 归一化框
    const rect = $('#regionRect');
    rect.hidden = false;
    rect.style.left = (a * 100) + '%'; rect.style.top = (c * 100) + '%';
    rect.style.width = ((b - a) * 100) + '%'; rect.style.height = ((d - c) * 100) + '%';
  };
  let drag = null;
  overlay.addEventListener('pointerdown', e => {
    if (!state.region) return;
    overlay.setPointerCapture(e.pointerId);
    const [nx, ny] = frac(e);
    drag = { nx, ny };
    drawBox(nx, nx, ny, ny);
  });
  overlay.addEventListener('pointermove', e => {
    if (!drag) return;
    const [nx, ny] = frac(e);
    drag.cur = [nx, ny];
    drawBox(Math.min(drag.nx, nx), Math.max(drag.nx, nx), Math.min(drag.ny, ny), Math.max(drag.ny, ny));
  });
  overlay.addEventListener('pointerup', e => {
    if (!drag) return;
    const [nx, ny] = frac(e);
    const a = Math.min(drag.nx, nx), b = Math.max(drag.nx, nx);
    const c = Math.min(drag.ny, ny), d = Math.max(drag.ny, ny);
    drag = null;
    const { w, h } = state.region;
    if ((b - a) * w < 6 || (d - c) * h < 6) { syncRegionRect(); return; }  // 过小视为点击
    const [lat1, lon0] = toLL(a, c);   // 左上：lat 大、lon 小
    const [lat0, lon1] = toLL(b, d);   // 右下：lat 小、lon 大
    const span = Math.max(lon1 - lon0, lat1 - lat0) / 2;
    $('#cfgLat').value = ((lat0 + lat1) / 2).toFixed(2);
    $('#cfgLon').value = ((lon0 + lon1) / 2).toFixed(2);
    $('#cfgSpan').value = Math.max(0.05, span).toFixed(2);
    clearFieldErrors(); syncRegionRect(); updateTemplateDiff();
  });

  $('#btnRegionSync').addEventListener('click', () => {
    if (!state.region) { toast('底图尚未加载完成', 'bad'); return; }
    syncRegionRect(); toast('已按当前经纬度回填方框', 'ok');
  });
  $('#btnRegionClear').addEventListener('click', () => {
    $('#regionRect').hidden = true;
    $('#regionInfo').textContent = '已清除圈选（配置数值保持不变）';
  });
}

/* ══════════════════════════ 4 · 执行进程 ══════════════════════════ */
function renderStepper(cur) {
  const idx = STAGES.findIndex(s => s[0] === cur);
  const finished = cur === 'done';
  $('#runStepper').innerHTML = STAGES.map(([k, label], i) => {
    let cls = '';
    if (finished || (idx >= 0 && i < idx)) cls = 'done';
    else if (idx >= 0 && i === idx) cls = 'active';
    return `<div class="step ${cls}"><span class="n">${String(i + 1).padStart(2, '0')}</span>${label}</div>`;
  }).join('');
}

/* 当前任务持久化：F5 / 重开浏览器后仍能自动续跟踪 */
function saveTask(id) {
  state.task = id;
  try { localStorage.setItem(TASK_KEY, id); } catch (e) { /* 忽略 */ }
}
function clearTask() {
  try { localStorage.removeItem(TASK_KEY); } catch (e) { /* 忽略 */ }
}

function startPolling() {
  clearInterval(state.timer);
  $('#runDot').classList.add('on');
  state.tick = 0;
  state.timer = setInterval(async () => {
    state.tick++;
    if (state.tick % 3 === 0) loadLiveTasks();   // 会话任务表节流刷新（≈2.7s）
    poll();
  }, 900);
  poll();
  loadLiveTasks();
}

function stopPolling() {
  clearInterval(state.timer);
  state.timer = null;
  $('#runDot').classList.remove('on');
}

/* 回到「执行进程」页时决定：继续轮询，还是只刷一帧 */
function ensureRunLoop() {
  const alive = state.task && (!state.progress || state.progress.state === 'running');
  if (alive) startPolling();
  else { poll(); loadLiveTasks(); }
}

async function poll() {
  if (!state.task) return;
  let p;
  try { p = await api('/api/search/progress?task_id=' + encodeURIComponent(state.task)); }
  catch (e) { stopPolling(); toast('进度查询失败：' + e.message, 'bad'); return; }
  state.progress = p;
  renderStepper(p.state === 'done' ? 'done' : p.stage);
  $('#runTaskId').textContent = p.task_id;
  $('#runBar').style.width = Math.min(100, p.pct) + '%';
  $('#runPct').textContent = p.pct + '%';
  $('#runMsg').textContent = p.msg || p.label || '';
  $('#runElapsed').textContent = `已用 ${p.elapsed_s}s`;
  $('#runHeadNote').innerHTML = `<span class="badge amber">${esc(p.label)}</span>` +
    (p.total ? ` <span class="mono">${num(p.done)} / ${num(p.total)}</span>` : '') +
    ` · 目标日期 <span class="mono">${esc(p.target_date)}</span> · 配置 <b style="color:var(--amber)">${esc(p.config.name)}</b>`;
  renderLog(p.log || [], p.state);
  $('#btnCancel').disabled = p.state !== 'running';

  if (p.state !== 'running') {
    stopPolling();
    clearTask();
    $('#btnOpenResult').disabled = false;
    if (p.state === 'done') {
      toast(`检索完成，耗时 ${p.elapsed_s}s；结果已生成`, 'ok');
      await loadLiveTasks();
      fillResultPicker();
      await loadResult(p.task_id);
    } else if (p.state === 'cancelled') {
      toast('任务已取消');
    } else {
      toast('任务失败：' + esc(p.error), 'bad');
    }
    $('#runHeadNote').innerHTML +=
      ` · <span class="badge ${p.state === 'done' ? 'ok' : 'bad'}">${p.state}</span>`;
    loadDashboard();
  }
}

function renderLog(log, taskState) {
  const el = $('#runLog');
  const near = el.scrollHeight - el.scrollTop - el.clientHeight < 40;
  el.innerHTML = log.map(l => {
    const cls = l.stage === 'error' ? 'err' : (l.stage === 'done' ? 'ok' : '');
    return `<div class="ln ${cls}"><span class="ts">${l.t}s</span><span>${esc(l.label)}</span></div>`;
  }).join('');
  if (near) el.scrollTop = el.scrollHeight;
}

$('#btnCancel').addEventListener('click', async () => {
  if (!state.task) return;
  try { await post('/api/search/cancel', { task_id: state.task }); toast('已发送取消请求'); }
  catch (e) { toast('取消失败：' + e.message, 'bad'); }
});
$('#btnOpenResult').addEventListener('click', () => { fillResultPicker(); go('result'); if (state.task) loadResult(state.task); });

async function refreshRun() {
  ensureRunLoop();                       // 运行中 → 重启轮询；否则只刷一帧
  if ($('#view-run').classList.contains('active')) {
    const el = $('#runLog');              // 视图隐藏时 clientHeight=0，切换回来强制贴底一次
    el.scrollTop = el.scrollHeight;
  }
}

async function loadLiveTasks() {
  try {
    const t = await api('/api/tasks?limit=30');
    const live = Object.values(t.live || {});
    const rows = live.length ? live.map(p => `<tr>
      <td class="mono">${esc(p.task_id)}</td>
      <td class="mono">${esc(p.target_date)}</td>
      <td>${esc(p.config.name)}</td>
      <td><span class="badge ${p.state === 'done' ? 'ok' : p.state === 'running' ? 'amber' : 'bad'}">${esc(p.state)}</span></td>
      <td class="num">${p.pct}%</td>
      <td class="mono">${esc(p.label)}</td>
      <td class="num">${p.elapsed_s}s</td>
      <td><button class="btn tiny" data-open="${esc(p.task_id)}">查看</button>${
        p.state === 'running' ? `<button class="btn tiny danger" data-cancel="${esc(p.task_id)}">取消</button>` : ''}</td>
    </tr>`).join('') : '';
    $('#runLive').innerHTML = rows
      ? `<div class="table-wrap"><table><thead><tr><th>任务</th><th>目标日期</th><th>配置</th>
         <th>状态</th><th class="num">进度</th><th>阶段</th><th class="num">耗时</th><th></th></tr></thead>
         <tbody>${rows}</tbody></table></div>`
      : '<div class="empty">本次会话还没有任务。请到「检索配置」页发起检索。</div>';
    $$('#runLive [data-open]').forEach(b => b.addEventListener('click', () => {
      saveTask(b.dataset.open);
      state.progress = null;
      go('run');
      startPolling();
    }));
    $$('#runLive [data-cancel]').forEach(b => b.addEventListener('click', async () => {
      try {
        await post('/api/search/cancel', { task_id: b.dataset.cancel });
        toast('已发送取消请求');
        loadLiveTasks();
      } catch (e) { toast('取消失败：' + e.message, 'bad'); }
    }));
  } catch (e) { /* 静默：会话列表非关键路径 */ }
}

/* ══════════════════════════ 5 · 结果产品 ══════════════════════════ */
/* 任务 ID 前缀是 YYYYMMDD_HHMMSS（发起时刻），解析成可读时间便于查找 */
function taskTime(id) {
  const m = /^(\d{4})(\d{2})(\d{2})_(\d{2})(\d{2})(\d{2})/.exec(id || '');
  return m ? `${m[1]}-${m[2]}-${m[3]} ${m[4]}:${m[5]}:${m[6]}` : String(id || '');
}

function fillResultPicker() {
  const sel = $('#resultTaskSel');
  api('/api/tasks?limit=50').then(t => {
    const items = [];
    Object.values(t.live || {}).filter(p => p.state === 'done').forEach(p => items.push({
      id: p.task_id, target: p.target_date,
      name: (p.config && p.config.name) || '', hist: false,
    }));
    const seen = new Set(items.map(x => x.id));
    // 历史任务：进程重启后本次会话没有 live 记录，但结果已落盘，仍可回看
    (t.history || [])
      .filter(x => x.status === '成功' && x.task_id && !seen.has(x.task_id))
      .forEach(x => items.push({ id: x.task_id, target: x.target_date,
                                 name: x.config_name || '', hist: true }));
    // 本次会话任务与历史任务统一按任务时间倒序（最新在前），不再各排各的
    items.sort((a, b) => (a.id < b.id ? 1 : a.id > b.id ? -1 : 0));
    sel.innerHTML = items.length ? items.map(x =>
      `<option value="${esc(x.id)}">${esc(taskTime(x.id))} · ${esc(x.target || '—')} · ${esc(x.name || '—')}${x.hist ? ' · 历史' : ''}</option>`).join('')
      : '<option value="">（暂无已完成任务）</option>';
    if (state.task && items.some(x => x.id === state.task)) sel.value = state.task;
  }).catch(() => { sel.innerHTML = '<option value="">加载失败</option>'; });
}
$('#btnLoadResult').addEventListener('click', () => {
  const id = $('#resultTaskSel').value;
  if (!id) { toast('没有可载入的任务', 'bad'); return; }
  loadResult(id);
});
$('#resultTaskSel').addEventListener('change', () => {   // 下拉即切换
  const id = $('#resultTaskSel').value;
  if (id) loadResult(id);
});

async function loadResult(taskId) {
  const box = $('#resultBody');
  box.innerHTML = '<div class="empty"><span class="spinner"></span>正在载入结果…</div>';
  try {
    const r = await api('/api/search/result?task_id=' + encodeURIComponent(taskId));
    if (!r.ok) {
      box.innerHTML = `<div class="empty">任务尚未产出结果：${esc(r.state || '')} ${esc(r.error || '')}</div>`;
      return;
    }
    state.result = r;
    renderResult(r);
  } catch (e) {
    box.innerHTML = `<div class="empty">载入失败：${esc(e.message)}</div>`;
    toast('载入失败：' + e.message, 'bad');
  }
}

/* 图表排序权重：precip_* → precip_quantile_* → field_* → field_direct_*
   → rain_bars → spatial_* */
function figRank(k) {
  if (k.startsWith('precip_quantile_')) return 1;
  if (k.startsWith('precip_')) return 0;
  if (k.startsWith('field_direct_')) return 3;
  if (k.startsWith('field_')) return 2;
  if (k.startsWith('rain_bars')) return 4;
  if (k.startsWith('spatial_')) return 5;
  return 6;
}

function figLabel(k) {
  if (k.startsWith('precip_quantile_')) return '累计降水分位数产品图';
  if (k.startsWith('precip_')) return '累计降水相似产品图';
  if (k.startsWith('field_direct_')) return '直取要素场图';
  if (k.startsWith('field_')) return '要素场对比图';
  if (k === 'rain_bars') return '降水统计图';
  if (k.startsWith('spatial_')) return '降水概率空间分布图';
  return k;
}

/* ── 检索配置回放：把随结果落盘的 config 原样铺开（结果里的 config 最可靠）── */
function cfgLabel(list, key) {
  const hit = (list || []).find(x => x.key === key);
  return hit ? hit.name : (key || '—');
}
function cfgVars(cfg) {
  const names = {};
  ((state.meta && state.meta.elements) || []).forEach(e => { names[e.key] = e.name; });
  const inter = cfg.inter_group || [], direct = cfg.direct_group || [];
  return (cfg.variables || []).map(([e, l]) => {
    const k = e + l;
    const role = inter.includes(k) ? '交集' : direct.includes(k) ? '直取' : '仅排名';
    return `${names[e] || e} ${l}hPa（${role}）`;
  }).join('、') || '—';
}
function cfgSingleTarget(cfg) {
  const keys = [...(cfg.inter_group || []), ...(cfg.direct_group || [])]
    .filter((v, i, a) => a.indexOf(v) === i);
  return keys.length === 1;
}
function cfgWeight(cfg) {
  if (cfg.weight_mode === 'gauss') return `高斯衰减（r0 = ${cfg.gauss_r0_km} km）`;
  const z = (cfg.zones || []).map(([d, w]) => `≤${d}km→${w}`).join('，');
  return z ? `距离分区（${z}）` : '距离分区（未设分区）';
}
function cfgRain(cfg) {
  const w6 = (cfg.rain_window || []).join(', ');
  const n24 = (cfg.rain_window_24h || []).length;
  return `6h 偏移 [${w6}]` + (cfg.enable_24h ? ` · 24h 共 ${n24} 小时` : ' · 不生成 24h');
}

function renderConfigPanel(cfg, targetDate) {
  const m = state.meta || {};
  const rows = [
    ['配置名称', cfg.name],
    ['目标日期', targetDate || cfg.target_date],
    ['配置指纹', cfg.fingerprint],
    ['样本选取', cfgLabel(m.sample_modes, cfg.sample_mode) +
      (cfg.sample_mode === 'same_period' ? ` · 前后 ${cfg.period_days} 天` : '')],
    ['年份范围', `${cfg.year_from}–${cfg.year_to}`],
    ['月份', (cfg.months || []).join(', ') || '—'],
    ['检索时次', (cfg.times || []).map(t => t + ' UTC').join(', ') || '—'],
    // radius_km 是 SearchConfig 的派生属性、不随 to_dict 落盘，这里按同口径换算
    ['区域中心', `${cfg.center_lat}°N, ${cfg.center_lon}°E · 半边长 ${cfg.half_span_deg}°（≈${Math.round(cfg.half_span_deg * 111)} km）`],
    ['要素 × 层次', cfgVars(cfg)],
    ['距离权重', cfgWeight(cfg)],
    ['特征处理', cfgLabel(m.feature_processes, cfg.feature_process)],
    ['相似度', cfgLabel(m.similarities, cfg.similarity) +
      (cfg.similarity === 'custom' && cfg.similarity_formula ? `：${cfg.similarity_formula}` : '')],
    ['排除窗', `目标日前后 ${cfg.exclude_days} 天`],
    ['交集组', `${(cfg.inter_group || []).join(', ') || '—'} · TopN ${cfg.inter_topn}`],
    ['直取组', `${(cfg.direct_group || []).join(', ') || '—'} · TopN ${cfg.direct_topn}`],
    ['组合方式', cfgSingleTarget(cfg) ? '单要素（不做交集，直接 TopN 完整流程）'
      : (cfg.partial_intersection ? '部分交集 + 直取' : '严格交集 + 直取')],
    ['部分交集', cfg.partial_intersection ? `开启（命中 ≥ ${cfg.partial_min_hits}）` : '关闭'],
    ['直取要素场单独出图', cfg.plot_direct_fields ? '是' : '否'],
    ['GMCP 累计降水检索', cfg.precip_enabled
      ? `${(cfg.precip_windows || []).length} 个窗口 · 每窗 TopN ${cfg.precip_topn} · 阈值 ${(cfg.precip_thresholds || []).map(x => '≥' + x + 'mm').join(' / ')}`
      : '未启用'],
    ['降水窗口', cfgRain(cfg)],
    ['降水阈值', (cfg.rain_thresholds || []).map(x => '≥' + x + ' mm').join(' / ') || '—'],
  ];
  const cells = rows.map(([k, v]) =>
    `<div class="cfg-item"><span>${esc(k)}</span><b>${esc(v)}</b></div>`).join('');
  return `<div class="panel"><div class="panel-head"><h3>检索配置（本次任务实际使用）</h3>
    <p>以下参数随结果一并落盘为 <code>result.json</code> 的 <code>config</code> 字段，
    与检索当时完全一致，可用于复核结论或复现本次检索；历史任务的配置同样由此回放。</p></div>
    <div class="cfg-grid">${cells}</div></div>`;
}

/* ── GMCP 累计降水目标检索产品（第一优先，V2.2.0 可后处理重算） ── */
function fmtPctl(v) {
  const n = Number(v);
  return Number.isFinite(n) ? (Math.abs(n - Math.round(n)) < 1e-9 ? String(Math.round(n)) : n.toFixed(1)) : '—';
}

function renderPrecipPanel(r) {
  const P = r.precip || {};
  if (!P.enabled) return '';
  const skipped = (P.skipped || []).map(s => s.reason).filter(Boolean);
  if (!P.available) {
    return `<div class="panel"><div class="panel-head"><h3>GMCP 累计降水目标检索</h3>
      <p>未取得可用的累计降水检索结果。</p></div>
      <div class="hint" style="color:var(--warn)">${esc(P.reason || '目标日或历史年份缺少累计降水产品')}
      ${skipped.length ? '<br>跳过：' + skipped.map(esc).join('；') : ''}
      <br>请先在「数据预处理」页生成产品，并点击「登记预处理产物」。</div></div>`;
  }
  const wins = Object.entries(P.windows || {});
  const maxTopn = Math.max(1, Math.min(...wins.map(([, w]) =>
    Number(w.max_topn || w.n_available || w.topn || 1))));
  const cachedTopn = Math.max(0, Math.min(...wins.map(([, w]) =>
    Number(w.cached_topn || 0))));
  const curTopn = Number((P.recompute && P.recompute.topn) || wins[0][1].topn || 1);
  const pctl = Number(P.percentile ?? (P.recompute && P.recompute.percentile) ?? 90);
  const pc = fmtPctl(pctl);
  const rc = P.recompute;
  const rcNote = rc
    ? `已按 TopN ${num(rc.topn)} · P${fmtPctl(rc.percentile)} 重算（${esc(String(rc.at || '').replace('T', ' '))}，未重新检索）`
    : `检索结果已就绪：改 TopN 或百分位后直接重算产品图，不重新走检索流程`;
  const bar = `<div class="recompute-bar">
      <div class="rc-field"><label>产品 TopN（每窗相似日数）</label>
        <input id="precipTopnInput" type="number" min="1" max="${maxTopn}" step="1" value="${curTopn}">
        <span class="rc-note">可用排名 1–${maxTopn}${cachedTopn && cachedTopn < maxTopn
          ? `（前 ${cachedTopn} 名直接取缓存，其余按排名回读产品）` : ''}</span></div>
      <div class="rc-field"><label>自定义百分位 P（%）</label>
        <div class="rc-range">
          <input id="precipPctlRange" type="range" min="1" max="99" step="1" value="${Math.round(pctl)}">
          <input id="precipPctlInput" type="number" min="1" max="99" step="1" value="${pctl}">
        </div><span class="rc-note">拖动滑块即时重算分位数产品</span></div>
      <button class="btn primary" id="btnPrecipRecompute">重算产品图</button>
      <span class="rc-status" id="precipRecomputeMsg">${rcNote}</span>
    </div>`;
  const tabs = wins.map(([k, w], i) =>
    `<button class="tab ${i ? '' : 'active'}" data-tab="q${i}">${esc(w.label || k)}（${num(w.n_valid)}）</button>`).join('');
  const panes = wins.map(([k, w], i) => {
    const p = w.products || {};
    const q = p.quantiles || {};
    const thRows = Object.entries(p.stats || {}).map(([th, v]) =>
      `<tr><td class="mono">≥ ${esc(th)} mm</td><td class="num">${pct(v.prob)}</td>
       <td class="num">${num(v.count)} / ${num(v.n)}</td></tr>`).join('');
    const qHeads = ['10', '25', '50', '75', '90'].map(x => `<th class="num">P${x}</th>`).join('') +
      `<th class="num" style="color:var(--amber)">P${pc}</th>`;
    const qVals = ['10', '25', '50', '75', '90'].map(x =>
      `<td class="num">${q[x] == null ? '—' : q[x]}</td>`).join('') +
      `<td class="num" style="color:var(--amber)">${p.pctl_mm == null ? '—' : p.pctl_mm}</td>`;
    const rows = (w.ranking || []).slice(0, 30).map(x =>
      `<tr><td class="num">${x.rank}</td><td class="date">${esc(x.date)}</td>
       <td class="num">${Number(x.si).toFixed(4)}</td><td class="num">${x.mm}</td></tr>`).join('');
    return `<div class="tabpane" data-pane="q${i}" ${i ? 'hidden' : ''}>
      <div class="hint">目标产品 <code>${esc(w.file || '')}</code> ·
        北京时间 ${esc(w.start_lst || '')} → ${esc(w.end_lst || '')} ·
        候选 ${num(w.n_candidates)} · 入产品 ${num(w.n_valid)} · TopN ${num(w.topn)}
        ${w.n_available != null ? ` · 可用排名 ${num(w.n_available)}` : ''}
        ${w.spatial_ready ? ' · 空间产品已生成' : ''}</div>
      <div class="kpis" style="margin-bottom:14px">
        <div class="kpi accent"><div class="k-label">目标日实况累计降水</div>
          <div class="k-value">${p.target_mm == null ? '—' : p.target_mm}<small>mm</small></div>
          <div class="k-note">目标 ${esc(r.target_date)} · 同窗口对比基准</div></div>
        <div class="kpi"><div class="k-label">相似日平均</div>
          <div class="k-value">${p.mean_mm}<small>mm</small></div><div class="k-note">n = ${num(p.n_valid)}</div></div>
        <div class="kpi"><div class="k-label">相似日中位数</div>
          <div class="k-value">${p.median_mm}<small>mm</small></div><div class="k-note">P50 分位</div></div>
        <div class="kpi warn"><div class="k-label">自定义 P${pc} 分位</div>
          <div class="k-value">${p.pctl_mm == null ? '—' : p.pctl_mm}<small>mm</small></div>
          <div class="k-note">可调百分位产品</div></div>
      </div>
      <div class="grid2">
        <div><div class="section-title">阈值概率（统计点）</div>
          <div class="table-wrap"><table><thead><tr><th>阈值</th><th class="num">概率</th>
          <th class="num">达到 / 有效</th></tr></thead><tbody>${thRows || '<tr><td colspan="3">无统计</td></tr>'}</tbody></table></div>
          <div class="section-title" style="margin-top:14px">分位数（统计点，mm）</div>
          <div class="table-wrap"><table><thead><tr>${qHeads}</tr></thead>
          <tbody><tr>${qVals}</tr></tbody></table></div>
        </div>
        <div><div class="section-title">相似历史日（按相似度名次）</div>
          <div class="table-wrap" style="max-height:320px"><table><thead><tr><th class="num">#</th>
          <th>日期</th><th class="num">相似度指数 SI</th><th class="num">点累计 mm</th></tr></thead>
          <tbody>${rows || '<tr><td colspan="4">无相似日</td></tr>'}</tbody></table></div></div>
      </div>
      <div class="hint">产品图：首格为目标日实况，其后为相似日平均、中位数与阈值概率；
        另有独立的分位数产品图（P10–P90 + 自定义 P${pc}）。重算只更新产品与图，不改检索排名。</div>
    </div>`;
  }).join('');
  return `<div class="panel"><div class="panel-head"><h3>GMCP 累计降水目标检索产品</h3>
    <p>每个窗口独立检索 TopN 相似历史日，再用这些日期的同一窗口累计降水场统计平均、中位数、
      固定分位数与自定义百分位产品；目标日实况场并排显示便于比对。
      24 小时两套窗口为第一优先。</p></div>
    ${bar}
    <div class="hint">并行计算：窗口排名 ×${num(P.parallel_workers || 1)} ·
      单窗口候选分块 ×${num(P.chunk_workers || 1)} ·
      产品读取进程 ×${num(P.io_workers || 1)}（GMCP 累计降水单要素检索同样加速）</div>
    <div class="tabs">${tabs}</div>${panes}
    ${skipped.length ? `<div class="hint" style="color:var(--warn)">跳过：${skipped.map(esc).join('；')}</div>` : ''}
  </div>`;
}

function renderResult(r) {
  const s = r.samples;
  const figs = Object.entries(r.figures || {})
    .sort((a, b) => figRank(a[0]) - figRank(b[0]) || String(a[0]).localeCompare(String(b[0])));
  state.figs = figs;
  const boxes = [];
  const g = r.grid, gg = r.gmcp_grid;

  boxes.push(`<div class="panel"><div class="panel-head">
      <h3>结果概要 <em class="pill-num">${esc(r.task_id)}</em></h3>
      <p>目标日期 <b style="color:var(--amber)">${esc(r.target_date)}</b> · 配置 ${esc(r.config.name)}
      · 指纹 <code>${esc(r.config.fingerprint)}</code></p></div>
    <div class="res-head">
      <div class="stat-pill"><span>候选样本</span><b>${num(s.n_candidates)}</b></div>
      <div class="stat-pill"><span>实际参与</span><b>${num(s.n_used)}</b></div>
      <div class="stat-pill ${s.n_read_errors ? '' : 'ok'}"><span>读取失败</span><b>${num(s.n_read_errors)}</b></div>
      <div class="stat-pill"><span>年份跨度</span><b>${s.years[0]}–${s.years[1]}</b></div>
      <div class="stat-pill"><span>总耗时</span><b>${r.elapsed_s}</b><span>s</span></div>
      <div class="stat-pill"><span>ERA5 网格</span><b>${num(g.n_lat)}×${num(g.n_lon)}</b><span>点</span></div>
      <div class="stat-pill"><span>区域最大距离</span><b>${g.max_dist_km}</b><span>km</span></div>
      <div class="stat-pill"><span>GMCP 网格</span><b>${num(gg.n_lat)}×${num(gg.n_lon)}</b><span>点</span></div>
      <div class="stat-pill"><span>空间概率场</span><b>${Object.values(r.products.by_time).some(p => p.spatial_ready) ? '已生成' : '未生成'}</b></div>
    </div>
    <div class="hint">ERA5 区域 ${esc(String(g.lat_range[0]))}–${esc(String(g.lat_range[1]))}°N ·
    ${esc(String(g.lon_range[0]))}–${esc(String(g.lon_range[1]))}°E　|　
    分阶段耗时：${Object.entries(r.timings || {}).map(([k, v]) => `${esc(k)} <b style="color:var(--amber)">${v}s</b>`).join(' · ') || '—'}
    ${s.n_read_errors ? `<br>最早 ${s.read_errors.length} 条读取错误：<span class="mono">${s.read_errors.slice(0, 3).map(esc).join(' / ')}</span>` : ''}</div>
  </div>`);

  // 检索配置回放（紧随概要，查结果时先看到当时的参数）
  boxes.push(renderConfigPanel(r.config || {}, r.target_date));

  // GMCP 累计降水目标检索产品（第一优先）
  const precipPanel = renderPrecipPanel(r);
  if (precipPanel) boxes.push(precipPanel);

  // 组合策略 + 诊断（仅 ERA5 路径有）
  const cards = Object.entries(r.combine || {}).map(([ts, c]) => {
    const dg = c.diagnose || {};
    const curve = (dg.curve || []);
    const maxN = Math.max(1, ...curve.map(p => p.n_inter));
    const bars = curve.map(p => `<div class="cbar" style="height:${Math.max(3, 100 * p.n_inter / maxN)}%"
        title="TopN=${p.topn} → 交集 ${p.n_inter} 个"><span>${p.n_inter}</span><i>${p.topn}</i></div>`).join('');
    const emptyHint = !c.n_final
      ? `<div class="hint" style="color:var(--warn)">本次锚定未取得任何相似时刻：交集组各要素在候选池（${num(dg.n_candidates)}）内没有共同日期。可改用「部分交集」、进一步放大 TopN，或扩大年份/月份范围。</div>`
      : '';
    return `<div class="combine-card">
      <h4>锚定 ${esc(ts)} ${ts === '24h' ? '' : 'UTC'}</h4>
      <div class="combine-nums">
        <div><b style="color:var(--amber)">${num(c.n_inter)}</b><span>交集</span></div>
        <div><b style="color:#8FB9DA">${num(c.n_direct)}</b><span>直取</span></div>
        <div><b>${num(c.n_final)}</b><span>合并入组</span></div>
      </div>
      <div class="diag">
        候选池 <b>${num(dg.n_candidates)}</b> · 交集 TopN <b>${num(r.config.inter_topn)}</b>${
          c.inter_topn_auto ? `<b style="color:var(--amber)">（严格交集为空，已自动放宽至 ${num(c.inter_topn_used)}）</b>` : ''}
        · 最小可行 TopN <b>${dg.min_feasible_topn == null ? '—' : num(dg.min_feasible_topn)}</b>
        <div class="curve-wrap"><div class="curve">${bars || '<span style="color:var(--ink-mute);font-size:11px">无可行交集</span>'}</div></div>
        <span style="font-size:10.5px">横轴为 TopN 取值，柱高与数字为对应的交集个数</span>
      </div>${emptyHint}</div>`;
  }).join('');
  if (cards) boxes.push(`<div class="panel"><div class="panel-head"><h3>组合策略与可行性诊断</h3>
    <p>交集 = 各交集组要素 TopN 的交集；直取 = 各直取组要素 TopN 的并集；合并 = 交集 ∪ 直取。</p></div>
    <div class="combine-grid">${cards || '<div class="empty">无组合结果。</div>'}</div></div>`);

  // 相似时刻明细 + 排名（仅 ERA5 路径有）
  const keys = [...(r.config.inter_group || []), ...(r.config.direct_group || [])];
  if (keys.length) {
  const detailTabs = Object.entries(r.combine || {}).map(([ts, c], i) => {
    const inter = r.config.inter_group || [];
    const head = `<tr><th>#</th><th>日期</th><th>来源标注</th>` +
      inter.map(k => `<th class="num">${esc(k)} 排名</th>`).join('') + '</tr>';
    const body = (c.final || []).map((d, j) =>
      `<tr><td class="num">${j + 1}</td><td class="date">${esc(d)}</td>
        <td>${esc(c.source[d] || '')}</td>` +
      inter.map(k => {
        const rank = ((r.ranking[k] || {})[ts] || []).find(x => x.date === d);
        return `<td class="num">${rank ? '#' + rank.rank : '—'}</td>`;
      }).join('') + '</tr>').join('');
    return `<div class="tabpane" data-pane="d${i}" ${i ? 'hidden' : ''}>
      ${c.final && c.final.length
        ? `<div class="table-wrap"><table><thead>${head}</thead><tbody>${body}</tbody></table></div>`
        : '<div class="empty">该锚定无入组相似时刻。</div>'}</div>`;
  }).join('');
  const detailTabBtns = Object.keys(r.combine || {}).map((ts, i) =>
    `<button class="tab ${i ? '' : 'active'}" data-tab="d${i}">锚定 ${esc(ts)}（${num(r.combine[ts].n_final)}）</button>`).join('');
  boxes.push(`<div class="panel"><div class="panel-head"><h3>相似时刻明细</h3>
    <p>排名列为该日期在对应要素各自排名中的位次；来源标注说明它因何入组。</p></div>
    <div class="tabs">${detailTabBtns}</div>${detailTabs}</div>`);

  // 各要素排名
  const rankTabs = keys.map((k, i) => {
    const rows = Object.entries(r.ranking[k] || {}).map(([ts, list]) => {
      const top = list.slice(0, 25).map(x =>
        `<tr><td class="num">${x.rank}</td><td class="date">${esc(x.date)}</td><td class="num">${x.si.toFixed(5)}</td></tr>`).join('');
      return `<div class="section-title">${esc(ts)}${ts === '24h' ? '' : ' UTC'} · 取前 ${Math.min(25, list.length)} / ${num(list.length)} 条</div>
        <div class="table-wrap" style="max-height:300px"><table><thead><tr><th class="num">排名</th><th>日期</th>
        <th class="num">相似度指数 SI</th></tr></thead><tbody>${top}</tbody></table></div>`;
    }).join('');
    return `<div class="tabpane" data-pane="r${i}" ${i ? 'hidden' : ''}>${rows || '<div class="empty">无排名数据。</div>'}</div>`;
  }).join('');
  const rankBtns = keys.map((k, i) =>
    `<button class="tab ${i ? '' : 'active'}" data-tab="r${i}">${esc(k)}</button>`).join('');
  boxes.push(`<div class="panel"><div class="panel-head"><h3>各要素相似度排名</h3>
    <p>SI 越小越相似；界面最多展示前 ${num(r.rank_view_limit)} 条，后端按 TopN 全量计算。</p></div>
    <div class="tabs">${rankBtns}</div>${rankTabs}</div>`);
  }

  // 降水概率（ERA5 路径的 GMCP 小时窗口统计）
  const prod = (r.products && r.products.by_time) || {};
  const rainTabs = Object.entries(prod).map(([ts, p], i) => {
    const thRows = Object.entries(p.stats || {}).map(([th, v]) =>
      `<tr><td class="mono">≥ ${esc(th)} mm</td><td class="num">${pct(v.prob)}</td>
       <td class="num">${num(v.count)} / ${num(v.n)}</td></tr>`).join('');
    const smp = (p.samples || []).map(s =>
      `<tr><td class="date">${esc(s.date)}</td><td class="num">${s.mm}</td></tr>`).join('');
    return `<div class="tabpane" data-pane="p${i}" ${i ? 'hidden' : ''}>
      <div class="kpis" style="margin-bottom:14px">
        <div class="kpi"><div class="k-label">有效样本</div><div class="k-value">${num(p.n_valid)}<small>个</small></div>
          <div class="k-note">缺失 ${num(p.n_missing)} 小时文件</div></div>
        <div class="kpi accent"><div class="k-label">窗口累计降水均值</div>
          <div class="k-value">${p.mean_mm}<small>mm</small></div><div class="k-note">统计点 ${r.products.point[0]}, ${r.products.point[1]}</div></div>
        <div class="kpi warn"><div class="k-label">最大累计降水</div>
          <div class="k-value">${p.max_mm}<small>mm</small></div><div class="k-note">样本集内极值</div></div>
      </div>
      <div class="grid2">
        <div><div class="section-title">阈值降水概率</div>
          <div class="table-wrap"><table><thead><tr><th>阈值</th><th class="num">概率</th>
          <th class="num">达到 / 有效</th></tr></thead><tbody>${thRows || '<tr><td colspan="3">无统计</td></tr>'}</tbody></table></div></div>
        <div><div class="section-title">逐样本累计降水（降序）</div>
          <div class="table-wrap" style="max-height:280px"><table><thead><tr><th>相似日期</th>
          <th class="num">累计 mm</th></tr></thead><tbody>${smp || '<tr><td colspan="2">无样本</td></tr>'}</tbody></table></div></div>
      </div>
      ${p.missing && p.missing.length ? `<div class="hint">降水缺失日期（前 ${p.missing.length} 个）：<span class="mono">${p.missing.map(esc).join(', ')}</span></div>` : ''}
    </div>`;
  }).join('');
  const rainBtns = Object.keys(prod).map((ts, i) =>
    `<button class="tab ${i ? '' : 'active'}" data-tab="p${i}">${ts === '24h' ? '24 小时窗口' : '锚定 ' + esc(ts) + ' UTC'}（${num(prod[ts].n_valid)}）</button>`).join('');
  if (rainBtns) boxes.push(`<div class="panel"><div class="panel-head"><h3>降水概率产品</h3>
    <p>对入组相似时刻在 GMCP 逐小时降水场中按窗口累加，得到统计点概率与空间概率场。</p></div>
    <div class="tabs">${rainBtns}</div>${rainTabs || '<div class="empty">无降水产品（入组相似时刻为空）。</div>'}</div>`);

  // 图表
  boxes.push(`<div class="panel"><div class="panel-head"><h3>图表</h3>
    <p>点击可放大，放大后可用 ← / → 或左右箭头切换、显示序号。场对比图为「目标日 + TopK 相似日」的要素场并置；概率图为各锚定时次的空间降水概率场。</p></div>
    ${figs.length ? `<div class="gallery">${figs.map(([name, url], i) =>
      `<figure class="fig" data-i="${i}"><img loading="lazy" src="${esc(url)}" alt="${esc(name)}">
       <figcaption><span>${esc(figLabel(name))}</span><span>${esc(url.split('/').pop().split('?')[0])}</span></figcaption></figure>`).join('')}</div>`
      : '<div class="empty">本次未生成图表。</div>'}
    ${Object.keys(r.figure_errors || {}).length ? `<div class="hint" style="color:var(--bad)">出图警告：${
      Object.entries(r.figure_errors).map(([k, v]) => esc(k) + ' → ' + esc(v)).join('；')}</div>` : ''}
  </div>`);

  const body = $('#resultBody');
  body.innerHTML = boxes.join('');
  bindTabs(body);
  bindPrecipControls();
  $$('.fig', body).forEach(f => f.addEventListener('click', () => showFig(Number(f.dataset.i))));
}

/* ── V2.2.0：结果页后处理重算（TopN / 自定义百分位），不重新检索 ── */
async function recomputePrecipProducts() {
  if (!state.result || state.busy) return;
  const topnEl = $('#precipTopnInput');
  if (!topnEl) return;
  const task = state.result.task_id;
  const topn = Math.max(1, Number(topnEl.value || 1));
  const pct = Math.min(99, Math.max(1, Number($('#precipPctlInput')?.value || 90)));
  const msg = $('#precipRecomputeMsg');
  if (msg) {
    msg.textContent = `重算中… TopN=${topn} · P${fmtPctl(pct)}`;
    msg.classList.add('busy');
  }
  const y = window.scrollY;
  state.busy = true;
  try {
    const r = await post('/api/search/recompute', {
      task_id: task, precip_topn: topn, percentile: pct,
    });
    state.result = r;
    renderResult(r);
    window.scrollTo(0, y);
    const used = Number((r.precip && r.precip.recompute && r.precip.recompute.topn) || topn);
    toast(`已按 TopN=${used}、P${fmtPctl(pct)} 重算产品图（未重新检索）`
      + (used < topn ? `；可用排名仅 ${used} 条，已按实际数量重算` : ''), 'ok');
  } catch (e) {
    toast('重算失败：' + e.message, 'bad');
    if (msg) {
      msg.textContent = '重算失败：' + e.message;
      msg.classList.remove('busy');
    }
  } finally {
    state.busy = false;
  }
}

function bindPrecipControls() {
  const topnEl = $('#precipTopnInput');
  if (!topnEl) return;
  const range = $('#precipPctlRange');
  const pnum = $('#precipPctlInput');
  let timer = null;
  const schedule = () => {
    clearTimeout(timer);
    timer = setTimeout(recomputePrecipProducts, 500);
  };
  const sync = (src, dst) => {
    const v = Math.min(99, Math.max(1, Number(src.value) || 1));
    src.value = v;
    dst.value = v;
    schedule();
  };
  range.addEventListener('input', () => sync(range, pnum));
  pnum.addEventListener('input', () => sync(pnum, range));
  topnEl.addEventListener('change', schedule);
  $('#btnPrecipRecompute').addEventListener('click', () => {
    clearTimeout(timer);
    recomputePrecipProducts();
  });
}

function bindTabs(root) {
  $$('.tab', root).forEach(t => t.addEventListener('click', () => {
    const panel = t.closest('.panel');
    $$('.tab', panel).forEach(x => x.classList.toggle('active', x === t));
    $$('.tabpane', panel).forEach(p => { p.hidden = p.dataset.pane !== t.dataset.tab; });
  }));
}

/* ── 灯箱：多图切换（← / → / 左右箭头 / 序号 i/n） ─────────────── */
function showFig(i) {
  const n = state.figs.length;
  if (!n) return;
  state.figIdx = ((i % n) + n) % n;
  const [name, url] = state.figs[state.figIdx];
  $('#modalImg').src = url;
  $('#modalImg').alt = name;
  $('#modalCap').textContent = `${name} · ${state.figIdx + 1}/${n}`;
  $('#modal').hidden = false;
}
$('#modalPrev').addEventListener('click', e => { e.stopPropagation(); showFig(state.figIdx - 1); });
$('#modalNext').addEventListener('click', e => { e.stopPropagation(); showFig(state.figIdx + 1); });
$('#modalClose').addEventListener('click', () => { $('#modal').hidden = true; });
$('#modal').addEventListener('click', e => { if (e.target.id === 'modal') $('#modal').hidden = true; });
document.addEventListener('keydown', e => {
  if ($('#modal').hidden) return;
  if (e.key === 'Escape') $('#modal').hidden = true;
  else if (e.key === 'ArrowLeft') showFig(state.figIdx - 1);
  else if (e.key === 'ArrowRight') showFig(state.figIdx + 1);
});

$('#btnExport').addEventListener('click', () => {
  if (!state.result) { toast('请先载入结果', 'bad'); return; }
  const blob = new Blob([JSON.stringify(state.result, null, 2)], { type: 'application/json' });
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = `histsim_${state.result.task_id}.json`;
  a.click();
  URL.revokeObjectURL(a.href);
  toast('已导出结果 JSON', 'ok');
});

$('#btnExportZip').addEventListener('click', async () => {
  if (!state.result) { toast('请先载入结果', 'bad'); return; }
  try {
    const r = await post('/api/search/export', { task_id: state.result.task_id });
    const a = document.createElement('a');
    a.href = r.url;
    a.download = r.filename || 'histsim_export.zip';
    document.body.appendChild(a);
    a.click();
    a.remove();
    toast(`已导出结果包：${r.n_figures} 张图 + result.json + 排名 CSV`, 'ok');
  } catch (e) {
    toast('导出失败：' + e.message, 'bad');
  }
});

/* ══════════════════════════ 6 · 操作记录 ══════════════════════════ */
async function loadOps() {
  try {
    const [t, o] = await Promise.all([api('/api/tasks?limit=200'), api('/api/operations?limit=300')]);
    const th = (t.history || []);
    $('#opsTasks').innerHTML = th.length ? `<div class="table-wrap"><table><thead><tr>
      <th>创建时间</th><th>目标日期</th><th>配置</th><th>指纹</th><th>状态</th>
      <th class="num">耗时</th><th>摘要</th></tr></thead><tbody>${th.map(x => `<tr>
        <td class="mono">${esc((x.created_at || '').replace('T', ' '))}</td>
        <td class="date">${esc(x.target_date)}</td>
        <td>${esc(x.config_name)}</td>
        <td class="mono">${esc(x.config_fingerprint)}</td>
        <td><span class="badge ${x.status === '成功' ? 'ok' : x.status === '失败' ? 'bad' : 'warn'}">${esc(x.status)}</span></td>
        <td class="num">${x.elapsed_s}s</td>
        <td title="${esc(x.summary)}" style="max-width:280px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${esc(x.summary)}</td>
        </tr>`).join('')}</tbody></table></div>` : '<div class="empty">还没有任务记录。</div>';

    const ops = o.items || [];
    $('#opsLog').innerHTML = ops.length ? `<div class="table-wrap"><table><thead><tr>
      <th>时间</th><th>操作</th><th>对象</th><th>详情</th><th>结果</th></tr></thead><tbody>${ops.map(x => `<tr>
        <td class="mono">${esc((x.ts || '').replace('T', ' '))}</td>
        <td>${esc(x.op)}</td><td class="mono">${esc(x.target)}</td>
        <td title="${esc(x.detail)}" style="max-width:420px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${esc(x.detail)}</td>
        <td><span class="badge ${x.result === '成功' ? 'ok' : x.result === '失败' ? 'bad' : ''}">${esc(x.result)}</span></td>
      </tr>`).join('')}</tbody></table></div>` : '<div class="empty">还没有操作日志。</div>';
  } catch (e) { toast('记录加载失败：' + e.message, 'bad'); }
}

/* ── 启动 ─────────────────────────────────────────────────────────── */
(async function init() {
  try {
    state.meta = await api('/api/meta');
    await initConfig();
  } catch (e) {
    toast('初始化失败，请确认后端服务在运行：' + e.message, 'bad');
    return;
  }
  initRegionPicker();
  await loadDashboard();
  loadSources();
  // 恢复未完成的预处理任务（进度在服务端，页面重开后继续跟踪）
  try {
    const pj = localStorage.getItem(PREP_KEY);
    if (pj) {
      const p = await api('/api/preprocess/progress?job_id=' + encodeURIComponent(pj));
      if (p.state === 'running') state.prepJob = pj;
      else localStorage.removeItem(PREP_KEY);
    }
  } catch (e) { try { localStorage.removeItem(PREP_KEY); } catch (err) { /* 忽略 */ } }
  go('dashboard');

  // F5 / 重开浏览器后自动续跟踪运行中的任务
  const h = await api('/api/health');
  let saved = null;
  try { saved = localStorage.getItem(TASK_KEY); } catch (e) { saved = null; }
  if (saved) {
    try {
      const p = await api('/api/search/progress?task_id=' + encodeURIComponent(saved));
      if (p.state === 'running') {
        saveTask(saved);
        state.progress = p;
        startPolling();
        toast(`已恢复跟踪运行中的任务 <span class="mono">${esc(saved)}</span>（可到「执行进程」页查看）`, 'ok');
      } else { clearTask(); }
    } catch (e) { clearTask(); }   // 任务不存在（服务重启后内存记录丢失）→ 放弃续跟踪
  }
  if (h.running_tasks && !state.task) {
    toast(`检测到 ${h.running_tasks} 个运行中的任务，可到「执行进程」页查看`);
  }
})();