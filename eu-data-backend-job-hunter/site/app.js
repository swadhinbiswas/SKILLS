/* EU Software, Data & Backend Jobs board — frontend logic.
   Talks only to the local server (/api/*); the Turso token never reaches the browser. */

const state = {
  q: '',
  track: 'all',
  level: 'all',
  country: 'all',
  source: 'all',
  sort: 'new',
  minMatch: '0',
  remote: false,
  reloc: false,
  hideApplied: false,
  page: 1,
  per: 30,
  total: 0,
  pages: 0,
};

const COUNTRY_NAMES = {
  at: 'Austria', be: 'Belgium', bg: 'Bulgaria', hr: 'Croatia', cy: 'Cyprus',
  cz: 'Czechia', dk: 'Denmark', ee: 'Estonia', fi: 'Finland', fr: 'France',
  de: 'Germany', gr: 'Greece', hu: 'Hungary', ie: 'Ireland', is: 'Iceland',
  it: 'Italy', lv: 'Latvia', li: 'Liechtenstein', lt: 'Lithuania', lu: 'Luxembourg',
  mt: 'Malta', nl: 'Netherlands', no: 'Norway', pl: 'Poland', pt: 'Portugal',
  ro: 'Romania', sk: 'Slovakia', si: 'Slovenia', es: 'Spain', se: 'Sweden',
  gb: 'UK', ch: 'Switzerland', us: 'USA',
};

const $ = (sel) => document.querySelector(sel);

async function api(path) {
  const res = await fetch(path);
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}

function esc(s) {
  return String(s == null ? '' : s)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
}

function fmtDate(d) {
  if (!d) return '—';
  const date = d.slice(0, 10);
  if (date === '0000-00-00') return '—';
  const dt = new Date(date + 'T00:00:00');
  if (isNaN(dt)) return d;
  return dt.toLocaleDateString('en-GB', { day: 'numeric', month: 'short', year: 'numeric' });
}

function timeAgo(d) {
  if (!d) return '';
  const date = d.slice(0, 10);
  const days = Math.floor((Date.now() - new Date(date + 'T00:00:00').getTime()) / 86400000);
  if (isNaN(days) || days < 0) return '';
  if (days === 0) return 'today';
  if (days === 1) return 'yesterday';
  if (days < 30) return `${days}d ago`;
  const weeks = Math.floor(days / 7);
  return `${weeks}w ago`;
}

function countryName(code) {
  return COUNTRY_NAMES[code] || (code ? code.toUpperCase() : '—');
}

function levelBadge(job) {
  if (job.seniority === 'junior') return '<span class="badge badge-junior">Junior</span>';
  if (job.seniority === 'mid_plus') return '<span class="badge badge-level">Mid+</span>';
  if (job.seniority === 'mixed') return '<span class="badge badge-borderline">⚠ Mixed</span>';
  if (job.seniority === 'internship') return '<span class="badge badge-borderline">Internship</span>';
  if (job.seniority === 'unclear') return '<span class="badge badge-borderline">⚠ Verify</span>';
  return '';
}

function salaryBadge(job) {
  if (!job.salary) return '';
  let s = esc(job.salary);
  return `<span class="badge badge-level">💰 ${s}</span>`;
}

function matchClass(score) {
  const n = Number(score);
  if (!isFinite(n)) return 'low';
  if (n >= 75) return 'high';
  if (n >= 50) return 'mid';
  return 'low';
}

function matchBadge(job) {
  if (job.match_score == null) return '';
  return `<span class="badge badge-match ${matchClass(job.match_score)}" title="Profile match score">🎯 ${esc(job.match_score)}</span>`;
}

function jobCard(job) {
  const classes = ['job-card'];
  if (job.remote) classes.push('remote');
  if (job.first_seen && job.first_seen.slice(0, 10) === todayIso()) classes.push('is-new');

  const title = job.role_fit === 'borderline' ? '~ ' + job.title : job.title;
  const badges = [];
  if (job.match_score != null) badges.push(matchBadge(job));
  if (job.first_seen && job.first_seen.slice(0, 10) === todayIso()) badges.push('<span class="badge badge-new">NEW</span>');
  if (job.remote) badges.push('<span class="badge badge-remote">🌍 Remote</span>');
  if (job.relocation) badges.push('<span class="badge badge-reloc">✈️ Relo/visa</span>');
  badges.push(`<span class="badge badge-source">${esc(job.source || '—')}</span>`);
  badges.push(levelBadge(job));

  const location = [
    job.location,
    job.country ? countryName(job.country) : null,
  ].filter(Boolean).join(' · ');

  return `
  <article class="${classes.join(' ')}" data-id="${esc(job.id)}">
    <div class="job-top">
      <a class="job-title" data-action="details" href="#">${esc(title)}</a>
      <div class="badges">${badges.join('')}</div>
    </div>
    <div class="job-meta">
      <span><b>${esc(job.company || 'Unknown')}</b></span>
      <span>📍 ${esc(location || 'Remote')}</span>
      ${job.salary ? salaryBadge(job) : ''}
    </div>
    <div class="job-footer">
      <span class="posted">${timeAgo(job.posted) || ''}</span>
      <div class="job-actions">
        <button class="btn btn-sm btn-details" data-action="details">Details</button>
        <a class="btn btn-sm btn-apply" href="${esc(job.url)}" target="_blank" rel="noopener">Apply →</a>
      </div>
    </div>
  </article>`;
}

