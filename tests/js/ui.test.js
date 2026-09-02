#!/usr/bin/env node
/*
 * Headless DOM test for the SPA.
 *
 * Loads index.html into jsdom, mounts the three real scripts, stubs `fetch`
 * with responses captured from the live API, then renders every screen and
 * drives the geo picker. Exits non-zero on the first failure.
 *
 * Run via: node tests/js/ui.test.js <fixtureDir>
 */
'use strict';

const fs = require('fs');
const path = require('path');
const { JSDOM } = require('jsdom');

const ROOT = path.resolve(__dirname, '..', '..');
const STATIC = path.join(ROOT, 'leadgen', 'static');
const FIXTURE_DIR = process.argv[2] || '/tmp/fixtures';

const failures = [];
const checks = [];

function ok(name, cond, detail) {
  checks.push(name);
  if (!cond) failures.push(`${name}${detail ? ` — ${detail}` : ''}`);
}

function fixture(name) {
  const file = path.join(FIXTURE_DIR, `${name}.json`);
  if (!fs.existsSync(file)) throw new Error(`missing fixture ${file}`);
  return JSON.parse(fs.readFileSync(file, 'utf8'));
}

/* ---------------------------------------------------------------- fixtures */
const FIX = {
  '/api/system/health': fixture('health'),
  '/api/system/compliance-posture': fixture('posture'),
  '/api/system/settings': fixture('settings'),
  '/api/accounts': fixture('accounts'),
  '/api/targeting/countries': fixture('countries'),
  '/api/targeting/niche-archetypes': fixture('archetypes'),
  '/api/crm/overview': fixture('overview'),
  '/api/crm/replies': fixture('replies'),
  '/api/crm/pipeline': fixture('pipeline'),
  '/api/crm/suppressions': fixture('suppressions'),
  '/api/campaigns': fixture('campaigns'),
  '/api/campaigns/1': fixture('campaign_detail'),
  '/api/campaigns/dispatch/state': fixture('dispatch_state'),
};

const requested = [];

function stubFetch(url, opts = {}) {
  const pathOnly = String(url).split('?')[0];
  requested.push(`${opts.method || 'GET'} ${url}`);
  const method = (opts.method || 'GET').toUpperCase();

  let payload;
  if (pathOnly.startsWith('/api/campaigns/1/leads')) {
    payload = fixture('leads');
  } else if (pathOnly === '/api/crm/replies') {
    payload = fixture('replies');
  } else if (pathOnly.startsWith('/api/targeting/countries/') && pathOnly.endsWith('/states')) {
    payload = { states: [{ code: 'AZ', name: 'Arizona', cityCount: 12 }, { code: 'TX', name: 'Texas', cityCount: 20 }] };
  } else if (pathOnly.includes('/cities')) {
    payload = { cities: [
      { name: 'Phoenix', avgSummerC: 41, climate: ['desert', 'arid'], popTier: 3 },
      { name: 'Tucson', avgSummerC: 40, climate: ['desert'], popTier: 2 },
    ] };
  } else if (pathOnly === '/api/campaigns/1/plan-preview') {
    payload = { plan: { total: 10, scheduled: 10, deferred: 0, meanGapSeconds: 14.7, spanMinutes: 3,
      slots: [{ leadId: 5, delaySeconds: 14, sendAt: '2026-01-01T12:00:00Z', longPause: false }] }, problems: [] };
  } else if (pathOnly === '/api/campaigns/1/preview') {
    payload = { previews: [{ subject: 'S', bodyText: 'T', bodyHtml: '<p>T</p>', email: 'a@b.com',
      templateKey: 'consultative', source: 'offline' }] };
  } else if (pathOnly === '/api/campaigns/compliance-check') {
    payload = { score: 100, blocked: false, issues: [] };
  } else if (pathOnly === '/api/targeting/niche-suggestions') {
    payload = { archetypeKey: 'hvac_cooling', archetypeLabel: 'HVAC', candidateCount: 685,
      source: 'rules', strategy: 'hot climates', constrainedToSelection: false,
      suggestions: [{ label: 'Phoenix, Arizona, United States', score: 100, fit: 'strong',
        avgSummerC: 41, climate: ['desert'], reasons: ['hot'], sampleQueries: ['hvac Phoenix'] }],
      searchTerms: ['hvac Phoenix'], targetCategories: ['HVAC'], hooks: ['uptime'],
      personas: ['Facilities manager'], seasonality: 'summer', adjacentNiches: [] };
  } else {
    payload = FIX[pathOnly];
  }

  if (payload === undefined) {
    return Promise.resolve({ ok: false, status: 404, json: async () => ({ detail: `no fixture for ${pathOnly}` }) });
  }
  return Promise.resolve({ ok: true, status: 200, json: async () => payload });
}

