/* 交班模型校验班报：严重延误概率 vs 主管确认实绩的 MSE 对照 */
const token = () => localStorage.getItem('cdt_token');
const endpoint = '/api/shift-report';

function element(tag, text) {
  const node = document.createElement(tag);
  if (text !== undefined) node.textContent = text;
  return node;
}

async function request(path = '', body, method) {
  const response = await fetch(endpoint + path, {
    method: method || (body ? 'POST' : 'GET'),
    headers: {'Authorization': `Bearer ${token() || ''}`, 'Content-Type': 'application/json'},
    body: body ? JSON.stringify(body) : undefined,
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : `请求失败 (${response.status})`);
  return data;
}

const launcher = element('button', '交班班报');
launcher.type = 'button';
launcher.id = 'shift-report-open';
launcher.hidden = true;
const dialog = element('dialog');
dialog.id = 'shift-report';
dialog.setAttribute('aria-labelledby', 'shift-report-title');
const header = element('header');
const titleEl = element('h2', '模型交班班报 · 严重延误校验');
titleEl.id = 'shift-report-title';
const closeBtn = element('button', '关闭');
closeBtn.addEventListener('click', () => dialog.close());
const refreshBtn = element('button', '刷新');
header.append(titleEl, refreshBtn, closeBtn);
const feedback = element('p');
feedback.setAttribute('role', 'status');
const summary = element('section');
summary.className = 'shift-summary';
const groupsEl = element('section');
groupsEl.className = 'shift-groups';
dialog.append(header, feedback, summary, groupsEl);
document.body.append(launcher, dialog);

function sessionChanged() {
  let signedIn = false;
  try { signedIn = Boolean(token()); } catch { /* ignore */ }
  launcher.hidden = !signedIn;
  if (!signedIn && dialog.open) dialog.close();
}
sessionChanged();
window.addEventListener('storage', sessionChanged);
setInterval(sessionChanged, 600);

const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#039;'}[c]));
const pct = v => v === null || v === undefined ? '—' : (v * 100).toFixed(1) + '%';
const mseText = v => v === null || v === undefined ? '待实绩' : Number(v).toFixed(4);
let role = 'viewer';
let busy = false;

function actualForm(caseId, defaultValue) {
  const form = element('form');
  const select = element('select');
  select.add(new Option('实绩：严重延误', '1'));
  select.add(new Option('实绩：非严重', '0'));
  if (defaultValue !== undefined) select.value = String(defaultValue);
  const note = element('input');
  note.placeholder = '主管确认依据（≥3字）';
  note.required = true;
  note.minLength = 3;
  note.maxLength = 500;
  const btn = element('button', defaultValue === undefined ? '登记实绩' : '更正实绩');
  btn.type = 'submit';
  form.append(select, note, btn);
  form.addEventListener('submit', async event => {
    event.preventDefault();
    if (busy) return;
    busy = true; btn.disabled = true; feedback.textContent = '正在保存实绩…';
    try {
      await request(`/actuals/${caseId}`, {severe_actual: Number(select.value), note: note.value});
      feedback.textContent = '实绩已入账，班报已重新计分';
      await load();
    } catch (error) { feedback.textContent = error.message; btn.disabled = false; }
    finally { busy = false; }
  });
  return form;
}

function caseTable(detail, canManage) {
  const wrap = element('div');
  wrap.className = 'shift-table-wrap';
  const table = element('table');
  const head = table.createTHead().insertRow();
  ['案件', '派工时间(北京)', '严重概率', '主管实绩', '平方误差', '实绩版本/登记', '操作'].forEach(t => head.append(element('th', t)));
  const body = table.createTBody();
  const addRow = (row, pending, noProb) => {
    const tr = body.insertRow();
    tr.insertCell().textContent = row.case_no;
    tr.insertCell().textContent = new Date(row.dispatched_at).toLocaleString('zh-CN', {hour12: false});
    tr.insertCell().textContent = pct(row.severe_probability);
    tr.insertCell().textContent = noProb ? '缺概率快照' : pending ? '待报实绩' : (row.severe_actual ? '严重延误' : '非严重');
    tr.insertCell().textContent = pending || noProb ? '不计入' : Number(row.squared_error).toFixed(4);
    tr.insertCell().textContent = pending || noProb ? '—' : `rev.${row.actual_revision} · ${row.recorded_by} · ${new Date(row.recorded_at).toLocaleString('zh-CN', {hour12: false})}`;
    const op = tr.insertCell();
    if (canManage && !noProb) op.append(actualForm(row.case_id, pending ? undefined : row.severe_actual));
  };
  detail.scored.forEach(row => addRow(row, false, false));
  detail.pending.forEach(row => addRow(row, true, false));
  detail.without_probability.forEach(row => addRow(row, false, true));
  wrap.append(table);
  return wrap;
}

function signoffPanel(group, detail) {
  const box = element('div');
  box.className = 'shift-signoff';
  if (group.signoff) {
    const snap = group.signoff.snapshot;
    box.append(
      element('h4', '已签发数字（原样保留）'),
      element('p', `签发 MSE：${mseText(snap.mse)}　计分案件 ${snap.scored_count}　待报 ${snap.pending_count}`),
      element('p', `签发人：${group.signoff.signed_by}　${new Date(group.signoff.signed_at).toLocaleString('zh-CN', {hour12: false})}`),
      element('p', `说明：${group.signoff.note}`),
    );
    const diff = element('p');
    diff.className = snap.mse === group.mse ? 'same' : 'diff';
    diff.textContent = snap.mse === group.mse
      ? '当前结果与已签发数字一致'
      : `签发后有更正：当前 MSE ${mseText(group.mse)} ≠ 已签发 ${mseText(snap.mse)}`;
    box.append(diff);
  } else if (role === 'supervisor') {
    const form = element('form');
    const note = element('input');
    note.placeholder = '班报签发说明（≥3字）';
    note.required = true; note.minLength = 3; note.maxLength = 500;
    const btn = element('button', '签发本班报');
    btn.type = 'submit';
    form.append(note, btn);
    form.addEventListener('submit', async event => {
      event.preventDefault();
      if (busy) return;
      busy = true; btn.disabled = true; feedback.textContent = '正在签发…';
      try {
        await request('/signoffs', {terminal: group.terminal, shift_key: group.shift_key, note: note.value});
        feedback.textContent = '班报已签发，数字已冻结';
        await load();
      } catch (error) { feedback.textContent = error.message; btn.disabled = false; }
      finally { busy = false; }
    });
    box.append(element('h4', '尚未签发'), form);
  } else {
    box.append(element('h4', '尚未签发'), element('p', '等待主管签发'));
  }
  return box;
}

function render(report, me) {
  role = me.role;
  const canManage = role === 'supervisor';
  summary.replaceChildren();
  const rule = element('p');
  rule.className = 'shift-rule';
  rule.textContent = `口径：严重延误=模型等级 ${report.severe_level}，取首条处置单派工时的概率；${report.shift_rules}（${report.timezone}）。未报实绩案件单列，不计入 MSE。`;
  summary.append(rule);
  groupsEl.replaceChildren();
  report.groups.forEach(group => {
    const detail = report.details.find(d => d.terminal === group.terminal && d.shift_key === group.shift_key);
    const card = element('article');
    card.className = 'shift-card';
    const head = element('div');
    head.className = 'shift-card-head';
    head.append(element('h3', `${group.terminal} · ${group.shift_label}`));
    const badges = element('div');
    badges.className = 'shift-badges';
    badges.append(
      element('span', `计分 ${group.scored_count}`),
      element('span', `待报实绩 ${group.pending_count}`),
      element('span', `缺概率 ${group.without_probability_count}`),
    );
    head.append(badges);
    const mseLine = element('div');
    mseLine.className = 'shift-mse-line';
    const cur = element('div');
    cur.append(element('label', '当前结果 MSE'), element('strong', mseText(group.mse)));
    mseLine.append(cur);
    card.append(head, mseLine, signoffPanel(group, detail));
    const toggle = element('button', '展开案件明细');
    toggle.type = 'button';
    const tableWrap = caseTable(detail, canManage);
    tableWrap.hidden = true;
    toggle.addEventListener('click', () => {
      tableWrap.hidden = !tableWrap.hidden;
      toggle.textContent = tableWrap.hidden ? '展开案件明细' : '收起案件明细';
    });
    card.append(toggle, tableWrap);
    groupsEl.append(card);
  });
  if (!report.groups.length) groupsEl.append(element('p', '当前没有已派工案件'));
}

async function load() {
  feedback.textContent = '正在读取班报…';
  try {
    const [report, me] = await Promise.all([request(), fetch('/api/me', {headers: {'Authorization': `Bearer ${token()}`}}).then(r => r.json())]);
    render(report, me);
    feedback.textContent = '';
  } catch (error) { feedback.textContent = error.message; }
}

refreshBtn.addEventListener('click', load);
launcher.addEventListener('click', async () => {
  dialog.showModal();
  await load();
});
