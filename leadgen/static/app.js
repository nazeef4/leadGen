/* App shell: navigation, mobile drawer, global state polling, boot. */
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

/* ------------------------------------------------------- mobile drawer */
function openNav() {
  const sidebar = $('#sidebar');
  const scrim = $('#scrim');
  const open = $('#btn-nav-open');
  sidebar.classList.add('open');
  scrim.hidden = false;
  document.body.classList.add('nav-locked');
  if (open) open.setAttribute('aria-expanded', 'true');
}

function closeNav() {
  const sidebar = $('#sidebar');
  const scrim = $('#scrim');
  const open = $('#btn-nav-open');
  if (!sidebar || !sidebar.classList.contains('open')) return;
  sidebar.classList.remove('open');
  if (scrim) scrim.hidden = true;
  document.body.classList.remove('nav-locked');
  if (open) open.setAttribute('aria-expanded', 'false');
}

async function go(view) {
  const host = $('#view');
  const meta = VIEW_META[view] || [view, ''];
  $('#view-title').textContent = meta[0];
  $('#view-sub').textContent = meta[1];
  $$('.nav-item').forEach((b) => b.classList.toggle('active', b.dataset.view === view));
  document.title = `${meta[0]} · LeadGen Studio`;
  closeNav();
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
  // On phones the new screen starts below the fold after the drawer closes.
  if (window.innerWidth <= 820) window.scrollTo({ top: 0, behavior: 'auto' });
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

  const openBtn = $('#btn-nav-open');
  if (openBtn) openBtn.addEventListener('click', openNav);
  const closeBtn = $('#btn-nav-close');
  if (closeBtn) closeBtn.addEventListener('click', closeNav);
  const scrim = $('#scrim');
  if (scrim) scrim.addEventListener('click', closeNav);

  document.addEventListener('keydown', (e) => {
    if (e.key !== 'Escape') return;
    // Close the topmost layer first: modal, then the nav drawer.
    if (!$('#modal-host').classList.contains('hidden')) closeModal();
    else closeNav();
  });

  // A growing viewport should never leave the drawer stuck open.
  window.addEventListener('resize', () => { if (window.innerWidth > 820) closeNav(); });

  refreshChrome();
  setInterval(refreshChrome, 8000);
  go('dashboard');
});
