/* View renderers.  Each entry returns a DOM node for one screen. */
const State = {
  campaignId: null,
  campaigns: [],
  accounts: [],
  settings: {},
  geoSelections: [],
  offer: {
    free_demo_call: false, free_audit: false, case_study: false, discount_percent: 0,
    limited_slots: 0, guarantee: '', local_reference: false, no_follow_up_pressure: false,
    calendar_url: '', extra_note: '',
  },
  scrapeJobId: null,
  scrapeTimer: null,
  dispatchTimer: null,
};

const Views = {};

/* ============================================================= dashboard */
function firstRunCard() {
  const steps = [
    ['Connect a sending account', 'accounts',
      'SMTP and IMAP credentials power real sends and reply syncing.'],
    ['Define your targeting', 'targeting',
      'Pick a niche, then the countries, states or cities you want to reach.'],
    ['Collect and curate leads', 'leads',
      'Scrape a directory, import a CSV, or try the offline sample source.'],
    ['Draft the outreach', 'compose',
      'Personalise the copy and preview it against the compliance scan.'],
    ['Queue the dispatch', 'dispatch',
      'Throttled, randomised sends stay under the daily recipient cap.'],
    ['Track replies', 'crm',
      'Responses are matched back to leads and flagged for follow-up.'],
  ];
  return h('div', { class: 'card first-run' }, [
    h('h2', {}, 'Get started'),
    h('p', { class: 'muted' },
      'This workspace has no data yet. Work through the six steps below, or load a sample workspace to see every screen populated straight away.'),
    h('ol', { class: 'steps' }, steps.map(([title, view, blurb], i) => h('li', {}, [
      h('button', { class: 'step', type: 'button', onclick: () => go(view) }, [
        h('span', { class: 'step-num', 'aria-hidden': 'true' }, String(i + 1)),
        h('span', { class: 'step-body' }, [
          h('span', { class: 'step-title' }, title),
          h('span', { class: 'sub muted' }, blurb),
        ]),
      ]),
    ]))),
    h('div', { class: 'btn-row' }, [
      h('button', {
        class: 'btn primary',
        type: 'button',
        onclick: async (e) => {
          const btn = e.currentTarget;
          btn.disabled = true;
          btn.textContent = 'Loading sample data…';
          try {
            const res = await API.post('/api/system/demo-data');
            toast(`Sample workspace loaded — ${res.leads} leads, ${res.replies} replies`, 'good');
            go('dashboard');
          } catch (err) {
            toast(String(err.message || err), 'err');
            btn.disabled = false;
            btn.textContent = 'Load sample data';
          }
        },
      }, 'Load sample data'),
      h('span', { class: 'muted' }, 'Adds a demo campaign; it never deletes your own data.'),
    ]),
  ]);
}

Views.dashboard = async () => {
  const [health, crm, posture] = await Promise.all([
    API.get('/api/system/health'),
    API.get('/api/crm/overview'),
    API.get('/api/system/compliance-posture'),
  ]);
  const wrap = h('div', { class: 'view' });

  wrap.appendChild(h('div', { class: 'grid cols-4' }, [
    statCard('Leads curated', crm.totals.leads, 'across all campaigns'),
    statCard('Emails sent', crm.totals.sent, `${health.quota.sentToday} today`),
    statCard('Replies', crm.totals.replies, `reply rate ${pct(crm.totals.replyRate)}`),
    statCard('Interested', crm.totals.interested, `${crm.totals.unread} unread replies`),
  ]));

  if (crm.totals.leads === 0 && (crm.campaigns || []).length === 0) {
    wrap.appendChild(firstRunCard());
  }

  const quotaRatio = health.quota.dailyCap ? health.quota.sentToday / health.quota.dailyCap : 0;
  const quotaCard = h('div', { class: 'card' }, [
    h('h2', {}, 'Today\'s sending budget'),
    h('p', { class: 'muted' }, `Hard cap ${health.quota.dailyCap} recipients/day — Google free accounts stop at 500.`),
    h('div', { class: 'kv' }, [h('span', { class: 'k' }, 'Used'), h('span', {}, `${health.quota.sentToday} / ${health.quota.dailyCap}`)]),
    bar(quotaRatio, quotaRatio > 0.85 ? 'red' : quotaRatio > 0.6 ? 'amber' : 'green'),
    h('div', { class: 'kv' }, [
      h('span', { class: 'k' }, 'Remaining'),
      h('span', {}, `${health.quota.remaining} recipients`),
    ]),
    h('div', { class: 'kv' }, [
      h('span', { class: 'k' }, 'Dispatch engine'),
      h('span', {}, health.dispatch.running ? (health.dispatch.paused ? 'paused' : 'running') : 'idle'),
    ]),
    h('div', { class: 'kv' }, [
      h('span', { class: 'k' }, 'Next send'),
      h('span', {}, fmtDate(health.dispatch.nextSendAt)),
    ]),
  ]);

  const guardrails = h('div', { class: 'card' }, [
    h('h2', {}, 'Compliance guardrails'),
    h('p', { class: 'muted' }, 'Every send passes these checks; a blocking issue stops the queue.'),
    h('div', { class: 'list' }, posture.checks.map((c) => h('div', { class: 'list-item' }, [
      h('div', {}, [
        h('div', {}, c.label),
        h('div', { class: 'sub muted', style: 'font-size:11.5px' }, String(c.value)),
      ]),
      pill(c.active ? 'active' : 'off', c.active ? 'good' : 'idle'),
    ]))),
  ]);

  wrap.appendChild(h('div', { class: 'grid cols-2' }, [quotaCard, guardrails]));

  const campaigns = crm.campaigns || [];
  wrap.appendChild(h('div', { class: 'card' }, [
    h('h2', {}, 'Campaign performance'),
    campaigns.length === 0
      ? emptyState('No campaigns yet — start with the Targeting screen.')
      : h('div', { class: 'table-wrap' }, [
          h('table', {}, [
            h('thead', {}, h('tr', {}, [
              h('th', {}, 'Campaign'), h('th', {}, 'Leads'), h('th', {}, 'Sent'),
              h('th', {}, 'Replies'), h('th', {}, 'Reply rate'), h('th', {}, 'Interested'), h('th', {}, 'Status'),
            ])),
            h('tbody', {}, campaigns.map((c) => h('tr', {}, [
              h('td', {}, [
                h('a', { href: '#', onclick: (e) => { e.preventDefault(); State.campaignId = c.campaignId; go('leads'); } }, c.campaignName),
                h('span', { class: 'sub' }, `#${c.campaignId}`),
              ]),
              h('td', {}, String(c.leads)),
              h('td', {}, String(c.sent)),
              h('td', {}, String(c.replies)),
              h('td', { class: 'score' }, pct(c.replyRate)),
              h('td', {}, String(c.interested)),
              h('td', {}, pill(c.status, c.status === 'running' ? 'run' : 'idle')),
            ]))),
          ]),
        ]),
  ]));

  wrap.appendChild(h('div', { class: 'card' }, [
    h('h2', {}, 'System'),
    h('div', { class: 'kv' }, [h('span', { class: 'k' }, 'Database'), h('span', {}, health.database)]),
    h('div', { class: 'kv' }, [h('span', { class: 'k' }, 'State directory'), h('span', {}, health.stateDir)]),
    h('div', { class: 'kv' }, [h('span', { class: 'k' }, 'Sending accounts'), h('span', {}, `${health.accounts.verified}/${health.accounts.total} verified`)]),
    h('div', { class: 'kv' }, [h('span', { class: 'k' }, 'AI copy engine'), h('span', {}, `${health.llm.provider} (${health.llm.enabled ? 'configured' : 'offline templates'})`)]),
    h('div', { class: 'kv' }, [h('span', { class: 'k' }, 'Scrapers'), h('span', {}, health.scrapers.join(', '))]),
  ]));
  return wrap;
};