function todayIso() {
  return new Date().toISOString().slice(0, 10);
}

function renderJobList(jobs) {
  buildJobsCache(jobs);
  const grid = $('#grid');
  if (!jobs.length) {
    grid.innerHTML = '';
    $('#empty').classList.remove('hidden');
  } else {
    $('#empty').classList.add('hidden');
    grid.innerHTML = jobs.map(jobCard).join('');
  }
  $('#results-count').textContent = `${state.total.toLocaleString()} job${state.total === 1 ? '' : 's'}`;
  renderPager();
}

function renderPager() {
  const pager = $('#pager');
  if (state.pages <= 1) { pager.innerHTML = ''; return; }
  let html = `<button class="page-btn" data-page="${state.page - 1}" ${state.page === 1 ? 'disabled' : ''}>‹</button>`;
  for (let p = 1; p <= state.pages; p++) {
    if (state.pages > 12 && p > 2 && p < state.pages - 1 && Math.abs(p - state.page) > 2) {
      if (p === 3 || p === state.pages - 2) html += '<span class="page-btn" disabled>…</span>';
      continue;
    }
    html += `<button class="page-btn ${p === state.page ? 'active' : ''}" data-page="${p}">${p}</button>`;
  }
  html += `<button class="page-btn" data-page="${state.page + 1}" ${state.page === state.pages ? 'disabled' : ''}>›</button>`;
  pager.innerHTML = html;
}

async function loadJobs({ reset = false } = {}) {
  if (reset) state.page = 1;
  $('#loading').classList.remove('hidden');
  $('#grid').innerHTML = '';
  const params = new URLSearchParams({
    q: state.q, track: state.track, level: state.level, country: state.country,
    source: state.source, sort: state.sort, page: state.page, per: state.per,
  });
  if (state.remote) params.set('remote', '1');
  if (state.reloc) params.set('reloc', '1');
  if (state.hideApplied) params.set('hide_applied', '1');
  if (state.minMatch && state.minMatch !== '0') params.set('min_match', state.minMatch);
  try {
    const data = await api('/api/jobs?' + params.toString());
    state.total = data.total;
    state.pages = data.pages;
    renderJobList(data.jobs);
  } catch (err) {
    $('#grid').innerHTML = `<div class="empty">Error loading jobs: ${esc(err.message)}</div>`;
  } finally {
    $('#loading').classList.add('hidden');
  }
}

async function loadStats() {
  try {
    const s = await api('/api/stats');
    $('#stat-total').textContent = s.total.toLocaleString();
    $('#stat-today').textContent = s.today.toLocaleString();
    $('#stat-remote').textContent = s.remote.toLocaleString();
    $('#stat-reloc').textContent = s.reloc.toLocaleString();
    $('#stat-data').textContent = (s.tracks.data_engineer || 0).toLocaleString();
    $('#stat-backend').textContent = (s.tracks.backend_engineer || 0).toLocaleString();
    $('#stat-software').textContent = (s.tracks.software_engineer || 0).toLocaleString();
    $('#last-updated').textContent = `Live · ${new Date().toLocaleString('en-GB', { hour12: false })}`;
  } catch (e) { /* stats are decorative */ }
}

async function loadOptions() {
  try {
    const o = await api('/api/options');
    const countrySel = $('#country');
    countrySel.innerHTML = '<option value="all">All countries</option>' +
      o.countries.map(c => `<option value="${esc(c)}">${esc(countryName(c))}</option>`).join('');
    const sourceSel = $('#source');
    sourceSel.innerHTML = '<option value="all">All sources</option>' +
      o.sources.map(s => `<option value="${esc(s)}">${esc(s)}</option>`).join('');
  } catch (e) { /* filters stay minimal */ }
}

