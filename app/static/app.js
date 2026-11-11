'use strict';

const TZ = 'Europe/Berlin';
const $ = (id) => document.getElementById(id);
const WD = ['Montag', 'Dienstag', 'Mittwoch', 'Donnerstag', 'Freitag', 'Samstag', 'Sonntag'];
const WD_SHORT = ['Mo', 'Di', 'Mi', 'Do', 'Fr', 'Sa', 'So'];

let ov = null; // last overview from the server
let weekRef = null; // any date inside the shown week (null = current week)
let offset = 0; // server time minus browser time (ms), so the clock follows Berlin server time
let editing = null; // {id, origStart, origEnd, running} while the entry dialog is open
let absenceDay = null;

const timeFmt = new Intl.DateTimeFormat('de-DE', { timeZone: TZ, hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false });

function el(tag, props, ...kids) {
  const e = document.createElement(tag);
  for (const [k, v] of Object.entries(props || {})) {
    if (k === 'class') e.className = v;
    else if (k === 'text') e.textContent = v;
    else if (k.startsWith('on')) e.addEventListener(k.slice(2), v);
    else if (v !== false && v != null) e.setAttribute(k, v === true ? '' : v);
  }
  for (const kid of kids) if (kid != null) e.append(kid);
  return e;
}

async function api(method, path, body) {
  const res = await fetch('/api/v1' + path, {
    method,
    headers: body ? { 'Content-Type': 'application/json' } : {},
    body: body ? JSON.stringify(body) : undefined,
  });
  if (res.status === 401) { window.location.reload(); throw new Error('Bitte anmelden.'); }
  if (res.status === 204) return null;
  let json = null;
  try { json = await res.json(); } catch (e) { /* not JSON */ }
  if (!res.ok) throw new Error(json && json.error ? json.error.message : 'Fehler ' + res.status);
  return json.data;
}

// ---- formatting ------------------------------------------------------------------------------
const pad = (n) => String(n).padStart(2, '0');
function hm(sec) {
  const s = Math.round(Math.abs(sec) / 60);
  return Math.floor(s / 60) + ':' + pad(s % 60);
}
function signed(sec) {
  if (Math.round(Math.abs(sec) / 60) === 0) return '±0:00';
  return (sec < 0 ? '−' : '+') + hm(sec);
}
function hms(sec) {
  const s = Math.max(0, Math.floor(sec));
  return pad(Math.floor(s / 3600)) + ':' + pad(Math.floor(s / 60) % 60) + ':' + pad(s % 60);
}
function dmy(dateStr) {
  const [y, m, d] = dateStr.split('-');
  return d + '.' + m + '.' + (y === String(new Date().getFullYear()) ? '' : y);
}
function clsFor(sec) {
  return Math.round(Math.abs(sec) / 60) === 0 ? '' : sec > 0 ? 'pos' : 'neg';
}
function nowMs() { return Date.now() + offset; }

// ---- rendering -------------------------------------------------------------------------------
function renderStats() {
  const { today, week, balance, vacation } = ov;
  $('s-today').textContent = hm(today.ist);
  $('s-today-sub').textContent = 'von ' + hm(today.soll) + ' Soll';
  $('s-week-label').textContent = 'KW ' + week.week;
  $('s-week').textContent = hm(week.ist);
  const wsub = $('s-week-sub');
  wsub.textContent = 'Soll ' + hm(week.soll) + ' · ' + signed(week.diff);
  const total = $('s-total');
  total.textContent = signed(balance.total);
  total.className = clsFor(balance.total);
  $('s-total-sub').textContent = 'bis gestern ' + signed(balance.before_today);
  $('s-vac').textContent = String(vacation.remaining).replace('.', ',') + ' Tage';
  $('s-vac-sub').textContent = 'von ' + String(vacation.total).replace('.', ',') + ' in ' + vacation.year;
}

function renderClock() {
  const running = ov.running;
  const btn = $('clock-btn');
  btn.disabled = false;
  btn.textContent = running ? 'Beenden' : 'Starten';
  btn.className = 'big ' + (running ? 'stop' : 'start');
  $('status').textContent = running ? 'Läuft seit ' + running.start_local.slice(11) + ' Uhr' : 'Nicht gestempelt';
  tick();
}

function tick() {
  if (!ov) return;
  const d = new Date(nowMs());
  $('now').textContent = timeFmt.format(d);
  if (ov.running) {
    const el_ = hms((nowMs() - Date.parse(ov.running.start)) / 1000);
    $('elapsed').textContent = el_;
    document.title = '● ' + el_ + ' · Zeiterfassung';
  } else {
    $('elapsed').textContent = '';
    document.title = 'Zeiterfassung';
  }
}

function entryTimes(e, day) {
  const f = (local) => (local.slice(0, 10) === day ? '' : dmy(local.slice(0, 10)) + ' ') + local.slice(11);
  return f(e.start_local) + ' – ' + (e.end_local ? f(e.end_local) : 'läuft');
}