/* ============================================================= targeting */
Views.targeting = async () => {
  const [{ countries }, presets] = await Promise.all([
    API.get('/api/targeting/countries'),
    API.get('/api/targeting/niche-archetypes'),
  ]);
  const wrap = h('div', { class: 'view' });

  const offeringInput = h('input', {
    type: 'text', placeholder: 'e.g. HVAC and AC repair for commercial buildings',
    value: State.offering || '',
  });
  const topBox = h('div', {});

  const suggestBtn = h('button', {
    class: 'btn',
    onclick: async () => {
      State.offering = offeringInput.value;
      if (!State.offering.trim()) return toast('Describe the service offering first', 'err');
      suggestBtn.disabled = true;
      suggestBtn.innerHTML = '<span class="spinner"></span>';
      try {
        const result = await API.post('/api/targeting/niche-suggestions', {
          offering: State.offering,
          geoFilter: { selections: State.geoSelections, extraCities: [] },
          topN: 12, useLlm: false,
        });
        renderSuggestions(topBox, result, presets.archetypes);
      } catch (err) { toast(err.message, 'err'); }
      suggestBtn.disabled = false;
      suggestBtn.textContent = 'Get AI suggestions';
    },
  }, 'Get AI suggestions');

  wrap.appendChild(h('div', { class: 'card' }, [
    h('h2', {}, 'What are you selling?'),
    h('p', { class: 'muted' }, 'The advisor matches your offer to climates, market density and buyer personas — e.g. "HVAC/AC repair" surfaces high-temperature metros.'),
    offeringInput,
    h('div', { class: 'chips', style: 'margin-top:10px' }, [
      'HVAC / AC repair', 'Managed IT & cybersecurity', 'Commercial cleaning',
      'Solar installation', 'Snow removal', 'Restaurant marketing', 'Bookkeeping',
    ].map((label) => h('button', {
      class: 'chip', onclick: () => { offeringInput.value = label; },
    }, label))),
    h('div', { class: 'btn-row' }, [
      suggestBtn,
      h('button', {
        class: 'btn ghost',
        onclick: () => openModal('Niche knowledge base', h('pre', {
          class: 'code',
        }, presets.archetypes.map((a) => `${a.label}\n  climate: ${a.climateWanted.join(', ') || 'any'}  temp: ${a.minSummerC ?? '—'}…${a.maxSummerC ?? '—'}°C  density≥${a.densityMin}\n  keywords: ${a.keywords.join(', ')}\n  seasonality: ${a.seasonality}`).join('\n\n'))),
      }, 'View the 18 built-in niches'),
    ]),
  ]));

  /* ---------------------------------------------------------- geo picker */
  // Explicit element references. The card is not in the document yet when this
  // runs, so document-level queries would return nothing here.
  const citySel = h('select', { disabled: true }, [h('option', { value: '*' }, 'All cities')]);
  const stateSel = h('select', { disabled: true }, [
    h('option', { value: '*' }, 'All states / provinces'),
  ]);
  const countrySel = h('select', {}, countries.map((c) => h('option', {
    value: c.code,
  }, `${c.name} (${c.cityCount} cities)`)));
  countrySel.addEventListener('change', () => loadStates(countrySel.value));
  stateSel.addEventListener('change', () => loadCities(countrySel.value, stateSel.value));

  const geoCard = h('div', { class: 'card' }, [
    h('h2', {}, 'Where should we look?'),
    h('p', { class: 'muted' }, 'Drill into country → state → city, or pick "All" at any level. Free-text cities are supported for anything not listed.'),
    h('div', { class: 'row' }, [countrySel, stateSel, citySel]),
  ]);

  const selBox = h('div', { class: 'chips', style: 'margin-top:10px' });
  function renderSelections() {
    selBox.innerHTML = '';
    if (State.geoSelections.length === 0) {
      selBox.appendChild(h('span', { class: 'muted', style: 'font-size:12px' }, 'Nothing selected yet — "All countries" is used by default.'));
      return;
    }
    State.geoSelections.forEach((s, i) => {
      const label = [s.city !== '*' ? s.city : null, s.state !== '*' ? s.state : null, s.country !== '*' ? s.country : null]
        .filter(Boolean).join(', ') || 'Everything';
      selBox.appendChild(h('button', {
        class: 'chip on',
        onclick: () => { State.geoSelections.splice(i, 1); renderSelections(); },
      }, `${label} ✕`));
    });
  }

  const addBtn = h('button', {
    class: 'btn small',
    onclick: () => {
      const country = countrySel.value;
      const state = stateSel.disabled ? '*' : (stateSel.value || '*');
      const city = citySel.disabled ? '*' : (citySel.value || '*');
      const entry = { country, state, city };
      const exists = State.geoSelections.some((s) => JSON.stringify(s) === JSON.stringify(entry));
      if (!exists) State.geoSelections.push(entry);
      renderSelections();
    },
  }, 'Add selection');

  const extraInput = h('input', { type: 'text', placeholder: 'Reykjavik, Iceland' });
  geoCard.appendChild(selBox);
  geoCard.appendChild(h('div', { class: 'btn-row' }, [
    addBtn,
    h('button', {
      class: 'btn ghost small',
      onclick: () => { State.geoSelections = [{ country: '*', state: '*', city: '*' }]; renderSelections(); },
    }, 'Select everything'),
    h('button', { class: 'btn ghost small', onclick: () => { State.geoSelections = []; renderSelections(); } }, 'Clear'),
    extraInput,
    h('button', {
      class: 'btn ghost small',
      onclick: () => { if (extraInput.value.trim()) { State.geoSelections.push({ country: '*', state: '*', city: extraInput.value.trim() }); renderSelections(); extraInput.value = ''; } },
    }, 'Add free-text city'),
  ]));
  wrap.appendChild(geoCard);
  renderSelections();

  async function loadStates(code) {
    const data = await API.get(`/api/targeting/countries/${code}/states`);
    stateSel.innerHTML = '';
    stateSel.disabled = false;
    stateSel.appendChild(h('option', { value: '*' }, `All ${data.states.length} states/provinces`));
    data.states.forEach((s) => stateSel.appendChild(h('option', {
      value: s.code,
    }, `${s.name} (${s.cityCount})`)));
    await loadCities(code, '*');
  }

  async function loadCities(country, state) {
    citySel.innerHTML = '';
    if (state === '*') {
      citySel.disabled = true;
      citySel.appendChild(h('option', { value: '*' }, 'All cities'));
      return;
    }
    const data = await API.get(`/api/targeting/countries/${country}/states/${state}/cities`);
    citySel.disabled = false;
    citySel.appendChild(h('option', { value: '*' }, `All ${data.cities.length} cities`));
    data.cities.forEach((c) => citySel.appendChild(h('option', {
      value: c.name,
    }, `${c.name} — ${c.avgSummerC}°C, ${c.climate.join('/')}`)));
  }

  wrap.appendChild(topBox);

  /* ------------------------------------------------------ offer settings */
  const offerCard = h('div', { class: 'card' }, [
    h('h2', {}, 'Offer & conditional blocks'),
    h('p', { class: 'muted' }, 'Toggle what gets woven into the copy. Anything off is simply not mentioned.'),
  ]);
  const offerGrid = h('div', { class: 'grid cols-2' });
  const toggles = [
    ['free_demo_call', 'Free 15-minute demo call'],
    ['free_audit', 'Free audit / health-check'],
    ['case_study', 'Mention a case study'],
    ['local_reference', 'Offer a local reference'],
    ['no_follow_up_pressure', 'Low pressure ("won\'t follow up")'],
  ];
  toggles.forEach(([key, label]) => {
    const box = h('input', { type: 'checkbox' });
    box.checked = !!State.offer[key];
    box.addEventListener('change', () => { State.offer[key] = box.checked; });
    offerGrid.appendChild(h('label', { class: 'checkbox' }, [box, label]));
  });
  offerCard.appendChild(offerGrid);
  const discount = h('input', { type: 'number', min: '0', max: '90', value: String(State.offer.discount_percent) });
  const slots = h('input', { type: 'number', min: '0', max: '50', value: String(State.offer.limited_slots) });
  const calendar = h('input', { type: 'text', value: State.offer.calendar_url, placeholder: 'https://cal.com/you/15min' });
  const guarantee = h('input', { type: 'text', value: State.offer.guarantee, placeholder: 'e.g. no results, no invoice' });
  offerCard.appendChild(h('div', { class: 'row' }, [
    h('div', {}, [h('label', {}, 'Discount %'), discount]),
    h('div', {}, [h('label', {}, 'Limited slots this month'), slots]),
    h('div', {}, [h('label', {}, 'Calendar link'), calendar]),
    h('div', {}, [h('label', {}, 'Guarantee wording'), guarantee]),
  ]));
  offerCard.appendChild(h('div', { class: 'btn-row' }, [
    h('button', {
      class: 'btn',
      onclick: () => {
        State.offer.discount_percent = Number(discount.value || 0);
        State.offer.limited_slots = Number(slots.value || 0);
        State.offer.calendar_url = calendar.value.trim();
        State.offer.guarantee = guarantee.value.trim();
        toast('Offer saved for this session', 'ok');
      },
    }, 'Save offer settings'),
  ]));
  wrap.appendChild(offerCard);

  /* --------------------------------------------------------- create/save */
  wrap.appendChild(h('div', { class: 'card' }, [await campaignForm()]));
  return wrap;
};