/* ---------- detail modal ---------- */

function metaItem(label, value) {
  if (!value) return '';
  return `<div class="meta-item"><span>${label}</span><div>${value}</div></div>`;
}

const APPLICATION_STATUSES = ['draft', 'ready', 'applied', 'interview', 'rejected', 'offer', 'withdrawn'];
let appIndex = {};
let profileReady = false;

async function postJson(path, body) {
  const res = await fetch(path, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body || {}),
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error || `HTTP ${res.status}`);
  return data;
}

let toastTimer;
function toast(msg, isError) {
  const el = $('#toast');
  if (!el) return;
  el.textContent = msg;
  el.classList.toggle('err', !!isError);
  el.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { el.hidden = true; }, 4200);
}

function statusSelect(jobId) {
  const current = appIndex[jobId] || 'draft';
  return `<label class="status-pick"><span>Status</span>
    <select data-status-job="${esc(jobId)}">
      ${APPLICATION_STATUSES.map(s => `<option value="${s}" ${s === current ? 'selected' : ''}>${s}</option>`).join('')}
    </select></label>`;
}

function openModal(id) {
  const data = jobsCache[id];
  if (!data) return;

  const companyUrl = data.company_url
    ? `<a class="btn btn-sm btn-ghost" href="${esc(data.company_url)}" target="_blank" rel="noopener">🏢 Company site</a>`
    : `<a class="btn btn-sm btn-ghost" href="https://www.google.com/search?q=${encodeURIComponent(data.company || '')}+jobs" target="_blank" rel="noopener">🔎 Search company</a>`;

  const badges = [];
  if (data.match_score != null) badges.push(matchBadge(data));
  if (data.remote) badges.push('<span class="badge badge-remote">🌍 Remote</span>');
  if (data.relocation) badges.push('<span class="badge badge-reloc">✈️ Relo/visa</span>');
  badges.push(levelBadge(data));

  const title = data.role_fit === 'borderline' ? '~ ' + data.title : data.title;

  const matchPanel = data.match_score != null ? `
    <div class="match-panel ${matchClass(data.match_score)}">
      <div class="match-title">🎯 Match ${esc(data.match_score)}/100</div>
      ${data.match_reasons ? `<div class="match-line"><b>Why it fits:</b> ${esc(data.match_reasons)}</div>` : ''}
      ${data.match_gaps ? `<div class="match-line"><b>Watch:</b> ${esc(data.match_gaps)}</div>` : ''}
      ${data.matched_skills ? `<div class="match-line"><b>Matched skills:</b> ${esc(data.matched_skills)}</div>` : ''}
    </div>` : '';

  const tailorBtn = profileReady
    ? `<button class="btn btn-tailor" id="btn-tailor" data-job="${esc(data.id)}">✍️ Tailor application pack</button>`
    : `<span class="muted">Add <code>profile/master_profile.json</code> to enable tailored resumes &amp; emails.</span>`;

  const modal = $('#modal');
  modal.innerHTML = `
    <div class="modal-head">
      <button class="modal-close" aria-label="Close">✕</button>
      <h3>${esc(title)}</h3>
      <div class="modal-company">${esc(data.company || 'Unknown')}</div>
      <div class="badges" style="margin-top:10px">${badges.join('')}</div>
    </div>
    <div class="modal-body">
      <div class="meta-grid">
        ${metaItem('Location', `${esc(data.location || '—')} · ${esc(countryName(data.country))}`)}
        ${metaItem('Posted', fmtDate(data.posted))}
        ${metaItem('Salary', data.salary ? esc(data.salary) : 'Not listed')}
        ${metaItem('Source', esc(data.source))}
        ${metaItem('Track', esc(String(data.track || '').replace(/_/g, ' ')))}
      </div>
      ${matchPanel}
      <div class="modal-desc">${esc(data.description || data.snippet || 'No description available.')}</div>
      <div class="modal-actions">
        <a class="btn btn-apply" href="${esc(data.url)}" target="_blank" rel="noopener">Apply on ${esc(data.source || 'job board')} →</a>
        ${companyUrl}
      </div>
      <div class="apply-box">
        <div class="apply-row">
          ${tailorBtn}
          ${statusSelect(data.id)}
        </div>
        <div id="drafts" class="drafts-box"></div>
      </div>
    </div>`;
  $('#modal-backdrop').hidden = false;
  document.body.style.overflow = 'hidden';
}