function renderDays() {
  const { week } = ov;
  $('week-h').textContent = 'KW ' + week.week + ' · ' + dmy(week.from) + ' – ' + dmy(week.to);
  const root = $('days');
  root.replaceChildren();
  const today = ov.now.date;
  for (const d of week.days) {
    const isFuture = d.date > today;
    const quiet = !d.workday && !d.entries.length && !d.absence;
    const head = el('div', { class: 'day-head' },
      el('span', { class: 'dname', text: WD[d.weekday - 1] }),
      el('span', { class: 'ddate', text: dmy(d.date) }));
    if (d.holiday) head.append(el('span', { class: 'chip', text: d.holiday }));
    if (d.absence) {
      head.append(el('span', { class: 'chip', text: (d.absence.kind === 'urlaub' ? 'Urlaub' : 'Krank') + (d.absence.fraction === 0.5 ? ' (halb)' : '') }));
    }
    if (d.workday || d.entries.length || d.absence) {
      const sums = el('span', { class: 'sums' }, hm(d.ist) + ' / ' + hm(d.soll));
      if (d.counted && (d.soll || d.ist)) sums.append(el('span', { class: 'diff ' + clsFor(d.diff), text: signed(d.diff) }));
      head.append(sums);
    }
    const card = el('article', { class: 'card day' + (d.date === today ? ' today' : '') + (quiet ? ' off' : '') }, head);
    if (d.entries.length) {
      const ul = el('ul', { class: 'entries' });
      for (const e of d.entries) {
        const part = e.from_previous_day || e.into_next_day ? ' (Anteil ' + hm(e.day_seconds) + ')' : '';
        ul.append(el('li', { class: 'entry' },
          el('span', { class: 'times', text: entryTimes(e, d.date) }),
          el('span', { class: 'dur' + (e.running ? ' live' : ''), text: hm(e.duration) + ' h' + part }),
          el('button', { type: 'button', class: 'small', 'aria-label': 'Eintrag bearbeiten ' + entryTimes(e, d.date), onclick: () => openEntry(e, d.date), text: 'Bearbeiten' }),
          el('button', { type: 'button', class: 'small danger', 'aria-label': 'Eintrag löschen ' + entryTimes(e, d.date), onclick: () => removeEntry(e), text: 'Löschen' })));
      }
      card.append(ul);
    }
    if (d.auto_break) {
      card.append(el('p', { class: 'auto-break', text: 'Gesetzliche Pause: ' + Math.round(d.auto_break / 60) + ' min abgezogen (nicht gestempelt)' }));
    }
    if (!isFuture || d.workday) {
      const actions = el('div', { class: 'day-actions' });
      if (!isFuture) actions.append(el('button', { type: 'button', class: 'small', onclick: () => openEntry(null, d.date), text: '+ Eintrag' }));
      if (d.workday) actions.append(el('button', { type: 'button', class: 'small', onclick: () => openAbsence(d), text: d.absence ? 'Abwesenheit ändern' : 'Urlaub / Krank' }));
      card.append(actions);
    }
    root.append(card);
  }
}

function render() {
  renderStats();
  renderClock();
  renderDays();
}

async function refresh() {
  try {
    ov = await api('GET', '/overview' + (weekRef ? '?date=' + weekRef : ''));
    offset = ov.now.ts * 1000 - Date.now();
    render();
  } catch (err) {
    $('status').textContent = 'Fehler: ' + err.message;
  }
}

// ---- clock -----------------------------------------------------------------------------------
$('clock-btn').addEventListener('click', async () => {
  const btn = $('clock-btn');
  const err = $('clock-error');
  btn.disabled = true;
  err.hidden = true;
  try {
    await api('POST', ov.running ? '/clock/stop' : '/clock/start');
  } catch (e) {
    err.textContent = e.message;
    err.hidden = false;
  }
  await refresh();
});

// ---- dialogs ---------------------------------------------------------------------------------
function showError(id, msg) {
  const e = $(id);
  e.textContent = msg || '';
  e.hidden = !msg;
}
document.querySelectorAll('[data-close]').forEach((b) => b.addEventListener('click', () => b.closest('dialog').close()));

function openEntry(entry, day) {
  showError('e-error');
  $('dlg-entry-h').textContent = entry ? 'Eintrag bearbeiten' : 'Eintrag ergänzen';
  editing = entry ? { id: entry.id, origStart: entry.start, origEnd: entry.end, origStartLocal: entry.start_local, origEndLocal: entry.end_local, running: entry.running } : null;
  $('e-start').value = entry ? entry.start_local : day + 'T08:00';
  $('e-end').value = entry ? entry.end_local || '' : day + 'T12:00';
  $('e-end').disabled = !!(entry && entry.running);
  $('e-end').required = !(entry && entry.running);
  $('e-hint').hidden = !(entry && entry.running);
  $('dlg-entry').showModal();
}

$('form-entry').addEventListener('submit', async (ev) => {
  ev.preventDefault();
  const start = $('e-start').value;
  const end = $('e-end').value;
  // Unchanged fields keep their exact offset, so an entry in the repeated autumn hour stays as it was.
  const body = {
    start: editing && start === editing.origStartLocal ? editing.origStart : start,
    end: !end ? null : editing && end === editing.origEndLocal ? editing.origEnd : end,
  };
  try {
    if (editing) await api('PUT', '/entries/' + editing.id, body);
    else await api('POST', '/entries', body);
    $('dlg-entry').close();
    await refresh();
  } catch (e) {
    showError('e-error', e.message);
  }
});