function renderSuggestions(host, result, archetypes = []) {
  host.innerHTML = '';
  const list = h('div', { class: 'table-wrap' }, [
    h('table', {}, [
      h('thead', {}, h('tr', {}, [
        h('th', {}, 'Target'), h('th', {}, 'Fit'), h('th', {}, 'Avg summer'), h('th', {}, 'Why'), h('th', {}, 'Sample query'),
      ])),
      h('tbody', {}, result.suggestions.map((s) => h('tr', {}, [
        h('td', {}, [s.label, h('span', { class: 'sub' }, s.climate.join(', '))]),
        h('td', {}, pill(`${s.score.toFixed(0)} · ${s.fit}`, s.fit === 'strong' ? 'good' : s.fit === 'moderate' ? 'pause' : 'idle')),
        h('td', { class: 'score' }, `${s.avgSummerC}°C`),
        h('td', { style: 'max-width:340px' }, s.reasons.join('; ')),
        h('td', {}, [h('code', { style: 'font-size:11.5px' }, s.sampleQueries[0] || '')]),
      ]))),
    ]),
  ]);
  host.appendChild(h('div', { class: 'card' }, [
    h('div', { class: 'card-head' }, [
      h('div', {}, [
        h('h2', {}, `Recommended targeting — ${result.archetypeLabel}`),
        h('p', { class: 'muted' }, `${result.candidateCount} places ranked · source: ${result.source}`),
      ]),
      pill(result.constrainedToSelection ? 'your selection' : 'global', 'accent'),
    ]),
    h('p', { class: 'muted', style: 'font-size:12.5px' }, result.strategy),
    list,
    h('h3', {}, 'Search terms the scraper will use'),
    h('div', { class: 'chips' }, result.searchTerms.map((t) => h('span', { class: 'tag' }, t))),
    h('h3', {}, 'Business types to look for'),
    h('div', { class: 'chips' }, result.targetCategories.map((t) => h('span', { class: 'tag' }, t))),
    h('h3', {}, 'Pain points to lead with'),
    h('ul', {}, result.hooks.map((x) => h('li', {}, x))),
    h('h3', {}, 'Buyer personas'),
    h('div', { class: 'chips' }, result.personas.map((t) => h('span', { class: 'tag' }, t))),
    h('h3', {}, 'Seasonality'),
    h('p', { class: 'muted' }, result.seasonality),
    (result.adjacentNiches || []).length
      ? h('div', {}, [
          h('h3', {}, 'Adjacent niches (same buyers)'),
          h('div', { class: 'chips' }, result.adjacentNiches.map((n) => h('span', { class: 'tag' }, `${n.label} — ${n.sharedBuyers.join(', ')}`))),
        ])
      : null,
  ]));
}

async function campaignForm() {
  const { accounts } = await API.get('/api/accounts');
  State.accounts = accounts;
  const name = h('input', { type: 'text', placeholder: 'Phoenix HVAC — Q3' });
  const offering = h('input', { type: 'text', value: State.offering || '' });
  const template = h('select', {}, [
    h('option', { value: 'consultative' }, 'Consultative / question-led'),
    h('option', { value: 'direct' }, 'Direct / value-first'),
    h('option', { value: 'proof' }, 'Case-study / social proof'),
    h('option', { value: 'local' }, 'Local / neighbourly'),
  ]);
  const sender = h('select', {}, [h('option', { value: '' }, '— none (dry run only) —'),
    ...accounts.map((a) => h('option', { value: String(a.id) }, `${a.email} (${a.is_verified ? 'verified' : 'unverified'})`))]);
  const perDay = h('input', { type: 'number', value: '50', min: '1', max: '500' });
  const delayMin = h('input', { type: 'number', value: '45', min: '10' });
  const delayMax = h('input', { type: 'number', value: '240', min: '10' });

  return h('div', {}, [
    h('h2', {}, 'Create the campaign'),
    h('p', { class: 'muted' }, 'Bundles targeting + offer + copy style + sending limits into one runnable campaign.'),
    h('div', { class: 'row' }, [
      h('div', {}, [h('label', {}, 'Campaign name'), name]),
      h('div', {}, [h('label', {}, 'Service offering'), offering]),
    ]),
    h('div', { class: 'row' }, [
      h('div', {}, [h('label', {}, 'Copy style'), template]),
      h('div', {}, [h('label', {}, 'Sending account'), sender]),
    ]),
    h('div', { class: 'row' }, [
      h('div', {}, [h('label', {}, 'Max emails / day'), perDay]),
      h('div', {}, [h('label', {}, 'Min delay (s)'), delayMin]),
      h('div', {}, [h('label', {}, 'Max delay (s)'), delayMax]),
    ]),
    h('div', { class: 'btn-row' }, [
      h('button', {
        class: 'btn',
        onclick: async () => {
          if (!name.value.trim()) return toast('Give the campaign a name', 'err');
          try {
            const res = await API.post('/api/campaigns', {
              name: name.value.trim(),
              service_offering: offering.value.trim() || State.offering || '',
              niche: offering.value.trim() || State.offering || '',
              geo_filter: { selections: State.geoSelections.length ? State.geoSelections : [{ country: '*', state: '*', city: '*' }], extraCities: [] },
              offers: State.offer,
              template_key: template.value,
              sender_account_id: sender.value ? Number(sender.value) : null,
              max_per_day: Number(perDay.value),
              delay_min: Number(delayMin.value),
              delay_max: Number(delayMax.value),
            });
            State.campaignId = res.campaign.id;
            toast(`Campaign #${res.campaign.id} created`, 'ok');
            go('leads');
          } catch (err) { toast(err.message, 'err'); }
        },
      }, 'Create campaign'),
    ]),
  ]);
}