function renderDrafts(manifest) {
  const box = document.querySelector('#drafts');
  if (!box || !manifest) return;
  const urls = manifest.urls || {};
  const links = [];
  if (urls.resume_pdf) links.push(`<a class="btn btn-sm btn-apply" href="${esc(urls.resume_pdf)}" target="_blank" rel="noopener">📄 Resume PDF</a>`);
  if (urls.resume_html) links.push(`<a class="btn btn-sm btn-ghost" href="${esc(urls.resume_html)}" target="_blank" rel="noopener">📄 Resume (web)</a>`);
  if (urls.cover_letter_md) links.push(`<a class="btn btn-sm btn-ghost" href="${esc(urls.cover_letter_md)}" target="_blank" rel="noopener">✉️ Cover letter</a>`);
  if (urls.email_txt) links.push(`<a class="btn btn-sm btn-ghost" href="${esc(urls.email_txt)}" target="_blank" rel="noopener">📧 Email draft</a>`);
  box.innerHTML = `<div class="drafts"><b>Drafts ready</b>${manifest.recipient ? ` · to ${esc(manifest.recipient)}` : ' · no recipient found — add one before sending'}
    <div class="draft-links">${links.join('')}</div>
    <div class="muted">Nothing was sent. Review each file, then send it yourself.</div></div>`;
}

async function tailorApplication(jobId, btn) {
  const original = btn.textContent;
  btn.disabled = true;
  btn.textContent = '⏳ Generating…';
  try {
    const data = await postJson('/api/tailor', { job_id: jobId });
    renderDrafts(data.manifest);
    if (!appIndex[jobId]) { appIndex[jobId] = 'draft'; }
    loadApplicationsCount();
    toast('Application pack generated');
  } catch (err) {
    toast('Tailoring failed: ' + err.message, true);
  } finally {
    btn.disabled = false;
    btn.textContent = original;
  }
}

async function setApplicationStatus(jobId, status) {
  try {
    await postJson('/api/applications', { job_id: jobId, status });
    appIndex[jobId] = status;
    toast(`Marked as ${status}`);
    loadApplicationsCount();
  } catch (err) {
    toast('Update failed: ' + err.message, true);
  }
}

function baseName(path) {
  return path ? String(path).split('/').pop() : '';
}

function appRow(app) {
  const link = app.url ? `<a class="btn btn-sm btn-ghost" href="${esc(app.url)}" target="_blank" rel="noopener">Open</a>` : '';
  const jobId = encodeURIComponent(app.job_id);
  const resumeFile = baseName(app.resume_path);
  const resume = resumeFile
    ? `<a class="btn btn-sm btn-ghost" href="/api/draft/${jobId}/${encodeURIComponent(resumeFile)}" target="_blank" rel="noopener">📄 CV</a>` : '';
  const emailFile = baseName(app.email_path);
  const email = emailFile
    ? `<a class="btn btn-sm btn-ghost" href="/api/draft/${jobId}/${encodeURIComponent(emailFile)}" target="_blank" rel="noopener">📧 Email</a>` : '';
  const coverFile = baseName(app.cover_path);
  const cover = coverFile
    ? `<a class="btn btn-sm btn-ghost" href="/api/draft/${jobId}/${encodeURIComponent(coverFile)}" target="_blank" rel="noopener">✉️ Letter</a>` : '';
  const select = `<select data-status-job="${esc(app.job_id)}">${APPLICATION_STATUSES
    .map(s => `<option value="${s}" ${s === app.status ? 'selected' : ''}>${s}</option>`).join('')}</select>`;
  return `<div class="app-row">
    <div class="app-main">
      <div class="app-title">${esc(app.title || app.subject || app.job_id)}</div>
      <div class="muted">${esc(app.company || '')}${app.match_score != null ? ` · 🎯 ${esc(app.match_score)}` : ''}</div>
    </div>
    <div class="app-actions">${resume}${cover}${email}${link}${select}</div>
  </div>`;
}

async function openApplications() {
  let data;
  try {
    data = await api('/api/applications');
  } catch (err) {
    toast('Could not load applications: ' + err.message, true);
    return;
  }
  const rows = data.applications || [];
  const counts = Object.entries(data.counts || {}).map(([k, v]) => `${k} ${v}`).join(' · ');
  const modal = $('#modal');
  modal.innerHTML = `
    <div class="modal-head">
      <button class="modal-close" aria-label="Close">✕</button>
      <h3>📁 Applications</h3>
      <div class="modal-company">${rows.length} tracked${counts ? ' · ' + esc(counts) : ''}</div>
    </div>
    <div class="modal-body">
      ${rows.length ? rows.map(appRow).join('') : '<p>No applications yet. Open a job and click “Tailor application pack”. Nothing is sent automatically — drafts only.</p>'}
    </div>`;
  $('#modal-backdrop').hidden = false;
  document.body.style.overflow = 'hidden';
}