async function removeEntry(e) {
  if (!confirm('Eintrag ' + e.start_local.replace('T', ' ') + (e.running ? ' (läuft noch)' : '') + ' wirklich löschen?')) return;
  try {
    await api('DELETE', '/entries/' + e.id);
  } catch (err) {
    alert(err.message);
  }
  await refresh();
}

function openAbsence(d) {
  showError('a-error');
  absenceDay = d.date;
  $('a-date').textContent = WD[d.weekday - 1] + ', ' + dmy(d.date);
  $('a-kind').value = d.absence ? d.absence.kind : '';
  $('a-fraction').value = d.absence ? String(d.absence.fraction) : '1';
  $('dlg-absence').showModal();
}

$('form-absence').addEventListener('submit', async (ev) => {
  ev.preventDefault();
  const kind = $('a-kind').value;
  try {
    if (kind) await api('PUT', '/absences/' + absenceDay, { kind, fraction: parseFloat($('a-fraction').value) });
    else {
      try { await api('DELETE', '/absences/' + absenceDay); } catch (e) { /* nothing to remove */ }
    }
    $('dlg-absence').close();
    await refresh();
  } catch (e) {
    showError('a-error', e.message);
  }
});

$('btn-settings').addEventListener('click', () => {
  showError('c-error');
  const s = ov.settings;
  $('c-hours').value = s.weekly_hours;
  $('c-vac').value = s.vacation_days;
  const yr = String(ov.vacation.year);
  $('c-vac-year-label').textContent = 'Urlaubstage ' + yr + ' (abweichend)';
  $('c-vac-year').value = s.vacation_overrides[yr] ?? '';
  $('c-start').value = s.start_date || '';
  $('c-balance').value = s.start_balance_hours;
  const box = $('c-days');
  box.replaceChildren();
  WD_SHORT.forEach((name, i) => {
    box.append(el('label', {}, el('input', { type: 'checkbox', value: i + 1, checked: s.workdays.includes(i + 1) }), name));
  });
  $('dlg-settings').showModal();
});

$('form-settings').addEventListener('submit', async (ev) => {
  ev.preventDefault();
  const workdays = [...$('c-days').querySelectorAll('input:checked')].map((i) => Number(i.value));
  const overrides = { ...ov.settings.vacation_overrides };
  const yearDays = $('c-vac-year').value;
  if (yearDays === '') delete overrides[String(ov.vacation.year)];
  else overrides[String(ov.vacation.year)] = parseFloat(yearDays);
  try {
    await api('PUT', '/settings', {
      weekly_hours: parseFloat($('c-hours').value),
      workdays,
      vacation_days: parseFloat($('c-vac').value),
      vacation_overrides: overrides,
      start_date: $('c-start').value || null,
      start_balance_hours: parseFloat($('c-balance').value),
    });
    $('dlg-settings').close();
    await refresh();
  } catch (e) {
    showError('c-error', e.message);
  }
});

// Downloads are buttons, not links, so crawlers (CI) do not try to navigate to the files.
document.querySelectorAll('[data-download]').forEach((b) => b.addEventListener('click', () => { window.location.assign(b.dataset.download); }));

// ---- week navigation, theme, loop --------------------------------------------------------------
function shiftWeek(days) {
  const base = new Date(ov.week.from + 'T12:00:00Z');
  base.setUTCDate(base.getUTCDate() + days);
  weekRef = base.toISOString().slice(0, 10);
  refresh();
}
$('prev').addEventListener('click', () => shiftWeek(-7));
$('next').addEventListener('click', () => shiftWeek(7));
$('this-week').addEventListener('click', () => { weekRef = null; refresh(); });

const THEMES = ['auto', 'light', 'dark'];
const THEME_LABEL = { auto: 'Auto', light: 'Hell', dark: 'Dunkel' };
function currentTheme() { return document.documentElement.dataset.theme || 'auto'; }
function showTheme() { $('btn-theme').textContent = THEME_LABEL[currentTheme()]; }
$('btn-theme').addEventListener('click', () => {
  const next = THEMES[(THEMES.indexOf(currentTheme()) + 1) % THEMES.length];
  if (next === 'auto') delete document.documentElement.dataset.theme;
  else document.documentElement.dataset.theme = next;
  try { next === 'auto' ? localStorage.removeItem('theme') : localStorage.setItem('theme', next); } catch (e) { /* ignore */ }
  showTheme();
});
showTheme();

setInterval(tick, 1000);
setInterval(refresh, 60000);
document.addEventListener('visibilitychange', () => { if (!document.hidden) refresh(); });
refresh();

document.getElementById('btn-logout').addEventListener('click', async () => {
  try { await fetch('/api/v1/auth/logout', { method: 'POST' }); } finally { window.location.assign('/'); }
});