/* ================================================================= leads */
Views.leads = async () => {
  const wrap = h('div', { class: 'view' });
  const { campaigns } = await API.get('/api/campaigns');
  State.campaigns = campaigns;
  if (!State.campaignId && campaigns.length) State.campaignId = campaigns[0].id;

  const picker = h('select', {}, campaigns.map((c) => h('option', {
    value: String(c.id), selected: c.id === State.campaignId ? 'selected' : null,
  }, `#${c.id} — ${c.name} (${c.counts.total} leads)`)));
  picker.addEventListener('change', () => { State.campaignId = Number(picker.value); go('leads'); });

  wrap.appendChild(h('div', { class: 'card' }, [
    h('div', { class: 'card-head' }, [
      h('div', {}, [h('h2', {}, 'Campaign'), h('p', { class: 'muted' }, 'Select all or curate by hand before dispatching.')]),
      picker,
    ]),
  ]));

  if (!State.campaignId) {
    wrap.appendChild(emptyState('Create a campaign on the Targeting screen first.'));
    return wrap;
  }

  const campaign = campaigns.find((c) => c.id === State.campaignId);
  const detail = await API.get(`/api/campaigns/${State.campaignId}`);
  const counts = detail.campaign.counts;

  wrap.appendChild(h('div', { class: 'grid cols-4' }, [
    statCard('Leads', counts.total, `${counts.withEmail} with email`),
    statCard('Selected', counts.selected, 'will be contacted'),
    statCard('Sent', counts.sent, `${counts.byStatus.replied || 0} replied`),
    statCard('Failed / skipped', (counts.byStatus.failed || 0) + (counts.byStatus.skipped || 0), 'excluded or bounced'),
  ]));

  /* --------------------------------------------------------- scrape card */
  const sourceBox = h('div', { class: 'chips' });
  const sources = ['duckduckgo', 'demo', 'csv'];
  const chosen = new Set(['duckduckgo']);
  sources.forEach((s) => {
    const chip = h('button', { class: `chip ${chosen.has(s) ? 'on' : ''}` }, s === 'demo' ? 'demo (offline sample data)' : s);
    chip.addEventListener('click', () => {
      if (chosen.has(s)) { chosen.delete(s); chip.classList.remove('on'); }
      else { chosen.add(s); chip.classList.add('on'); }
    });
    sourceBox.appendChild(chip);
  });
  const maxResults = h('input', { type: 'number', value: '60', min: '1', max: '1000' });
  const csvArea = h('textarea', { placeholder: 'business_name,email,city,category\nAcme Roofing,owner@acme.com,Phoenix,Roofing' });
  const jobStatus = h('div', { class: 'muted', style: 'font-size:12.5px' });
  const scrapeBtn = h('button', { class: 'btn' }, 'Start scraping');

  scrapeBtn.addEventListener('click', async () => {
    if (chosen.size === 0) return toast('Pick at least one source', 'err');
    scrapeBtn.disabled = true;
    jobStatus.innerHTML = '<span class="spinner"></span> starting…';
    try {
      const res = await API.post(`/api/campaigns/${State.campaignId}/scrape`, {
        sources: Array.from(chosen),
        max_results: Number(maxResults.value),
        csv_text: csvArea.value,
        sync: false,
      });
      State.scrapeJobId = res.job.jobId;
      pollScrape(jobStatus, scrapeBtn);
    } catch (err) {
      toast(err.message, 'err');
      scrapeBtn.disabled = false;
      jobStatus.textContent = '';
    }
  });

  wrap.appendChild(h('div', { class: 'card' }, [
    h('h2', {}, 'Find leads'),
    h('p', { class: 'muted' }, `Queries are built from your offer and targeting: ${detail.campaign.geoSummary || 'everything'}`),
    detail.campaign.geoProblems && detail.campaign.geoProblems.length
      ? h('div', { class: 'pill pill-bad', style: 'margin-bottom:8px' }, detail.campaign.geoProblems.join('; '))
      : null,
    h('label', {}, 'Sources'),
    sourceBox,
    h('div', { class: 'row', style: 'margin-top:8px' }, [
      h('div', { style: 'max-width:180px' }, [h('label', {}, 'Max results'), maxResults]),
    ]),
    h('label', {}, 'Or paste a CSV list'),
    csvArea,
    h('div', { class: 'btn-row' }, [scrapeBtn, jobStatus]),
  ]));

  /* --------------------------------------------------------- leads table */
  const search = h('input', { type: 'text', placeholder: 'Filter by name, email, city…' });
  const statusFilter = h('select', {}, [h('option', { value: '' }, 'All statuses'),
    ...['new', 'queued', 'sent', 'replied', 'failed', 'skipped', 'excluded'].map((s) => h('option', { value: s }, s))]);
  const tableHost = h('div', {});
  const selectAll = h('input', { type: 'checkbox' });

  async function reload() {
    const params = new URLSearchParams();
    if (search.value) params.set('q', search.value);
    if (statusFilter.value) params.set('status', statusFilter.value);
    params.set('limit', '300');
    const data = await API.get(`/api/campaigns/${State.campaignId}/leads?${params.toString()}`);
    renderTable(tableHost, data.leads, selectAll, reload);
  }
  search.addEventListener('input', debounce(reload, 300));
  statusFilter.addEventListener('change', reload);

  const bulk = async (action) => {
    try {
      const ids = $$('#app tbody input.lead-check:checked').map((i) => Number(i.dataset.id));
      const res = await API.post(`/api/campaigns/${State.campaignId}/leads/bulk`, {
        lead_ids: ids, action,
      });
      toast(`${action}: ${res.affected} leads`, 'ok');
      reload();
    } catch (err) { toast(err.message, 'err'); }
  };

  selectAll.addEventListener('change', () => {
    $$('#app tbody input.lead-check').forEach((i) => { i.checked = selectAll.checked; });
    $$('#app tbody tr').forEach((tr) => tr.classList.toggle('selected', selectAll.checked));
  });

  wrap.appendChild(h('div', { class: 'card' }, [
    h('div', { class: 'card-head' }, [
      h('div', {}, [h('h2', {}, 'Lead review dashboard'), h('p', { class: 'muted' }, 'Audited before anything is sent. Unselected leads are never contacted.')]),
      h('div', { class: 'row', style: 'max-width:420px' }, [search, statusFilter]),
    ]),
    h('div', { class: 'btn-row' }, [
      h('button', { class: 'btn small ghost', onclick: () => { selectAll.checked = true; selectAll.dispatchEvent(new Event('change')); } }, 'Select all'),
      h('button', { class: 'btn small ghost', onclick: () => { selectAll.checked = false; selectAll.dispatchEvent(new Event('change')); } }, 'Clear selection'),
      h('button', { class: 'btn small', onclick: () => bulk('select') }, 'Select checked'),
      h('button', { class: 'btn small ghost', onclick: () => bulk('exclude') }, 'Exclude checked'),
      h('button', { class: 'btn small danger', onclick: () => bulk('delete') }, 'Delete checked'),
      h('a', { class: 'btn small ghost', href: `/api/campaigns/${State.campaignId}/leads-export.csv` }, 'Export CSV'),
    ]),
    tableHost,
  ]));
  await reload();
  return wrap;
};

