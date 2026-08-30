/* App shell: navigation, global state polling, boot. */
const VIEW_META = {
  dashboard: ['Dashboard', 'Overview of targeting, outreach and pipeline health.'],
  targeting: ['1 · Smart targeting', 'Define the offer, let the advisor pick the geography and buyer profile.'],
  leads: ['2 · Lead curation', 'Scrape, review and hand-pick the businesses to contact.'],
  compose: ['3 · Dynamic copy', 'Per-lead personalised emails with a compliance check on every draft.'],
  dispatch: ['4 · Compliant dispatch', 'Randomised, unordered sending with live quota guardrails.'],
  crm: ['5 · CRM & replies', 'Reply detection, pipeline progression and suppression.'],
  accounts: ['Sending accounts', 'Encrypted locally. Gmail needs an App Password with 2FA enabled.'],
  settings: ['Compliance & AI', 'Sender identity, pacing limits and the optional AI copy engine.'],
};

async function go(view) {
  const host = $('#view');
  const meta = VIEW_META[view] || [view, ''];
  $('#view-title').textContent = meta[0];
  $('#view-sub').textContent = meta[1];
  $$('.nav-item').forEach((b) => b.classList.toggle('active', b.dataset.view === view));
  host.innerHTML = '<div class="empty"><span class="spinner"></span> loading…</div>';
  host.setAttribute('data-active-view', view);
  try {
    const node = await Views[view]();
    host.innerHTML = '';
    host.appendChild(node);
  } catch (err) {
    host.innerHTML = '';
    host.appendChild(h('div', { class: 'card' }, [
      h('h2', {}, 'Could not load this screen'),
      h('p', { class: 'muted' }, String(err.message || err)),
    ]));
    toast(String(err.message || err), 'err');
  }
}

async function refreshChrome() {
  try {
    const health = await API.get('/api/system/health');
    $('#brand-version').textContent = `v${health.app.version} · local`;
    const remaining = health.quota.remaining;
    $('#quota-mini').textContent = `${health.quota.sentToday}/${health.quota.dailyCap} sent today · ${remaining} left`;
    const pillEl = $('#dispatch-pill');
    const d = health.dispatch;
    const label = d.running ? (d.paused ? 'paused' : 'sending') : 'idle';
    pillEl.className = `pill pill-${d.running ? (d.paused ? 'pause' : 'run') : 'idle'}`;
    pillEl.textContent = d.running ? `${label} · ${d.sent} sent` : label;
  } catch (err) {
    $('#quota-mini').textContent = 'backend unreachable';
  }
}

document.addEventListener('DOMContentLoaded', () => {
  $$('.nav-item').forEach((btn) => btn.addEventListener('click', () => go(btn.dataset.view)));
  $('#btn-refresh').addEventListener('click', () => { refreshChrome(); go($('.nav-item.active').dataset.view); });
  $('#modal-host').addEventListener('click', (e) => { if (e.target.id === 'modal-host') closeModal(); });
  document.addEventListener('keydown', (e) => { if (e.key === 'Escape') closeModal(); });
  refreshChrome();
  setInterval(refreshChrome, 8000);
  go('dashboard');
});