async function loadApplicationsCount() {
  try {
    const data = await api('/api/applications');
    appIndex = {};
    for (const app of data.applications || []) appIndex[app.job_id] = app.status;
    const btn = $('#btn-apps');
    const n = (data.applications || []).length;
    if (btn) btn.textContent = `📁 Applications${n ? ` (${n})` : ''}`;
  } catch (e) { /* decorative */ }
}

async function loadProfileStatus() {
  try {
    const p = await api('/api/profile');
    profileReady = !!p.configured;
  } catch (e) { profileReady = false; }
}

function closeModal() {
  $('#modal-backdrop').hidden = true;
  document.body.style.overflow = '';
}

let jobsCache = {};

function buildJobsCache(jobs) {
  for (const j of jobs) jobsCache[j.id] = j;
}

/* ---------- events ---------- */

function bindEvents() {
  $('#btn-search').addEventListener('click', () => {
    state.q = $('#q').value.trim();
    loadJobs({ reset: true });
  });
  $('#q').addEventListener('keydown', (e) => {
    if (e.key === 'Enter') {
      state.q = $('#q').value.trim();
      loadJobs({ reset: true });
    }
  });
  $('#track').addEventListener('change', (e) => { state.track = e.target.value; loadJobs({ reset: true }); });
  $('#level').addEventListener('change', (e) => { state.level = e.target.value; loadJobs({ reset: true }); });
  $('#country').addEventListener('change', (e) => { state.country = e.target.value; loadJobs({ reset: true }); });
  $('#source').addEventListener('change', (e) => { state.source = e.target.value; loadJobs({ reset: true }); });
  $('#sort').addEventListener('change', (e) => { state.sort = e.target.value; loadJobs({ reset: true }); });
  $('#remote').addEventListener('change', (e) => { state.remote = e.target.checked; loadJobs({ reset: true }); });
  $('#reloc').addEventListener('change', (e) => { state.reloc = e.target.checked; loadJobs({ reset: true }); });
  $('#minmatch').addEventListener('change', (e) => { state.minMatch = e.target.value; loadJobs({ reset: true }); });
  $('#hideApplied').addEventListener('change', (e) => { state.hideApplied = e.target.checked; loadJobs({ reset: true }); });
  $('#btn-apps').addEventListener('click', openApplications);
  $('#btn-reset').addEventListener('click', () => {
    state.q = ''; state.track = 'all'; state.level = 'all'; state.country = 'all'; state.source = 'all';
    state.sort = 'new'; state.minMatch = '0'; state.remote = false; state.reloc = false; state.hideApplied = false;
    $('#q').value = ''; $('#track').value = 'all'; $('#level').value = 'all'; $('#country').value = 'all';
    $('#source').value = 'all'; $('#sort').value = 'new'; $('#minmatch').value = '0';
    $('#remote').checked = false; $('#reloc').checked = false; $('#hideApplied').checked = false;
    loadJobs({ reset: true });
  });

  $('#grid').addEventListener('click', (e) => {
    const btn = e.target.closest('[data-action="details"]');
    if (btn) {
      e.preventDefault();
      const card = btn.closest('.job-card');
      openModal(card.dataset.id);
    }
  });

  $('#pager').addEventListener('click', (e) => {
    const btn = e.target.closest('[data-page]');
    if (btn && !btn.disabled) {
      state.page = parseInt(btn.dataset.page, 10);
      loadJobs();
      window.scrollTo({ top: 0, behavior: 'smooth' });
    }
  });

  $('#modal-backdrop').addEventListener('click', (e) => {
    const tailor = e.target.closest('#btn-tailor');
    if (tailor) {
      tailorApplication(tailor.dataset.job, tailor);
      return;
    }
    if (e.target === $('#modal-backdrop') || e.target.closest('.modal-close')) closeModal();
  });
  $('#modal-backdrop').addEventListener('change', (e) => {
    const sel = e.target.closest('select[data-status-job]');
    if (sel) setApplicationStatus(sel.dataset.statusJob, sel.value);
  });
  document.addEventListener('keydown', (e) => { if (e.key === 'Escape') closeModal(); });
}

/* ---------- init ---------- */
(async function init() {
  bindEvents();
  await loadProfileStatus();
  await Promise.all([loadStats(), loadApplicationsCount(), loadOptions()]);
  await loadJobs({ reset: true });
})();
