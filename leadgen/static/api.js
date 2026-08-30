/* Minimal fetch wrapper + shared UI helpers (no framework, no build step). */
const API = {
  async req(method, path, body) {
    const opts = { method, headers: {} };
    if (body !== undefined) {
      opts.headers['content-type'] = 'application/json';
      opts.body = JSON.stringify(body);
    }
    const res = await fetch(path, opts);
    let data = null;
    try { data = await res.json(); } catch (e) { data = null; }
    if (!res.ok) {
      const detail = (data && (data.detail || data.message)) || `HTTP ${res.status}`;
      throw new Error(typeof detail === 'string' ? detail : JSON.stringify(detail));
    }
    return data;
  },
  get(path) { return this.req('GET', path); },
  post(path, body) { return this.req('POST', path, body === undefined ? {} : body); },
  patch(path, body) { return this.req('PATCH', path, body); },
  del(path) { return this.req('DELETE', path); },
};

/* ------------------------------------------------------------------ utils */
const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));

function h(tag, attrs = {}, children = []) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === 'class') el.className = v;
    else if (k === 'html') el.innerHTML = v;
    else if (k.startsWith('on') && typeof v === 'function') el.addEventListener(k.slice(2), v);
    else if (v !== null && v !== undefined && v !== false) el.setAttribute(k, v);
  }
  (Array.isArray(children) ? children : [children]).forEach((c) => {
    if (c === null || c === undefined || c === false) return;
    el.appendChild(typeof c === 'string' ? document.createTextNode(c) : c);
  });
  return el;
}

function toast(message, kind = '') {
  const host = $('#toast-host');
  const el = h('div', { class: `toast ${kind}` }, message);
  host.appendChild(el);
  setTimeout(() => el.remove(), kind === 'err' ? 7000 : 4200);
}

function fmtDate(value) {
  if (!value) return '—';
  const d = new Date(value);
  if (Number.isNaN(d.getTime())) return value;
  return d.toLocaleString(undefined, { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' });
}

function pct(value, digits = 1) {
  return `${((value || 0) * 100).toFixed(digits)}%`;
}

function statCard(label, value, hint) {
  return h('div', { class: 'card stat' }, [
    h('div', { class: 'label' }, label),
    h('div', { class: 'value' }, String(value)),
    hint ? h('div', { class: 'hint' }, hint) : null,
  ]);
}

function pill(text, kind = 'idle') {
  return h('span', { class: `pill pill-${kind}` }, text);
}

function bar(ratio, kind = '') {
  const clamped = Math.max(0, Math.min(1, ratio || 0));
  return h('div', { class: `bar ${kind}` }, [h('span', { style: `width:${(clamped * 100).toFixed(1)}%` })]);
}

function emptyState(text) {
  return h('div', { class: 'empty' }, text);
}

function openModal(title, bodyNode, actions = []) {
  const host = $('#modal-host');
  host.innerHTML = '';
  const modal = h('div', { class: 'modal' }, [
    h('h2', {}, title),
    bodyNode,
    h('div', { class: 'btn-row' }, [
      ...actions,
      h('button', { class: 'btn ghost', onclick: () => (host.classList.add('hidden')) }, 'Close'),
    ]),
  ]);
  host.appendChild(modal);
  host.classList.remove('hidden');
}

function closeModal() { $('#modal-host').classList.add('hidden'); }

const INTENT_LABEL = {
  interested: ['Interested', 'good'],
  not_interested: ['Not interested', 'bad'],
  out_of_office: ['Out of office', 'idle'],
  auto_reply: ['Auto reply', 'idle'],
  question: ['Question', 'accent'],
  spam: ['Spam', 'bad'],
  unknown: ['Unclassified', 'idle'],
};

const STAGE_LABEL = {
  new: 'New', contacted: 'Contacted', replied: 'Replied', engaged: 'Engaged',
  meeting: 'Meeting', proposal: 'Proposal', won: 'Won', lost: 'Lost',
};