/* -------------------------------------------------------------------- boot */
async function main() {
  const html = fs.readFileSync(path.join(STATIC, 'index.html'), 'utf8');
  const dom = new JSDOM(html, { runScripts: 'outside-only', pretendToBeVisual: true });
  const { window } = dom;

  window.fetch = stubFetch;
  // jsdom does not implement these; the app only needs them to exist.
  window.URL.createObjectURL = () => 'blob:stub';

  const consoleErrors = [];
  window.console.error = (...a) => consoleErrors.push(a.join(' '));

  // The three files share one lexical scope in the browser (top-level `const`
  // in a classic script is script-scoped but shared across scripts), so eval
  // them as a single unit and expose the internals the harness drives.
  const FILES = ['api.js', 'views.js', 'app.js'];
  const combined = FILES.map((f) => {
    const code = fs.readFileSync(path.join(STATIC, f), 'utf8');
    ok(`script reads: ${f}`, code.length > 500, `${code.length} bytes`);
    return `// ==== ${f} ====\n${code}`;
  }).join('\n;\n');

  const epilogue = `
;window.__app = { go, Views, State, API, h, $, $$ };
`;
  try {
    window.eval(combined + epilogue);
    ok('all three scripts evaluate in one scope', true);
  } catch (err) {
    ok('all three scripts evaluate in one scope', false, err.message);
    throw err;
  }

  window.document.dispatchEvent(new window.Event('DOMContentLoaded', { bubbles: true }));
  await new Promise((r) => setTimeout(r, 60));

  ok('dashboard renders on boot', window.document.querySelectorAll('#view .card').length >= 3,
    `${window.document.querySelectorAll('#view .card').length} cards`);
  ok('sidebar quota populated', !/—|unreachable/.test(window.document.querySelector('#quota-mini').textContent),
    window.document.querySelector('#quota-mini').textContent);

  /* ------------------------------------------------- every screen renders */
  for (const view of ['dashboard', 'targeting', 'leads', 'compose', 'dispatch', 'crm', 'accounts', 'settings']) {
    const before = failures.length;
    try {
      await window.__app.go(view);
    } catch (err) {
      ok(`view renders: ${view}`, false, err.stack || err.message);
      continue;
    }
    const cards = window.document.querySelectorAll('#view .card').length;
    ok(`view renders: ${view}`, cards > 0 && failures.length === before, `${cards} cards`);
    const title = window.document.querySelector('#view-title').textContent;
    ok(`view title set: ${view}`, title.length > 0 && title !== 'Dashboard' || view === 'dashboard', title);
  }

  /* --------------------------------------------------- geo picker wiring */
  await window.__app.go('targeting');
  const row = window.document.querySelector('#view .row');
  const selects = row ? Array.from(row.querySelectorAll('select')) : [];
  ok('geo picker has 3 selects', selects.length === 3, `found ${selects.length}`);

  const [countrySel, stateSel, citySel] = selects;
  ok('country select populated', countrySel && countrySel.options.length > 40,
    countrySel ? `${countrySel.options.length} options` : 'missing');
  ok('state select starts disabled', stateSel && stateSel.disabled === true);
  ok('city select starts disabled', citySel && citySel.disabled === true);

  countrySel.value = 'US';
  countrySel.dispatchEvent(new window.Event('change', { bubbles: true }));
  await new Promise((r) => setTimeout(r, 40));
  ok('choosing a country loads states', stateSel.disabled === false && stateSel.options.length === 3,
    `disabled=${stateSel.disabled} options=${stateSel.options.length}`);

  stateSel.value = 'AZ';
  stateSel.dispatchEvent(new window.Event('change', { bubbles: true }));
  await new Promise((r) => setTimeout(r, 40));
  ok('choosing a state loads cities', citySel.disabled === false && citySel.options.length === 3,
    `disabled=${citySel.disabled} options=${citySel.options.length}`);
  ok('city option shows climate data', /Phoenix/.test(citySel.options[1].textContent)
    && /41/.test(citySel.options[1].textContent), citySel.options[1] && citySel.options[1].textContent);

  const addBtn = Array.from(window.document.querySelectorAll('#view button'))
    .find((b) => b.textContent.trim() === 'Add selection');
  ok('Add selection button present', !!addBtn);
  addBtn.click();
  const chips = window.document.querySelectorAll('#view .chips .chip.on');
  ok('selection becomes a removable chip', chips.length === 1, `${chips.length} chips`);
  // City still reads "All cities" (value '*'), so only state + country are named.
  ok('whole-state selection omits the city', chips[0].textContent.trim() === 'AZ, US ✕',
    chips[0].textContent);

  chips[0].click();
  ok('chip removal clears the selection',
    window.document.querySelectorAll('#view .chips .chip.on').length === 0);

  citySel.value = 'Phoenix';
  citySel.dispatchEvent(new window.Event('change', { bubbles: true }));
  addBtn.click();
  const cityChip = window.document.querySelectorAll('#view .chips .chip.on')[0];
  ok('city-level selection names city, state and country',
    cityChip.textContent.trim() === 'Phoenix, AZ, US ✕', cityChip.textContent);

  // Adding the same place twice must not duplicate it.
  addBtn.click();
  ok('duplicate selections are not added twice',
    window.document.querySelectorAll('#view .chips .chip.on').length === 1,
    `${window.document.querySelectorAll('#view .chips .chip.on').length} chips`);
  cityChip.click();

  /* ------------------------------------------------- niche suggestions UI */
  const offerInput = window.document.querySelector('#view input[type="text"]');
  offerInput.value = 'HVAC and AC repair services';
  const suggestBtn = Array.from(window.document.querySelectorAll('#view button'))
    .find((b) => b.textContent.includes('Get AI suggestions'));
  ok('suggest button present', !!suggestBtn);
  suggestBtn.click();
  await new Promise((r) => setTimeout(r, 40));
  ok('suggestions render a ranked table',
    window.document.querySelectorAll('#view table tbody tr').length >= 1,
    `${window.document.querySelectorAll('#view table tbody tr').length} rows`);

  /* ---------------------------------------------------- leads table wiring */
  await window.__app.go('leads');
  const boxes = window.document.querySelectorAll('#app tbody input.lead-check');
  ok('leads table renders rows', boxes.length > 0, `${boxes.length} rows`);
  const selectAll = window.document.querySelector('#app thead input[type="checkbox"]');
  ok('select-all checkbox present', !!selectAll);
  selectAll.checked = true;
  selectAll.dispatchEvent(new window.Event('change', { bubbles: true }));
  ok('select-all ticks every row', Array.from(boxes).every((b) => b.checked === true));

  /* ---------------------------------------------------- compose preview */
  await window.__app.go('compose');
  const runBtn = Array.from(window.document.querySelectorAll('#view button'))
    .find((b) => b.textContent.trim() === 'Generate previews');
  ok('generate previews button present', !!runBtn);
  runBtn.click();
  await new Promise((r) => setTimeout(r, 60));
  ok('compose preview renders an email',
    window.document.querySelectorAll('#view .email-preview').length >= 1,
    `${window.document.querySelectorAll('#view .email-preview').length} previews`);

  /* ---------------------------------------------------------- dispatch */
  await window.__app.go('dispatch');
  const planBtn = Array.from(window.document.querySelectorAll('#view button'))
    .find((b) => b.textContent.includes('Preview the send plan'));
  ok('plan preview button present', !!planBtn);
  planBtn.click();
  await new Promise((r) => setTimeout(r, 60));
  ok('send plan renders slots',
    /randomised/i.test(window.document.querySelector('#view').textContent));

  /* ------------------------------------------------------ error surfacing */
  const realFetch = window.fetch;
  window.fetch = (url) => {
    if (String(url).includes('/api/crm/overview')) {
      return Promise.resolve({ ok: false, status: 500, json: async () => ({ detail: 'backend exploded' }) });
    }
    return realFetch(url, {});
  };
  await window.__app.go('dashboard');
  ok('API errors surface instead of a blank screen',
    /backend exploded/.test(window.document.querySelector('#view').textContent)
    || window.document.querySelectorAll('#view .toast').length > 0
    || /Could not load this screen/.test(window.document.querySelector('#view').textContent),
    window.document.querySelector('#view').textContent.slice(0, 80));
  window.fetch = realFetch;

  /* --------------------------------------------------------- first run */
  // An empty workspace must show guidance, not just a wall of zeros.
  const runFetch = window.fetch;
  let demoPosted = false;
  window.fetch = (url, opts = {}) => {
    const u = String(url);
    if (u.includes('/api/crm/overview')) {
      const empty = { totals: { leads: 0, sent: 0, replies: 0, interested: 0, unread: 0, replyRate: 0 }, campaigns: [] };
      return Promise.resolve({ ok: true, status: 200, json: async () => empty });
    }
    if (u.includes('/api/campaigns') && !u.includes('dispatch')) {
      return Promise.resolve({ ok: true, status: 200, json: async () => ({ campaigns: [] }) });
    }
    if (u.includes('/api/system/demo-data')) {
      demoPosted = true;
      return Promise.resolve({ ok: true, status: 200, json: async () => ({ ok: true, leads: 18, replies: 5 }) });
    }
    return runFetch(u, opts);
  };
  await window.__app.go('dashboard');
  const fr = window.document.querySelector('#view .first-run');
  ok('empty workspace shows a first-run guide', !!fr);
  ok('guide lists all six setup steps',
    window.document.querySelectorAll('#view .first-run .step').length === 6,
    `${window.document.querySelectorAll('#view .first-run .step').length} steps`);
  ok('guide steps are real buttons',
    window.document.querySelectorAll('#view .first-run button.step[type="button"]').length === 6);
  const demoBtn = Array.from(window.document.querySelectorAll('#view .first-run .btn'))
    .find((b) => /sample data/i.test(b.textContent));
  ok('guide offers a one-click sample loader', !!demoBtn);
  demoBtn.click();
  await new Promise((r) => setTimeout(r, 60));
  ok('sample loader calls the demo-data endpoint', demoPosted);
  ok('sample loader reports success to the user',
    window.document.querySelectorAll('#toast-host .toast').length > 0);
  window.fetch = runFetch;

  /* ------------------------------------------------- responsive / mobile */
  const sidebar = window.document.querySelector('#sidebar');
  const scrimEl = window.document.querySelector('#scrim');
  const openBtn = window.document.querySelector('#btn-nav-open');
  const closeBtn = window.document.querySelector('#btn-nav-close');
  ok('mobile drawer elements present', !!(sidebar && scrimEl && openBtn && closeBtn));
  ok('drawer starts closed', !sidebar.classList.contains('open') && scrimEl.hidden === true);
  ok('hamburger advertises collapsed state', openBtn.getAttribute('aria-expanded') === 'false');

  openBtn.click();
  ok('hamburger opens the drawer', sidebar.classList.contains('open'));
  ok('opening reveals the scrim', scrimEl.hidden === false);
  ok('hamburger advertises expanded state', openBtn.getAttribute('aria-expanded') === 'true');
  ok('body scroll is locked while the drawer is open',
    window.document.body.classList.contains('nav-locked'));

  // Escape must close the drawer when no modal is open.
  window.document.dispatchEvent(new window.Event('keydown', { bubbles: true }));
  // jsdom KeyboardEvent needs the key set explicitly.
  const esc = new window.KeyboardEvent('keydown', { key: 'Escape', bubbles: true });
  window.document.dispatchEvent(esc);
  ok('Escape closes the drawer', !sidebar.classList.contains('open') && scrimEl.hidden === true);

  openBtn.click();
  scrimEl.click();
  ok('tapping the scrim closes the drawer', !sidebar.classList.contains('open'));

  openBtn.click();
  ok('drawer reopens before navigation test', sidebar.classList.contains('open'));
  await window.__app.go('crm');
  ok('navigating closes the drawer', !sidebar.classList.contains('open'));

  // The stacked mobile table needs labels, or cells render unnamed.
  await window.__app.go('leads');
  const table = window.document.querySelector('#view table');
  ok('leads table opts into the responsive layout', table.classList.contains('responsive'));
  const labelled = window.document.querySelectorAll('#view table td[data-label]');
  ok('lead rows carry column labels for the mobile card view', labelled.length >= 12,
    `${labelled.length} labelled cells`);
  const labels = Array.from(new Set(Array.from(labelled).map((td) => td.dataset.label)));
  ok('labels name the real columns',
    ['Business', 'Email', 'Location', 'Score', 'Status'].every((l) => labels.includes(l)),
    labels.join(','));
  ok('checkbox cells are excluded from the label treatment',
    !!window.document.querySelector('#view table td.cell-check'));
  ok('row checkbox has an accessible name',
    !!window.document.querySelector('#view table td.cell-check input[aria-label]'));

  ok('no uncaught console errors', consoleErrors.length === 0, consoleErrors.slice(0, 3).join(' | '));

  /* ---------------------------------------------------------------- report */
  // app.js installs a setInterval; without tearing the window down Node never
  // exits even when every check passes.
  window.close();

  console.log(`\n${checks.length - failures.length}/${checks.length} DOM checks passed`);
  if (failures.length) {
    console.log('\nFAILURES:');
    failures.forEach((f) => console.log(`  ✗ ${f}`));
    process.exit(1);
  }
  console.log('all DOM checks passed');
  process.exit(0);
}

const watchdog = setTimeout(() => {
  console.error('harness timed out after 120s');
  process.exit(3);
}, 120000);
watchdog.unref();

main().catch((err) => {
  console.error('harness crashed:', err);
  process.exit(2);
});