function renderTable(host, leads, selectAll, reload) {
  host.innerHTML = '';
  if (!leads.length) {
    host.appendChild(emptyState('No leads yet — run a scrape or paste a CSV above.'));
    return;
  }
  const rows = leads.map((lead) => h('tr', { class: lead.selected ? 'selected' : '' }, [
    h('td', { class: 'cell-check' }, (() => {
      const box = h('input', {
        type: 'checkbox', class: 'lead-check', 'data-id': String(lead.id),
        'aria-label': `Select ${lead.business_name || 'lead'}`,
      });
      box.checked = lead.selected;
      box.addEventListener('change', () => box.closest('tr').classList.toggle('selected', box.checked));
      return box;
    })()),
    h('td', { 'data-label': 'Business' }, [
      h('a', { href: '#', onclick: (e) => { e.preventDefault(); openLead(lead.id); } }, lead.business_name || '(unnamed)'),
      h('span', { class: 'sub' }, [lead.category || '—', lead.contact_name ? ` · ${lead.contact_name}` : ''].join('')),
    ]),
    h('td', { 'data-label': 'Email' }, [lead.email || h('span', { class: 'muted' }, 'no email'),
      lead.website ? h('span', { class: 'sub' }, lead.website.replace(/^https?:\/\//, '')) : null]),
    h('td', { 'data-label': 'Location' }, [lead.city || '—', h('span', { class: 'sub' }, [lead.state, lead.country].filter(Boolean).join(', '))]),
    h('td', { class: 'score', 'data-label': 'Score' }, [
      String(lead.score),
      lead.rating ? h('span', { class: 'sub' }, `★ ${lead.rating}${lead.review_count ? ` (${lead.review_count})` : ''}`) : null,
    ]),
    h('td', { 'data-label': 'Status' }, pill(lead.status, lead.status === 'replied' ? 'good' : lead.status === 'sent' ? 'accent' : lead.status === 'failed' ? 'bad' : 'idle')),
    h('td', { 'data-label': '' }, h('button', {
      class: 'btn small ghost',
      onclick: async () => {
        const next = !lead.selected;
        await API.patch(`/api/campaigns/${lead.campaign_id}/leads/${lead.id}`, { selected: next });
        reload();
      },
    }, lead.selected ? 'Deselect' : 'Select')),
  ]));
  host.appendChild(h('div', { class: 'table-wrap' }, [
    h('table', { class: 'responsive' }, [
      h('thead', {}, h('tr', {}, [
        h('th', {}, selectAll), h('th', {}, 'Business'), h('th', {}, 'Email'),
        h('th', {}, 'Location'), h('th', {}, 'Score'), h('th', {}, 'Status'), h('th', {}, ''),
      ])),
      h('tbody', {}, rows),
    ]),
  ]));
}

async function openLead(leadId) {
  const data = await API.get(`/api/crm/leads/${leadId}`);
  const lead = data.lead;
  const body = h('div', {}, [
    h('div', { class: 'kv' }, [h('span', { class: 'k' }, 'Email'), h('span', {}, lead.email || '—')]),
    h('div', { class: 'kv' }, [h('span', { class: 'k' }, 'Phone'), h('span', {}, lead.phone || '—')]),
    h('div', { class: 'kv' }, [h('span', { class: 'k' }, 'Website'), h('span', {}, lead.website || '—')]),
    h('div', { class: 'kv' }, [h('span', { class: 'k' }, 'Location'), h('span', {}, [lead.city, lead.state, lead.country].filter(Boolean).join(', ') || '—')]),
    h('div', { class: 'kv' }, [h('span', { class: 'k' }, 'Source'), h('span', {}, `${lead.source} · score ${lead.score}`)]),
    lead.snippet ? h('p', { class: 'muted', style: 'font-size:12.5px' }, lead.snippet) : null,
    h('h3', {}, 'Outbound'),
    data.messages.length
      ? h('div', { class: 'list' }, data.messages.map((m) => h('div', { class: 'list-item' }, [
          h('div', {}, [h('div', {}, m.subject), h('span', { class: 'sub muted', style: 'font-size:11.5px' }, `${m.status} · ${fmtDate(m.sent_at)} · delay ${m.delay_seconds}s · compliance ${m.compliance_score}`)]),
        ])))
      : emptyState('Nothing sent yet'),
    h('h3', {}, 'Replies'),
    data.replies.length
      ? h('div', { class: 'list' }, data.replies.map((r) => h('div', { class: 'reply-item' }, [
          h('div', {}, [h('strong', {}, `${r.from_name || r.from_email}`), ` ${pill(...INTENT_LABEL[r.intent] || ['?', 'idle'])}`]),
          h('div', { class: 'sub muted', style: 'font-size:11.5px' }, fmtDate(r.received_at)),
          h('p', { style: 'font-size:12.5px' }, r.snippet),
        ])))
      : emptyState('No replies yet'),
    h('h3', {}, 'Activity'),
    h('div', { class: 'list' }, data.activities.slice(0, 12).map((a) => h('div', { class: 'list-item' }, [
      h('div', {}, [h('div', {}, a.kind), h('span', { class: 'sub muted', style: 'font-size:11.5px' }, a.note || JSON.stringify(a.payload))]),
      h('span', { class: 'muted', style: 'font-size:11.5px' }, fmtDate(a.created_at)),
    ]))),
  ]);
  const noteInput = h('textarea', { placeholder: 'Add a follow-up note…' });
  body.appendChild(h('h3', {}, 'Add note'));
  body.appendChild(noteInput);
  openModal(lead.business_name || 'Lead', body, [
    h('button', {
      class: 'btn',
      onclick: async () => {
        if (!noteInput.value.trim()) return;
        await API.post(`/api/crm/leads/${leadId}/notes`, { note: noteInput.value.trim() });
        toast('Note added', 'ok');
        closeModal();
      },
    }, 'Save note'),
  ]);
}

function pollScrape(statusEl, btn) {
  if (State.scrapeTimer) clearInterval(State.scrapeTimer);
  State.scrapeTimer = setInterval(async () => {
    try {
      const { job } = await API.get(`/api/campaigns/scrape-jobs/${State.scrapeJobId}`);
      statusEl.textContent = `${job.message} — ${job.resultCount} found (${Math.round(job.progress * 100)}%)`;
      if (job.status === 'done' || job.status === 'error') {
        clearInterval(State.scrapeTimer);
        btn.disabled = false;
        try {
          const saved = await API.post(`/api/campaigns/scrape-jobs/${State.scrapeJobId}/save`, null);
          toast(`Saved ${saved.saved.created} new leads (${saved.saved.duplicates} duplicates skipped)`, saved.saved.created ? 'ok' : '');
          go('leads');
        } catch (err) { toast(err.message, 'err'); }
      }
    } catch (err) {
      clearInterval(State.scrapeTimer);
      btn.disabled = false;
      statusEl.textContent = err.message;
    }
  }, 1800);
}

/* =============================================================== compose */
Views.compose = async () => {
  const wrap = h('div', { class: 'view' });
  const { campaigns } = await API.get('/api/campaigns');
  if (!State.campaignId && campaigns.length) State.campaignId = campaigns[0].id;
  if (!State.campaignId) return h('div', { class: 'view' }, [emptyState('Create a campaign first.')]);

  const previewHost = h('div', {});
  const useLlm = h('input', { type: 'checkbox' });
  const runBtn = h('button', { class: 'btn' }, 'Generate previews');
  runBtn.addEventListener('click', async () => {
    runBtn.disabled = true;
    previewHost.innerHTML = '<span class="spinner"></span>';
    try {
      const data = await API.post(`/api/campaigns/${State.campaignId}/preview`, {
        campaign_id: State.campaignId, offers: State.offer, prefer_llm: useLlm.checked, limit: 3,
      });
      previewHost.innerHTML = '';
      // for..of, not forEach: the callback is async.
      for (const p of data.previews) {
        const report = await API.post('/api/campaigns/compliance-check', {
          subject: p.subject, body_text: p.bodyText, body_html: p.bodyHtml,
        });
        previewHost.appendChild(h('div', { class: 'card' }, [
          h('div', { class: 'card-head' }, [
            h('div', {}, [h('h2', {}, p.subject), h('p', { class: 'muted' }, `to ${p.email} · template ${p.templateKey} · engine ${p.source}`)]),
            pill(`compliance ${report.score}`, report.blocked ? 'bad' : report.score >= 85 ? 'good' : 'pause'),
          ]),
          h('div', { class: 'email-preview' }, [
            h('div', { class: 'meta' }, `Subject: ${p.subject}`),
            h('div', { html: p.bodyHtml || `<pre>${p.bodyText}</pre>` }),
          ]),
          report.issues.length
            ? h('div', { style: 'margin-top:10px' }, report.issues.map((i) => h('div', { class: 'kv' }, [
                pill(i.severity, i.severity === 'block' ? 'bad' : 'pause'), h('span', {}, i.message),
              ])))
            : h('p', { class: 'muted', style: 'margin-top:10px' }, 'No compliance issues detected.'),
        ]));
      }
    } catch (err) {
      previewHost.innerHTML = '';
      previewHost.appendChild(h('div', { class: 'card' }, [emptyState(err.message)]));
    }
    runBtn.disabled = false;
  });

  const { settings } = await API.get('/api/system/settings');
  wrap.appendChild(h('div', { class: 'card' }, [
    h('h2', {}, 'Personalised copy'),
    h('p', { class: 'muted' }, 'Each lead gets its own variant — seeded by lead id, so it is reproducible and auditable.'),
    h('label', { class: 'checkbox' }, [useLlm, `Use the AI engine (${settings.llm_provider}${settings.llm_configured ? '' : ' — not configured, offline templates will be used'})`]),
    h('div', { class: 'btn-row' }, [runBtn]),
  ]));
  wrap.appendChild(previewHost);
  return wrap;
};

/* ============================================================== dispatch */
Views.dispatch = async () => {
  const wrap = h('div', { class: 'view' });
  const { campaigns } = await API.get('/api/campaigns');
  if (!State.campaignId && campaigns.length) State.campaignId = campaigns[0].id;
  if (!State.campaignId) return h('div', { class: 'view' }, [emptyState('Create a campaign first.')]);

  const posture = await API.get('/api/system/compliance-posture');
  const planHost = h('div', {});
  const logHost = h('div', { class: 'log' });
  const stateHost = h('div', {});

  const planBtn = h('button', {
    class: 'btn ghost',
    onclick: async () => {
      planHost.innerHTML = '<span class="spinner"></span>';
      const data = await API.post(`/api/campaigns/${State.campaignId}/plan-preview`);
      planHost.innerHTML = '';
      planHost.appendChild(h('div', { class: 'card' }, [
        h('h2', {}, 'Send plan (randomised, unordered)'),
        h('p', { class: 'muted' }, `${data.plan.scheduled} scheduled of ${data.plan.total} selected · deferred ${data.plan.deferred} to the next day · mean gap ${data.plan.meanGapSeconds}s · total span ${data.plan.spanMinutes} min`),
        h('div', { class: 'table-wrap', style: 'max-height:280px;overflow:auto' }, [
          h('table', {}, [
            h('thead', {}, h('tr', {}, [h('th', {}, '#'), h('th', {}, 'Lead id'), h('th', {}, 'Gap'), h('th', {}, 'Send at'), h('th', {}, 'Type')])),
            h('tbody', {}, data.plan.slots.slice(0, 40).map((s, i) => h('tr', {}, [
              h('td', {}, String(i + 1)), h('td', {}, String(s.leadId)),
              h('td', { class: 'score' }, `${s.delaySeconds}s`),
              h('td', {}, fmtDate(s.sendAt)),
              h('td', {}, s.longPause ? pill('long break', 'pause') : pill('humanised gap', 'idle')),
            ]))),
          ]),
        ]),
        data.problems.length ? h('div', { class: 'pill pill-bad', style: 'margin-top:8px' }, data.problems.join('; ')) : null,
      ]));
    },
  }, 'Preview the send plan');

  const dryBtn = h('button', { class: 'btn ghost' }, 'Dry run (no email sent)');
  const startBtn = h('button', { class: 'btn' }, 'Start dispatch');
  const pauseBtn = h('button', { class: 'btn ghost' }, 'Pause');
  const resumeBtn = h('button', { class: 'btn ghost' }, 'Resume');
  const stopBtn = h('button', { class: 'btn danger' }, 'Stop');

  const dispatchCall = async (path, body) => {
    try {
      const res = await API.post(path, body);
      toast(res.detail || 'ok', res.ok ? 'ok' : 'err');
      refreshState();
    } catch (err) { toast(err.message, 'err'); }
  };
  dryBtn.addEventListener('click', () => dispatchCall('/api/campaigns/dispatch/start', { campaign_id: State.campaignId, dry_run: true, prepare: true }));
  startBtn.addEventListener('click', () => dispatchCall('/api/campaigns/dispatch/start', { campaign_id: State.campaignId, dry_run: false, prepare: true }));
  pauseBtn.addEventListener('click', () => dispatchCall('/api/campaigns/dispatch/pause'));
  resumeBtn.addEventListener('click', () => dispatchCall('/api/campaigns/dispatch/resume'));
  stopBtn.addEventListener('click', () => dispatchCall('/api/campaigns/dispatch/stop'));

  wrap.appendChild(h('div', { class: 'card' }, [
    h('div', { class: 'card-head' }, [
      h('div', {}, [h('h2', {}, 'Dispatch engine'), h('p', { class: 'muted' }, 'Randomised gaps + long breaks, quota checks before every send.')]),
      stateHost,
    ]),
    h('div', { class: 'btn-row' }, [planBtn, dryBtn, startBtn, pauseBtn, resumeBtn, stopBtn]),
    logHost,
  ]));
  wrap.appendChild(planHost);

  wrap.appendChild(h('div', { class: 'card' }, [
    h('h2', {}, 'Live guardrails'),
    h('div', { class: 'grid cols-3' }, [
      statCard('Sent today', posture.caps.daily.used, `cap ${posture.caps.daily.limit}`),
      statCard('Sent this hour', posture.caps.hourly.used, `cap ${posture.caps.hourly.limit}`),
      statCard('Suppressed', posture.caps.suppressionList, 'never contacted again'),
    ]),
    h('div', { class: 'list', style: 'margin-top:10px' }, posture.checks.map((c) => h('div', { class: 'list-item' }, [
      h('div', {}, [h('div', {}, c.label), h('div', { class: 'sub muted', style: 'font-size:11.5px' }, String(c.value))]),
      pill(c.active ? 'active' : 'off', c.active ? 'good' : 'idle'),
    ]))),
  ]));

  async function refreshState() {
    const { state } = await API.get('/api/campaigns/dispatch/state');
    stateHost.innerHTML = '';
    stateHost.appendChild(pill(
      state.running ? (state.paused ? 'paused' : 'running') : 'idle',
      state.running ? (state.paused ? 'pause' : 'run') : 'idle',
    ));
    logHost.innerHTML = '';
    if (state.total) {
      logHost.appendChild(h('div', {}, [
        h('div', {}, `${state.campaignName || ''} — sent ${state.sent}, failed ${state.failed}, skipped ${state.skipped}, remaining ${state.remaining}`),
        h('div', {}, state.message),
        h('div', {}, `today ${state.sentToday}/${state.dailyCap} · this hour ${state.sentThisHour} · current gap ${state.currentDelaySeconds}s · next ${fmtDate(state.nextSendAt)}`),
      ]));
      (state.recent || []).slice(-12).reverse().forEach((r) => {
        logHost.appendChild(h('div', {}, `${fmtDate(r.at)} ${r.status.toUpperCase()} ${r.email} ${r.error || ''} ${r.delay ? `(+${r.delay}s)` : ''}`));
      });
      (state.errors || []).slice(-6).forEach((e) => logHost.appendChild(h('div', { style: 'color:#ffb3ad' }, `! ${e}`)));
    } else {
      logHost.appendChild(h('div', { class: 'muted' }, 'Nothing dispatched yet.'));
    }
  }
  await refreshState();
  if (State.dispatchTimer) clearInterval(State.dispatchTimer);
  State.dispatchTimer = setInterval(async () => {
    if (!document.querySelector('[data-active-view="dispatch"]')) { clearInterval(State.dispatchTimer); return; }
    await refreshState();
  }, 2500);
  return wrap;
};

/* =================================================================== crm */
Views.crm = async () => {
  const wrap = h('div', { class: 'view' });
  const [overview, repliesRes, pipelineRes] = await Promise.all([
    API.get('/api/crm/overview'),
    API.get('/api/crm/replies?limit=40'),
    API.get('/api/crm/pipeline'),
  ]);

  wrap.appendChild(h('div', { class: 'grid cols-4' }, [
    statCard('Replies', overview.totals.replies, `${overview.totals.unread} unread`),
    statCard('Interested', overview.totals.interested, `interest rate ${pct(overview.totals.interestRate)}`),
    statCard('Reply rate', pct(overview.totals.replyRate), `${overview.totals.sent} sent`),
    statCard('In pipeline', Object.values(overview.stages).reduce((a, b) => a + b, 0), 'tracked leads'),
  ]));

  const syncBtn = h('button', {
    class: 'btn ghost',
    onclick: async () => {
      syncBtn.disabled = true;
      syncBtn.innerHTML = '<span class="spinner"></span>';
      try {
        const res = await API.post('/api/crm/sync');
        const detail = res.results.map((r) => `${r.email || r.accountId}: ${r.ok ? `${r.replies} replies` : r.error}`).join(' · ');
        toast(detail, res.results.some((r) => r.ok) ? 'ok' : 'err');
        go('crm');
      } catch (err) { toast(err.message, 'err'); }
      syncBtn.disabled = false;
      syncBtn.textContent = 'Sync inbox now';
    },
  }, 'Sync inbox now');

  const intentFilter = h('select', {}, [h('option', { value: '' }, 'All intents'),
    ...Object.keys(INTENT_LABEL).map((k) => h('option', { value: k }, INTENT_LABEL[k][0]))]);

  const replyList = h('div', { class: 'list' });
  function renderReplies(replies) {
    replyList.innerHTML = '';
    if (!replies.length) {
      replyList.appendChild(emptyState('No replies yet. Sync runs against every active account\'s IMAP inbox.'));
      return;
    }
    replies.forEach((r) => {
      const [label, kind] = INTENT_LABEL[r.intent] || ['?', 'idle'];
      replyList.appendChild(h('div', { class: `reply-item ${r.is_read ? '' : 'unread'}` }, [
        h('div', {}, [
          h('div', {}, [
            h('strong', {}, r.from_name || r.from_email),
            ` `, pill(label, kind), ` `, r.lead ? pill(r.lead.businessName, 'accent') : pill('unmatched', 'idle'),
          ]),
          h('div', { class: 'sub muted', style: 'font-size:11.5px' }, `${r.subject} · ${fmtDate(r.received_at)} · matched by ${r.matched_by}`),
          h('p', { style: 'font-size:12.5px;margin:6px 0' }, r.snippet),
        ]),
        h('div', { class: 'btn-row', style: 'margin-top:6px' }, [
          h('button', {
            class: 'btn small ghost',
            onclick: async () => { await API.post(`/api/crm/replies/${r.id}/read?read=true`); go('crm'); },
          }, r.is_read ? 'Read' : 'Mark read'),
          r.lead ? h('button', { class: 'btn small ghost', onclick: () => openLead(r.lead.id) }, 'Open lead') : null,
          r.lead ? h('select', {
            onchange: async (e) => {
              await API.post(`/api/crm/leads/${r.lead.id}/stage`, { pipeline_stage: e.target.value });
              toast('Stage updated', 'ok');
            },
          }, (overview.stageOrder || []).map((s) => h('option', {
            value: s, selected: r.lead.pipelineStage === s ? 'selected' : null,
          }, STAGE_LABEL[s] || s))) : null,
        ]),
      ]));
    });
  }
  renderReplies(repliesRes.replies);
  intentFilter.addEventListener('change', async () => {
    const data = await API.get(`/api/crm/replies?limit=40${intentFilter.value ? `&intent=${intentFilter.value}` : ''}`);
    renderReplies(data.replies);
  });

  wrap.appendChild(h('div', { class: 'card' }, [
    h('div', { class: 'card-head' }, [
      h('div', {}, [h('h2', {}, 'Inbox & reply detection'), h('p', { class: 'muted' }, 'Matched by Message-ID reference, then subject, then sender address.')]),
      h('div', { class: 'row', style: 'max-width:340px' }, [intentFilter, syncBtn]),
    ]),
    replyList,
  ]));

  const board = h('div', { class: 'board' });
  (pipelineRes.stageOrder || []).forEach((stage) => {
    const items = pipelineRes.board[stage] || [];
    board.appendChild(h('div', { class: 'board-col' }, [
      h('h4', {}, [STAGE_LABEL[stage] || stage, h('span', {}, String(items.length))]),
      ...items.slice(0, 12).map((lead) => h('div', { class: 'board-card' }, [
        h('div', {}, [h('a', { href: '#', onclick: (e) => { e.preventDefault(); openLead(lead.id); } }, lead.business_name || lead.email)]),
        h('div', { class: 'sub' }, lead.email),
        h('div', { class: 'sub' }, `${lead.city || '—'} · ${lead.status}`),
      ])),
      items.length > 12 ? h('div', { class: 'sub muted', style: 'font-size:11px' }, `+${items.length - 12} more`) : null,
    ]));
  });
  wrap.appendChild(h('div', { class: 'card' }, [h('h2', {}, 'Pipeline'), h('p', { class: 'muted' }, 'Move a lead from automated outreach to manual follow-up.'), board]));

  const { suppressions } = await API.get('/api/crm/suppressions');
  const supInput = h('input', { type: 'email', placeholder: 'someone@company.com' });
  wrap.appendChild(h('div', { class: 'card' }, [
    h('h2', {}, 'Suppression list'),
    h('p', { class: 'muted' }, 'Anyone who opts out is blocked across every campaign, permanently.'),
    h('div', { class: 'row' }, [
      supInput,
      h('button', {
        class: 'btn',
        onclick: async () => {
          if (!supInput.value) return;
          try {
            await API.post('/api/crm/suppressions', { email: supInput.value, reason: 'manual' });
            toast('Suppressed', 'ok');
            go('crm');
          } catch (err) { toast(err.message, 'err'); }
        },
      }, 'Add'),
    ]),
    suppressions.length
      ? h('div', { class: 'list', style: 'margin-top:10px' }, suppressions.map((s) => h('div', { class: 'list-item' }, [
          h('div', {}, [h('div', {}, s.email), h('span', { class: 'sub muted', style: 'font-size:11.5px' }, `${s.reason} · ${fmtDate(s.created_at)}`)]),
          h('button', {
            class: 'btn small ghost',
            onclick: async () => { await API.del(`/api/crm/suppressions/${s.id}`); go('crm'); },
          }, 'Remove'),
        ])))
      : emptyState('Empty — good.'),
  ]));
  return wrap;
};

/* ============================================================== accounts */
Views.accounts = async () => {
  const wrap = h('div', { class: 'view' });
  const { accounts, presets } = await API.get('/api/accounts');

  const email = h('input', { type: 'email', placeholder: 'you@company.com' });
  const displayName = h('input', { type: 'text', placeholder: 'Your name' });
  const password = h('input', { type: 'password', placeholder: 'App password (not your normal password)' });
  const provider = h('select', {}, Object.keys(presets).map((p) => h('option', { value: p }, p)));
  const smtpHost = h('input', { type: 'text' });
  const smtpPort = h('input', { type: 'number', value: '587' });
  const imapHost = h('input', { type: 'text' });
  const imapPort = h('input', { type: 'number', value: '993' });
  const note = h('p', { class: 'help' }, presets.gmail.note);
  provider.addEventListener('change', () => {
    const p = presets[provider.value] || presets.custom;
    smtpHost.value = p.smtp_host; smtpPort.value = String(p.smtp_port);
    imapHost.value = p.imap_host; imapPort.value = String(p.imap_port);
    note.textContent = p.note;
  });
  smtpHost.value = presets.gmail.smtp_host;
  imapHost.value = presets.gmail.imap_host;

  wrap.appendChild(h('div', { class: 'card' }, [
    h('h2', {}, 'Add a sending account'),
    h('p', { class: 'muted' }, 'Credentials are encrypted with a local key and never leave this machine. Gmail requires an App Password with 2FA enabled.'),
    h('div', { class: 'row' }, [
      h('div', {}, [h('label', {}, 'Email address'), email]),
      h('div', {}, [h('label', {}, 'Display name'), displayName]),
    ]),
    h('div', { class: 'row' }, [
      h('div', {}, [h('label', {}, 'Provider'), provider]),
      h('div', {}, [h('label', {}, 'App password'), password]),
    ]),
    h('div', { class: 'row' }, [
      h('div', {}, [h('label', {}, 'SMTP host'), smtpHost]),
      h('div', { style: 'max-width:110px' }, [h('label', {}, 'Port'), smtpPort]),
      h('div', {}, [h('label', {}, 'IMAP host'), imapHost]),
      h('div', { style: 'max-width:110px' }, [h('label', {}, 'Port'), imapPort]),
    ]),
    note,
    h('div', { class: 'btn-row' }, [
      h('button', {
        class: 'btn',
        onclick: async () => {
          if (!email.value) return toast('Email is required', 'err');
          try {
            const res = await API.post('/api/accounts', {
              email: email.value, display_name: displayName.value, provider: provider.value,
              password: password.value, smtp_host: smtpHost.value, smtp_port: Number(smtpPort.value),
              imap_host: imapHost.value, imap_port: Number(imapPort.value),
            });
            toast(`Account added (id ${res.account.id})`, 'ok');
            go('accounts');
          } catch (err) { toast(err.message, 'err'); }
        },
      }, 'Save account'),
    ]),
  ]));

  wrap.appendChild(h('div', { class: 'card' }, [
    h('h2', {}, 'Configured accounts'),
    accounts.length === 0 ? emptyState('No sending accounts yet.') : h('div', { class: 'table-wrap' }, [
      h('table', {}, [
        h('thead', {}, h('tr', {}, [
          h('th', {}, 'Address'), h('th', {}, 'Provider'), h('th', {}, 'Status'),
          h('th', {}, 'Today'), h('th', {}, 'Limits'), h('th', {}, ''),
        ])),
        h('tbody', {}, accounts.map((a) => h('tr', {}, [
          h('td', {}, [a.email, h('span', { class: 'sub' }, a.display_name)]),
          h('td', {}, a.provider),
          h('td', {}, pill(a.is_verified ? 'verified' : 'unverified', a.is_verified ? 'good' : 'pause')),
          h('td', { class: 'score' }, `${a.sentToday}/${a.daily_limit}`),
          h('td', {}, [h('span', { class: 'sub' }, `${a.smtp_host}:${a.smtp_port}`), h('span', { class: 'sub' }, `imap ${a.imap_host}:${a.imap_port}`)]),
          h('td', {}, [
            h('button', {
              class: 'btn small ghost',
              onclick: async () => {
                try {
                  const res = await API.post(`/api/accounts/${a.id}/test`, { smtp: true, imap: true });
                  toast(res.verified ? 'Connection OK' : Object.entries(res.results).map(([k, v]) => `${k}: ${v.detail}`).join(' | '), res.verified ? 'ok' : 'err');
                  go('accounts');
                } catch (err) { toast(err.message, 'err'); }
              },
            }, 'Test'),
            ' ',
            h('button', {
              class: 'btn small danger',
              onclick: async () => { await API.del(`/api/accounts/${a.id}`); go('accounts'); },
            }, 'Delete'),
          ]),
        ]))),
      ]),
    ]),
  ]));
  return wrap;
};

/* ============================================================= settings */
Views.settings = async () => {
  const wrap = h('div', { class: 'view' });
  const { settings } = await API.get('/api/system/settings');
  State.settings = settings;

  const bizName = h('input', { type: 'text', value: settings.business_name || '' });
  const bizAddress = h('input', { type: 'text', value: settings.business_mailing_address || '', placeholder: '480 Commerce Ave, Suite 210, Phoenix, AZ 85004' });
  const unsubUrl = h('input', { type: 'text', value: settings.unsubscribe_url || '', placeholder: 'https://yourdomain.com/unsubscribe' });
  const dailyCap = h('input', { type: 'number', value: String(settings.daily_recipient_cap) });
  const hourlyCap = h('input', { type: 'number', value: String(settings.hourly_recipient_cap) });
  const minDelay = h('input', { type: 'number', value: String(settings.min_delay_seconds) });
  const maxDelay = h('input', { type: 'number', value: String(settings.max_delay_seconds) });
  const quietOn = h('input', { type: 'checkbox' });
  quietOn.checked = settings.enforce_quiet_hours;
  const quietStart = h('input', { type: 'number', value: String(settings.quiet_hours_start), min: '0', max: '23' });
  const quietEnd = h('input', { type: 'number', value: String(settings.quiet_hours_end), min: '0', max: '23' });

  wrap.appendChild(h('div', { class: 'card' }, [
    h('h2', {}, 'Sender identity & legal footer'),
    h('p', { class: 'muted' }, 'CAN-SPAM requires a valid postal address and a working opt-out in every commercial email. Emails that lack either are blocked before sending.'),
    h('div', { class: 'row' }, [
      h('div', {}, [h('label', {}, 'Business name'), bizName]),
      h('div', {}, [h('label', {}, 'Unsubscribe URL (optional)'), unsubUrl]),
    ]),
    h('label', {}, 'Postal mailing address'),
    bizAddress,
  ]));

  wrap.appendChild(h('div', { class: 'card' }, [
    h('h2', {}, 'Anti-spam pacing'),
    h('p', { class: 'muted' }, `Google free accounts stop at 500 recipients/day. The default cap of ${settings.daily_recipient_cap} keeps headroom.`),
    h('div', { class: 'row' }, [
      h('div', {}, [h('label', {}, 'Daily recipient cap'), dailyCap]),
      h('div', {}, [h('label', {}, 'Hourly recipient cap'), hourlyCap]),
      h('div', {}, [h('label', {}, 'Min delay (s)'), minDelay]),
      h('div', {}, [h('label', {}, 'Max delay (s)'), maxDelay]),
    ]),
    h('label', { class: 'checkbox' }, [quietOn, 'Enforce quiet hours']),
    h('div', { class: 'row' }, [
      h('div', { style: 'max-width:140px' }, [h('label', {}, 'Quiet start (h)'), quietStart]),
      h('div', { style: 'max-width:140px' }, [h('label', {}, 'Quiet end (h)'), quietEnd]),
    ]),
  ]));

  const llmProvider = h('select', {}, ['offline', 'openai', 'openai_compatible', 'anthropic'].map((p) => h('option', {
    value: p, selected: settings.llm_provider === p ? 'selected' : null,
  }, p)));
  const llmModel = h('input', { type: 'text', value: settings.llm_model });
  const llmKey = h('input', { type: 'password', placeholder: settings.llm_configured ? 'configured' : 'sk-…' });
  const llmBase = h('input', { type: 'text', value: 'https://api.openai.com/v1' });
  // Write-only: the API reports whether a key is configured, never the key.
  const placesKey = h('input', {
    type: 'password',
    placeholder: settings.google_places_configured ? 'configured' : 'AIza… (optional)',
  });

  wrap.appendChild(h('div', { class: 'card' }, [
    h('h2', {}, 'AI copy engine'),
    h('p', { class: 'muted' }, 'Optional. Without a key the offline template engine writes every email — still personalised, still compliant.'),
    h('div', { class: 'row' }, [
      h('div', {}, [h('label', {}, 'Provider'), llmProvider]),
      h('div', {}, [h('label', {}, 'Model'), llmModel]),
    ]),
    h('div', { class: 'row' }, [
      h('div', {}, [h('label', {}, 'API key'), llmKey]),
      h('div', {}, [h('label', {}, 'Base URL'), llmBase]),
    ]),
  ]));

  wrap.appendChild(h('div', { class: 'card' }, [
    h('h2', {}, 'Lead sources'),
    h('p', { class: 'muted' }, 'DuckDuckGo, CSV and the offline demo need no key. Google Places returns the richest records (name, address, phone, website, rating) and costs money, so it stays off until you add a billing-enabled key.'),
    h('div', { class: 'kv' }, [
      h('span', { class: 'k' }, 'Google Places'),
      h('span', {}, settings.google_places_configured ? 'configured' : 'not configured'),
    ]),
    h('div', { class: 'row' }, [
      h('div', {}, [h('label', {}, 'Google Maps API key'), placesKey]),
    ]),
    h('p', { class: 'help' }, 'Leave blank to keep it disabled. The key is write-only — it is never sent back to the browser.'),
  ]));

  const save = h('button', {
    class: 'btn',
    onclick: async () => {
      const body = {
        business_name: bizName.value,
        business_mailing_address: bizAddress.value,
        unsubscribe_url: unsubUrl.value,
        daily_recipient_cap: Number(dailyCap.value),
        hourly_recipient_cap: Number(hourlyCap.value),
        min_delay_seconds: Number(minDelay.value),
        max_delay_seconds: Number(maxDelay.value),
        enforce_quiet_hours: quietOn.checked,
        quiet_hours_start: Number(quietStart.value),
        quiet_hours_end: Number(quietEnd.value),
        llm_provider: llmProvider.value,
        llm_model: llmModel.value,
        llm_base_url: llmBase.value,
      };
      if (llmKey.value) body.llm_api_key = llmKey.value;
      if (placesKey.value) body.google_maps_api_key = placesKey.value;
      try {
        await API.patch('/api/system/settings', body);
        toast('Settings applied for this session', 'ok');
      } catch (err) { toast(err.message, 'err'); }
    },
  }, 'Save settings');
  wrap.appendChild(h('div', { class: 'card' }, [
    h('h2', {}, 'Persisting changes'),
    h('p', { class: 'muted' }, 'Runtime edits apply immediately but reset on restart. Put the same values in .env (LEADGEN_ prefix) to persist them.'),
    h('div', { class: 'btn-row' }, [save]),
  ]));
  return wrap;
};

/* ------------------------------------------------------------------ misc */
function debounce(fn, ms) {
  let timer;
  return (...args) => { clearTimeout(timer); timer = setTimeout(() => fn(...args), ms); };
}
