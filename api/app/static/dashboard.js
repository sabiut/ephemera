// Ephemera Dashboard

const API_BASE = window.location.origin;

// ─── Auth ───────────────────────────────────────────────
//
// The session is an HttpOnly cookie set by the OAuth callback. Scripts never
// see it; the browser attaches it to same-origin requests. The server
// requires X-Requested-With on any non-GET cookie-authenticated request as
// CSRF protection, so apiCall always sends it.

async function logout() {
    try {
        await apiCall('/auth/logout', { method: 'POST' });
    } catch (e) {
        console.error('Logout failed:', e);
    }
    window.location.href = '/';
}

// ─── API Helper ─────────────────────────────────────────

async function apiCall(endpoint, options = {}) {
    const headers = {
        'Content-Type': 'application/json',
        'X-Requested-With': 'ephemera-dashboard',
        ...options.headers
    };

    const response = await fetch(`${API_BASE}${endpoint}`, {
        credentials: 'same-origin',
        ...options,
        headers
    });

    if (response.status === 401) {
        window.location.href = '/';
        return;
    }

    if (!response.ok) {
        const error = await response.json().catch(() => ({}));
        // FastAPI validation errors give detail as a list of {msg, loc}.
        const detail = Array.isArray(error.detail)
            ? error.detail.map(d => d.msg || JSON.stringify(d)).join('; ')
            : error.detail;
        throw new Error(detail || `Request failed (HTTP ${response.status})`);
    }

    if (response.status === 204) return null;  // e.g. DELETE: no body
    return response.json();
}

// ─── Toast Notifications ────────────────────────────────

function showToast(message, type = 'success') {
    const container = document.getElementById('toastContainer');
    const toast = document.createElement('div');
    toast.className = `toast toast-${type}`;
    toast.textContent = message;
    container.appendChild(toast);
    setTimeout(() => {
        toast.style.opacity = '0';
        toast.style.transition = 'opacity 0.3s ease';
        setTimeout(() => toast.remove(), 300);
    }, 3000);
}

// ─── Modal Helpers ──────────────────────────────────────

function openModal(id) {
    document.getElementById(id).classList.add('active');
}

function closeModal(id) {
    document.getElementById(id).classList.remove('active');
}

// Close modal on overlay click
document.addEventListener('click', (e) => {
    if (e.target.classList.contains('modal-overlay')) {
        e.target.classList.remove('active');
        if (e.target.id === 'envDetailModal') detailEnvId = null;
    }
});
document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape' && detailEnvId !== null) closeEnvironmentDetail();
});

// ─── Sidebar Toggle (Mobile) ────────────────────────────

function toggleSidebar() {
    document.getElementById('sidebar').classList.toggle('open');
}

// ─── Routing ────────────────────────────────────────────

const views = ['overview', 'environments', 'repositories', 'credentials', 'tokens'];

function navigate() {
    const hash = window.location.hash.slice(1) || 'overview';
    const view = views.includes(hash) ? hash : 'overview';

    // Update active view
    views.forEach(v => {
        const el = document.getElementById(`view-${v}`);
        if (el) el.classList.toggle('active', v === view);
    });

    // Update active nav
    document.querySelectorAll('.nav-item').forEach(item => {
        item.classList.toggle('active', item.dataset.view === view);
    });

    // Close sidebar on mobile after navigation
    document.getElementById('sidebar').classList.remove('open');

    // Load data for view
    loadView(view);
}

window.addEventListener('hashchange', navigate);

// ─── Data Loading ───────────────────────────────────────

let cachedEnvironments = [];
let environmentsError = null;
let cachedCredentials = [];
let cachedTokens = [];
let isAdmin = false;
let cachedRepositories = null;   // null: not loaded or failed
let repositoriesError = null;
let installUrl = null;

async function loadView(view) {
    switch (view) {
        case 'overview':
            await Promise.all([loadEnvironments(), loadRepositories()]);
            renderOverview();
            break;
        case 'repositories':
            await loadRepositories();
            renderRepositories();
            break;
        case 'environments':
            await loadEnvironments();
            renderEnvironments();
            break;
        case 'credentials':
            await loadCredentials();
            renderCredentials();
            break;
        case 'tokens':
            await loadTokens();
            renderTokens();
            break;
    }
}

// One refresh loop for every view. While anything is in flight (a preview
// provisioning, updating or being torn down, or a PR whose preview was just
// requested) it polls every 10 seconds; otherwise every 30. Before this only
// the Environments page refreshed, so after "Create preview" the
// Repositories and Overview pages kept showing "Provisioning" for good.
const PENDING_STATUSES = ['pending', 'provisioning', 'updating', 'destroying'];
let selectedRepo = null;          // "owner/repo" shown on the Repositories page
let cachedPulls = [];
let refreshTimer = null;

function workPending() {
    const envBusy = cachedEnvironments.some(e => PENDING_STATUSES.includes((e.status || '').toLowerCase()));
    const pullBusy = cachedPulls.some(p => PENDING_STATUSES.includes(p.environment_status));
    return envBusy || pullBusy;
}

function startAutoRefresh() {
    if (refreshTimer) clearTimeout(refreshTimer);
    refreshTimer = setTimeout(async () => {
        const hash = window.location.hash.slice(1) || 'overview';
        try {
            if (detailEnvId !== null) {
                await loadEnvironments();
                renderEnvironmentDetail();
            }
            if (hash === 'overview') {
                if (detailEnvId === null) await loadEnvironments();
                renderOverview();
            } else if (hash === 'environments') {
                if (detailEnvId === null) await loadEnvironments();
                renderEnvironments();
            } else if (hash === 'repositories' && selectedRepo) {
                const [owner, repo] = selectedRepo.split('/');
                await loadPulls(owner, repo);
            }
        } finally {
            startAutoRefresh();
        }
    }, workPending() ? 10000 : 30000);
}

async function loadEnvironments() {
    try {
        cachedEnvironments = await apiCall('/api/v1/environments/') || [];
        environmentsError = null;
    } catch (e) {
        // Keep whatever we last had and say the refresh failed, instead of
        // showing an empty list that looks like "you have no environments".
        console.error('Failed to load environments:', e);
        environmentsError = e.message || 'Request failed';
    }
}

async function loadCredentials() {
    try {
        cachedCredentials = await apiCall('/api/v1/credentials/') || [];
    } catch (e) {
        console.error('Failed to load credentials:', e);
        cachedCredentials = [];
    }
}

async function loadTokens() {
    try {
        cachedTokens = await apiCall('/api/v1/tokens/') || [];
    } catch (e) {
        console.error('Failed to load tokens:', e);
        cachedTokens = [];
    }
}

// ─── User Info ──────────────────────────────────────────

async function loadUserInfo() {
    try {
        const user = await apiCall('/auth/me');
        if (user) {
            isAdmin = Boolean(user.is_admin);
            document.getElementById('userName').textContent = user.github_login;
            document.getElementById('userAvatar').src = user.avatar_url || '';
        }
    } catch (e) {
        console.error('Failed to load user info:', e);
    }
}

// ─── Renderers ──────────────────────────────────────────

function statusBadge(status, label) {
    const s = (status || 'pending').toLowerCase();
    return `<span class="badge badge-${s}"><span class="badge-dot"></span>${escapeHtml(label || s)}</span>`;
}

// A deployment attempt's status, in the colours of the preview statuses.
function attemptBadge(status) {
    const map = { success: ['ready', 'succeeded'], failed: ['failed', 'failed'],
                  in_progress: ['updating', 'deploying'], queued: ['pending', 'queued'] };
    const [style, label] = map[status] || ['pending', status];
    return statusBadge(style, label);
}

function activeBadge(isActive) {
    return isActive
        ? '<span class="badge badge-active"><span class="badge-dot"></span>active</span>'
        : '<span class="badge badge-inactive"><span class="badge-dot"></span>inactive</span>';
}

// ─── Deployment progress ────────────────────────────────

const STAGE_LABELS = {
    queued: 'Queued',
    preparing: 'Setting up',
    deploying: 'Deploying services',
    waiting_for_image: 'Waiting for image',
    starting: 'Starting services',
    checking_https: 'Checking HTTPS',
    ready: 'Ready',
    failed: 'Failed',
    destroying: 'Removing',
    destroyed: 'Removed',
};

function formatDuration(seconds) {
    seconds = Math.max(0, Math.floor(seconds));
    if (seconds < 60) return `${seconds}s`;
    const m = Math.floor(seconds / 60), s = seconds % 60;
    if (m < 60) return `${m}m ${String(s).padStart(2, '0')}s`;
    return `${Math.floor(m / 60)}h ${String(m % 60).padStart(2, '0')}m`;
}

// Elements with data-since="<ISO time>" show the time elapsed since then and
// tick every second between the 10-second data refreshes.
function sinceHTML(iso) {
    if (!iso) return '';
    const secs = (Date.now() - new Date(iso).getTime()) / 1000;
    return `<span data-since="${escapeHtml(iso)}">${formatDuration(secs)}</span>`;
}

setInterval(() => {
    document.querySelectorAll('[data-since]').forEach(el => {
        el.textContent = formatDuration((Date.now() - new Date(el.dataset.since).getTime()) / 1000);
    });
}, 1000);

// "Ready" only when every public service passed its check; otherwise say so.
function unverifiedChecks(env) {
    return Object.entries(env.readiness || {}).filter(([, r]) => !r.verified);
}

function readinessLineHTML(env) {
    if ((env.status || '').toLowerCase() !== 'ready' || !unverifiedChecks(env).length) return '';
    return `<div class="text-sm" style="margin-top:4px;"><a class="row-link" style="color:#eab308;" onclick="openEnvironment(${env.id})">Responding, not verified</a></div>`;
}

function checksHTML(env) {
    const entries = Object.entries(env.readiness || {}).sort();
    if (!entries.length) return '';
    return `<div class="section-label">Checks</div><ul class="history">${entries.map(([service, r]) => r.verified
        ? `<li><span class="mono">${escapeHtml(service)}</span><span style="color:#22c55e;">✓</span><span class="text-muted"><code>${escapeHtml(r.path)}</code> answered ${r.status}</span></li>`
        : `<li><span class="mono">${escapeHtml(service)}</span><span style="color:#eab308;">!</span><span class="text-muted"><code>${escapeHtml(r.path)}</code> returned ${r.status}: responding, but not confirmed working</span>
           <span class="why">Add <code>ephemera.readiness-path: /health</code> (a path that returns 200) to ${escapeHtml(service)} in docker-compose.yml so Ready means it works.</span></li>`
    ).join('')}</ul>`;
}

function stageLineHTML(env) {
    const status = (env.status || '').toLowerCase();
    if (!PENDING_STATUSES.includes(status) || !env.stage) return '';
    const label = STAGE_LABELS[env.stage] || env.stage;
    return `<div class="stage-line">${escapeHtml(label)} · ${sinceHTML(env.deploy_started_at || env.stage_started_at)}</div>`;
}

// The steps of this deployment. "Setting up" only applies to a new preview;
// the image step reads "Waiting for image" while it is the current one.
function stepperHTML(env) {
    const status = (env.status || '').toLowerCase();
    const steps = ['queued'];
    if (status === 'provisioning' || status === 'pending' || env.stage === 'preparing') steps.push('preparing');
    steps.push('deploying', 'waiting_for_image', 'starting', 'checking_https', 'ready');
    const current = steps.indexOf(env.stage);
    return `<ol class="stepper">${steps.map((step, i) => {
        const state = current < 0 ? 'todo' : i < current ? 'done' : i === current ? 'current' : 'todo';
        let label = STAGE_LABELS[step];
        if (step === 'waiting_for_image' && state !== 'current') label = 'Image available';
        const timer = state === 'current' ? ` <span class="text-muted">${sinceHTML(env.stage_started_at)}</span>` : '';
        const detail = state === 'current' && env.stage_detail ? `<div class="step-detail">${escapeHtml(env.stage_detail)}</div>` : '';
        return `<li class="step-${state}"><span class="dot">${state === 'done' ? '✓' : ''}</span><div>${escapeHtml(label)}${timer}${detail}</div></li>`;
    }).join('')}</ol>`;
}

function timeAgo(dateStr) {
    if (!dateStr) return '-';
    const seconds = Math.floor((Date.now() - new Date(dateStr).getTime()) / 1000);
    if (seconds < 60) return 'just now';
    if (seconds < 3600) return `${Math.floor(seconds / 60)}m ago`;
    if (seconds < 86400) return `${Math.floor(seconds / 3600)}h ago`;
    return `${Math.floor(seconds / 86400)}d ago`;
}

// A failed row leads with what happened; the explanation, next step, full
// error and Retry are in the details view (not a hover tooltip, which
// phones cannot show).
function envErrorHTML(env) {
    const s = (env.status || '').toLowerCase();
    if (s === 'destroyed' && ['expired', 'stopped'].includes(env.removal_reason)) {
        const label = env.removal_reason === 'stopped' ? 'Stopped' : 'Expired';
        return `<div class="text-sm" style="margin-top:4px;"><a class="row-link" style="color:#a5b4fc;" onclick="openEnvironment(${env.id})">${label}: recreate</a></div>`;
    }
    if (s !== 'failed') return '';
    const title = (env.diagnosis && env.diagnosis.title) || 'Preview failed';
    return `<div class="text-sm" style="margin-top:4px;"><a class="row-link" onclick="openEnvironment(${env.id})">${escapeHtml(title)}: see what to do</a></div>`;
}

function envPreviewHTML(env) {
    const status = (env.status || '').toLowerCase();
    const urls = env.service_urls || {};
    const names = Object.keys(urls);
    if (status !== 'ready' || (!env.environment_url && names.length === 0)) {
        return '<span class="text-muted">-</span>';
    }
    const primary = env.environment_url || urls[names[0]];
    const others = names.filter(n => urls[n] !== primary);
    const extra = others.length
        ? `<div class="text-sm" style="margin-top:4px;">${others.map(n =>
            `<a href="${escapeHtml(urls[n])}" target="_blank" class="text-muted">${escapeHtml(n)}</a>`).join(' · ')}</div>`
        : '';
    return `<a href="${escapeHtml(primary)}" target="_blank" style="color: #6366f1;">Open preview</a>${extra}`;
}

function envTableHTML(envs) {
    if (environmentsError) {
        return `
            <div class="empty-state">
                <p><strong>Could not load environments.</strong> ${escapeHtml(environmentsError)}</p>
                <p class="text-muted text-sm">This list may be stale. It refreshes automatically; reload the page if it persists.</p>
            </div>
        `;
    }
    if (!envs || envs.length === 0) {
        return `
            <div class="empty-state">
                <svg fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="1.5">
                    <path stroke-linecap="round" stroke-linejoin="round" d="M5 12h14M5 12a2 2 0 01-2-2V6a2 2 0 012-2h14a2 2 0 012 2v4a2 2 0 01-2 2M5 12a2 2 0 00-2 2v4a2 2 0 002 2h14a2 2 0 002-2v-4a2 2 0 00-2-2"/>
                </svg>
                <p>${isAdmin
                    ? 'No environments yet. Create a PR on a connected repository to get started.'
                    : 'No environments yet. You see previews for pull requests you opened and for repositories where you are a collaborator.'}</p>
            </div>
        `;
    }

    return `
        <table class="table">
            <thead>
                <tr>
                    <th>Repository</th>
                    <th>PR</th>
                    <th>Opened by</th>
                    <th>Branch</th>
                    <th>Status</th>
                    <th>Preview</th>
                    <th>Commit</th>
                    <th>Created</th>
                    <th></th>
                </tr>
            </thead>
            <tbody>
                ${envs.map(env => `
                    <tr>
                        <td class="mono">${escapeHtml(env.repository_full_name || '-')}</td>
                        <td>#${env.pr_number || '-'}</td>
                        <td class="text-muted">${escapeHtml(env.owner_login || '-')}</td>
                        <td class="mono text-muted">${escapeHtml(env.branch_name || '-')}</td>
                        <td>${statusBadge(env.status)}${stageLineHTML(env)}${readinessLineHTML(env)}${envErrorHTML(env)}</td>
                        <td>${envPreviewHTML(env)}</td>
                        <td class="mono text-muted text-sm">${escapeHtml((env.commit_sha || '').slice(0, 8) || '-')}</td>
                        <td class="text-muted text-sm">${timeAgo(env.created_at)}</td>
                        <td style="text-align:right;"><button class="btn btn-ghost btn-sm" onclick="openEnvironment(${env.id})">Details</button></td>
                    </tr>
                `).join('')}
            </tbody>
        </table>
    `;
}

// ─── Environment details ────────────────────────────────

let detailEnvId = null;
let detailDeployments = [];

// Backticks in diagnosis text mark names (services, files); show them as code.
function richText(text) {
    return escapeHtml(text || '').replace(/`([^`]+)`/g, '<code>$1</code>');
}

async function openEnvironment(id) {
    detailEnvId = id;
    detailDeployments = [];
    renderEnvironmentDetail();
    openModal('envDetailModal');
    try {
        detailDeployments = await apiCall(`/api/v1/environments/${id}/deployments?limit=5`) || [];
    } catch (e) {
        detailDeployments = null;
    }
    if (detailEnvId === id) renderEnvironmentDetail();
}

function closeEnvironmentDetail() {
    detailEnvId = null;
    closeModal('envDetailModal');
}

function renderEnvironmentDetail() {
    const env = cachedEnvironments.find(e => e.id === detailEnvId);
    const body = document.getElementById('envDetailBody');
    const footer = document.getElementById('envDetailFooter');
    if (!document.getElementById('envDetailModal').classList.contains('active') && detailEnvId === null) return;
    if (!env) {
        body.innerHTML = '<p class="text-muted">This preview is no longer in your list.</p>';
        footer.innerHTML = '<button class="btn btn-ghost" onclick="closeEnvironmentDetail()">Close</button>';
        return;
    }
    const status = (env.status || '').toLowerCase();
    const repo = env.repository_full_name;
    const sha = env.commit_sha || '';
    const running = ['pending', 'provisioning', 'ready', 'updating', 'failed'].includes(status);
    document.getElementById('envDetailTitle').textContent = `${repo} #${env.pr_number}`;

    const meta = `<div class="detail-meta">
        <div><div class="label">Status</div><div class="value">${statusBadge(env.status)}</div></div>
        <div><div class="label">Commit</div><div class="value mono"><a href="https://github.com/${escapeHtml(repo)}/commit/${escapeHtml(sha)}" target="_blank" class="text-muted">${escapeHtml(sha.slice(0, 7) || '-')}</a></div></div>
        <div><div class="label">Branch</div><div class="value mono">${escapeHtml(env.branch_name || '-')}</div></div>
        <div><div class="label">Opened by</div><div class="value">${escapeHtml(env.owner_login || '-')}</div></div>
        <div><div class="label">Access</div><div class="value">${env.access_applied === 'protected' ? 'Collaborators only'
            : env.access_applied === 'failed' ? '<span style="color:#f87171;">Last change failed</span>' : 'Anyone with the link'}</div></div>
        <div><div class="label">Last change</div><div class="value">${timeAgo(env.stage_started_at || env.updated_at || env.created_at)}</div></div>
    </div>`;
    const prTitle = env.pr_title ? `<p style="color:#e5e5e5;margin:0 0 16px;">${escapeHtml(env.pr_title)}</p>` : '';

    let main = '';
    if (status === 'failed') {
        const d = env.diagnosis || { title: 'Preview failed', explanation: '', action: '', links: [] };
        const actions = (d.actions || []).map((a, i) =>
            `<button class="btn btn-primary btn-sm" onclick="runRecovery(${env.id}, ${i})">${escapeHtml(a.label)}</button>`).join('');
        const links = actions + (d.links || []).map(l =>
            `<a class="btn btn-ghost btn-sm" href="${escapeHtml(l.url)}" target="_blank">${escapeHtml(l.label)}</a>`).join('');
        main = `<div class="diagnosis">
            <div class="diagnosis-title">${escapeHtml(d.title)}</div>
            ${d.explanation ? `<p>${richText(d.explanation)}</p>` : ''}
            ${d.action ? `<p class="next"><strong>What to do:</strong> ${richText(d.action)}</p>` : ''}
            ${links ? `<div class="diagnosis-links">${links}</div>` : ''}
        </div>
        ${env.error_message ? `<details class="tech-details"><summary>Technical details</summary><pre>${escapeHtml(env.error_message)}</pre></details>` : ''}`;
    } else if (status === 'ready') {
        const unverified = unverifiedChecks(env).length;
        main = `<div class="detail-note">${unverified ? 'Deployed and responding, but not every check passed.' : 'Ready.'} ${envPreviewHTML(env)}</div>${checksHTML(env)}`;
    } else if (status === 'destroying') {
        main = '<div class="detail-note">Being removed. This updates automatically.</div>';
    } else if (PENDING_STATUSES.includes(status)) {
        const total = env.deploy_started_at ? ` · ${sinceHTML(env.deploy_started_at)} so far` : '';
        main = `<div class="detail-note">Deploying commit <code>${escapeHtml(sha.slice(0, 7))}</code>${total}. This updates automatically.</div>${stepperHTML(env)}`;
    } else if (status === 'destroyed' && env.removal_reason === 'stopped') {
        main = `<div class="detail-note">Stopped. The pull request is still open, but new commits won't bring the preview back until you recreate it.</div>`;
    } else if (status === 'destroyed' && env.removal_reason === 'expired') {
        main = `<div class="detail-note">Removed after a period with no new commits, to free resources. Push a commit to the pull request, or recreate it here.</div>`;
    } else if (status === 'destroyed') {
        main = '<div class="detail-note">Removed. The pull request was closed or merged.</div>';
    }

    let history = '';
    if (detailDeployments === null) {
        history = '<p class="text-muted text-sm">Could not load earlier attempts.</p>';
    } else if (detailDeployments.length) {
        history = `<div class="section-label">Recent attempts</div><ul class="history">${detailDeployments.map(d => {
            const why = d.status === 'failed' && d.error_message ? `<span class="why">${escapeHtml(d.error_message.split('\n')[0])}</span>` : '';
            return `<li><span class="mono">${escapeHtml(d.commit_sha.slice(0, 7))}</span>${attemptBadge(d.status)}<span class="text-muted">${timeAgo(d.created_at)}</span>${why}</li>`;
        }).join('')}</ul>`;
    }
    let expiry = '';
    if (running && env.expires_at) {
        const when = new Date(env.expires_at);
        const label = when.toLocaleString(undefined, { weekday: 'short', day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' });
        expiry = `<div class="detail-note" style="display:flex;gap:12px;align-items:center;flex-wrap:wrap;">
            <span style="flex:1;min-width:200px;">Removed <strong>${escapeHtml(label)}</strong> if there are no new commits before then, to free resources.</span>
            <button class="btn btn-ghost btn-sm" onclick="keepEnvironment(${env.id}, this)">Keep available</button></div>`;
    }
    body.innerHTML = prTitle + meta + main + expiry + history;

    const recreatable = status === 'destroyed' && ['expired', 'stopped'].includes(env.removal_reason);
    const retry = status === 'failed'
        ? `<button class="btn btn-primary" onclick="retryEnvironment(${env.id}, this)">Retry preview</button>`
        : recreatable ? `<button class="btn btn-primary" onclick="retryEnvironment(${env.id}, this)">Recreate preview</button>` : '';
    const stop = running
        ? `<button class="btn btn-danger" onclick="stopEnvironment(${env.id}, this)">Stop preview</button>` : '';
    footer.innerHTML = `${stop}<span style="flex:1"></span><button class="btn btn-ghost" onclick="closeEnvironmentDetail()">Close</button>${retry}`;
}

async function stopEnvironment(id, button) {
    const env = cachedEnvironments.find(e => e.id === id);
    if (!env) return;
    if (!confirm(`Stop the preview for #${env.pr_number}? The pull request stays open and its slot is freed. New commits won't bring it back until you recreate it.`)) return;
    if (button) { button.disabled = true; button.textContent = 'Stopping…'; }
    try {
        await apiCall(`/api/v1/environments/${id}/stop`, { method: 'POST' });
        showToast(`Stopping the preview for #${env.pr_number}.`);
        await loadEnvironments();
        renderEnvironmentDetail();
        startAutoRefresh();
    } catch (e) {
        showToast('Could not stop: ' + e.message, 'error');
        if (button) { button.disabled = false; button.textContent = 'Stop preview'; }
    }
}

async function keepEnvironment(id, button) {
    if (button) { button.disabled = true; button.textContent = 'Keeping…'; }
    try {
        const updated = await apiCall(`/api/v1/environments/${id}/keep`, { method: 'POST' });
        const i = cachedEnvironments.findIndex(e => e.id === id);
        if (i >= 0) cachedEnvironments[i] = updated;
        renderEnvironmentDetail();
        showToast('Kept available: the idle timer starts again now.');
    } catch (e) {
        showToast('Could not keep it available: ' + e.message, 'error');
        if (button) { button.disabled = false; button.textContent = 'Keep available'; }
    }
}

// Recovery buttons: open the place where a failure is fixed, for this
// preview's repository and pull request.
async function openRepositoryView(fullName) {
    closeEnvironmentDetail();
    if (window.location.hash !== '#repositories') {
        window.location.hash = 'repositories';
        await new Promise(r => setTimeout(r, 0));
    }
    await selectRepository(fullName);
}

async function runRecovery(envId, index) {
    const env = cachedEnvironments.find(e => e.id === envId);
    const action = env && env.diagnosis && (env.diagnosis.actions || [])[index];
    if (!action) return;
    const fullName = env.repository_full_name;
    const [owner, repo] = fullName.split('/');
    if (action.kind === 'setup_guide') {
        closeEnvironmentDetail();
        await openSetupGuide(owner, repo, env.pr_number);
    } else if (action.kind === 'setup_check') {
        await openRepositoryView(fullName);
        await checkPull(fullName, env.pr_number);
    } else if (action.kind === 'registry') {
        await openRepositoryView(fullName);
        await loadRegistries(owner, repo);
        const kind = ['ghcr.io', 'docker.io'].includes(action.registry) ? action.registry : 'other';
        document.getElementById('regKind').value = kind;
        registryKindChanged();
        if (kind === 'other') document.getElementById('regHost').value = action.registry || '';
        document.getElementById('repoRegistriesCard').scrollIntoView({ behavior: 'smooth', block: 'start' });
        document.getElementById('regUser').focus();
    } else if (action.kind === 'usage') {
        await openRepositoryView(fullName);
        document.getElementById('repoPullsCard').scrollIntoView({ behavior: 'smooth', block: 'start' });
    }
}

async function retryEnvironment(id, button) {
    const env = cachedEnvironments.find(e => e.id === id);
    if (!env) return;
    if (button) { button.disabled = true; button.textContent = 'Retrying…'; }
    try {
        await apiCall('/api/v1/environments/', {
            method: 'POST',
            body: JSON.stringify({ repository_full_name: env.repository_full_name, pr_number: env.pr_number }),
        });
        const verb = (env.status || '').toLowerCase() === 'destroyed' ? 'Recreating' : 'Retrying';
        showToast(`${verb} #${env.pr_number}. This view updates as it deploys.`);
        await loadEnvironments();
        renderEnvironmentDetail();
        const hash = window.location.hash.slice(1) || 'overview';
        if (hash === 'overview') renderOverview();
        if (hash === 'environments') renderEnvironments();
        startAutoRefresh();
    } catch (e) {
        showToast('Could not retry: ' + e.message, 'error');
        if (button) { button.disabled = false; button.textContent = 'Retry preview'; }
    }
}

// ─── Repositories and onboarding ────────────────────────

async function loadRepositories(refresh = false) {
    try {
        const body = await apiCall('/api/v1/repositories' + (refresh ? '?refresh=true' : ''));
        cachedRepositories = (body && body.repositories) || [];
        installUrl = body && body.install_url;
        repositoriesError = null;
    } catch (e) {
        cachedRepositories = null;
        repositoriesError = e.message || 'Request failed';
    }
}

// Asks GitHub again instead of using cached access. Installing the App also
// refreshes it on the server, but this is the button a user reaches for.
async function refreshRepositories(button) {
    if (button) { button.disabled = true; button.textContent = 'Refreshing…'; }
    const before = Array.isArray(cachedRepositories) ? cachedRepositories.length : 0;
    try {
        await loadRepositories(true);
        renderRepositories();
        if (document.getElementById('view-overview').classList.contains('active')) renderOverview();
        if (repositoriesError) {
            showToast('Could not refresh repositories: ' + repositoriesError, 'error');
        } else {
            const n = cachedRepositories.length;
            showToast(n === 0
                ? 'Still no repositories. Check the App is installed on the repository and that you are a collaborator on it.'
                : n > before ? `Found ${n - before} new ${n - before === 1 ? 'repository' : 'repositories'}.`
                : `${n} ${n === 1 ? 'repository' : 'repositories'} connected.`, n === 0 ? 'error' : 'success');
        }
    } finally {
        if (button) { button.disabled = false; button.textContent = 'Refresh repositories'; }
    }
}

function renderGettingStarted() {
    const card = document.getElementById('gettingStarted');
    if (!card) return;
    const hasRepo = Array.isArray(cachedRepositories) && cachedRepositories.length > 0;
    // "Has ever had a working preview": a record with a deployment time, even
    // if since destroyed, or a per-browser flag set the first time that was
    // seen (old destroyed records are cleaned up after a week).
    let onboarded = false;
    try { onboarded = localStorage.getItem('ephemera_onboarded') === '1'; } catch (e) { /* storage blocked */ }
    const hasPreview = onboarded || cachedEnvironments.some(e => e.last_deployed_at || (e.status || '').toLowerCase() === 'ready');
    if (hasPreview && !onboarded) {
        try { localStorage.setItem('ephemera_onboarded', '1'); } catch (e) { /* storage blocked */ }
    }
    card.hidden = hasPreview;
    if (hasPreview) return;
    const install = installUrl
        ? `<a href="${escapeHtml(installUrl)}" target="_blank" style="color:#6366f1;">Install the Ephemera GitHub App</a> on the repository you want previews for.`
        : 'Ask your Ephemera administrator to install the GitHub App on your repository.';
    const steps = [
        { done: hasRepo, title: 'Connect a repository', body: hasRepo
            ? `Connected: ${cachedRepositories.slice(0, 3).map(r => escapeHtml(r.full_name)).join(', ')}${cachedRepositories.length > 3 ? '…' : ''}`
            : install + ' Signing in does not connect repositories; installing the App does. '
              + 'Installed already? <a href="#" onclick="refreshRepositories(); return false;" style="color:#6366f1;">Refresh repositories</a>. '
              + 'Just exploring? <a href="/#sample" style="color:#6366f1;">Try the sample app</a> first.' },
        { done: false, title: 'Check the setup', body: 'Open <a href="#repositories" style="color:#6366f1;">Repositories</a> and run the setup check. It reads your compose file and tells you what to fix before a pull request finds out the hard way.' },
        { done: hasPreview, title: 'Create the first preview', body: 'Open a pull request, or create a preview for an existing one from the Repositories page. The link appears on the pull request and here.' },
    ];
    document.getElementById('gettingStartedSteps').innerHTML = steps.map((s, i) => `
        <li class="${s.done ? 'step-done' : ''}">
            <span class="step-mark">${s.done ? '✓' : i + 1}</span>
            <div><div class="step-title">${s.title}</div><div class="step-body">${s.body}</div></div>
        </li>`).join('');
}

function renderRepositories() {
    const list = document.getElementById('repositoriesList');
    const link = document.getElementById('installAppLink');
    if (installUrl) { link.href = installUrl; link.hidden = false; }
    if (repositoriesError) {
        list.innerHTML = `<div class="empty-state"><p><strong>Could not load repositories.</strong> ${escapeHtml(repositoriesError)}</p></div>`;
        return;
    }
    if (!cachedRepositories.length) {
        list.innerHTML = `<div class="empty-state">
            <p>No repositories yet. Previews come from the Ephemera GitHub App, so it has to be installed on a repository first. Signing in to this dashboard does not connect one.</p>
            ${installUrl ? `<p><a class="btn btn-primary" href="${escapeHtml(installUrl)}" target="_blank">Install the GitHub App</a></p>` : ''}
            <p class="text-muted text-sm">Installed it already? <button class="btn btn-ghost btn-sm" onclick="refreshRepositories(this)">Refresh repositories</button></p>
            <p class="text-muted text-sm">A repository only appears if you are a collaborator on it.</p>
        </div>`;
        return;
    }
    list.innerHTML = `<table class="table"><thead><tr><th>Repository</th><th>Default branch</th><th></th></tr></thead><tbody>
        ${cachedRepositories.map(r => `<tr>
            <td class="mono"><a href="${escapeHtml(r.html_url)}" target="_blank" class="text-muted">${escapeHtml(r.full_name)}</a>${r.private ? ' <span class="text-muted text-sm">private</span>' : ''}</td>
            <td class="mono text-muted">${escapeHtml(r.default_branch)}</td>
            <td style="text-align:right;"><button class="btn btn-primary btn-sm" onclick="selectRepository('${escapeHtml(r.full_name)}')">Check setup</button></td>
        </tr>`).join('')}
    </tbody></table>`;
}

async function selectRepository(fullName) {
    selectedRepo = fullName;
    const [owner, repo] = fullName.split('/');
    const setupCard = document.getElementById('repoSetupCard');
    const pullsCard = document.getElementById('repoPullsCard');
    setupCard.hidden = false;
    pullsCard.hidden = false;
    document.getElementById('repoRegistriesCard').hidden = false;
    document.getElementById('repoAccessCard').hidden = false;
    loadRegistries(owner, repo);
    loadAccess(owner, repo);
    document.getElementById('repoSetupTitle').textContent = `Setup check: ${fullName}`;
    document.getElementById('repoSetupRef').textContent = '';
    document.getElementById('repoSetupBody').innerHTML = '<div class="loading-spinner"><div class="spinner"></div></div>';
    document.getElementById('repoPullsBody').innerHTML = '<div class="loading-spinner"><div class="spinner"></div></div>';
    setupCard.scrollIntoView({ behavior: 'smooth', block: 'start' });
    await Promise.all([
        runSetupCheck(owner, repo),
        loadPulls(owner, repo),
    ]);
}

// Re-run the setup check at a pull request's latest commit, so a fix made
// inside the PR is what gets checked rather than the default branch.
async function checkPull(fullName, prNumber) {
    const [owner, repo] = fullName.split('/');
    document.getElementById('repoSetupBody').innerHTML = '<div class="loading-spinner"><div class="spinner"></div></div>';
    document.getElementById('repoSetupCard').scrollIntoView({ behavior: 'smooth', block: 'start' });
    await runSetupCheck(owner, repo, prNumber);
}

async function runSetupCheck(owner, repo, prNumber = null) {
    const body = document.getElementById('repoSetupBody');
    try {
        const query = prNumber ? `?pr=${prNumber}` : '';
        const r = await apiCall(`/api/v1/repositories/${encodeURIComponent(owner)}/${encodeURIComponent(repo)}/check${query}`);
        const where = r.ref_label || r.ref;
        document.getElementById('repoSetupRef').textContent = r.compose_file ? `${r.compose_file} at ${where}` : `at ${where}`;
        const back = prNumber
            ? ` <a href="#" onclick="runSetupCheck('${escapeHtml(owner)}', '${escapeHtml(repo)}'); return false;" style="color:#6366f1;">Check the default branch instead</a>`
            : '';
        const icon = { ok: '✓', warning: '!', error: '✕' };
        const order = { error: 0, warning: 1, ok: 2 };
        const checks = [...r.checks].sort((a, b) => order[a.level] - order[b.level]);
        // Configuration only: whether CI published the image and the cluster
        // can pull it is only known once a preview deploys.
        const guideButton = r.needs_image_setup
            ? `<p style="margin:0 20px 16px;"><button class="btn btn-primary btn-sm" onclick="openSetupGuide('${escapeHtml(owner)}', '${escapeHtml(repo)}'${prNumber ? `, ${prNumber}` : ''})">Set up image builds</button>
               <span class="text-muted text-sm">Generates the CI workflow and compose lines for this repository.</span></p>`
            : '';
        const verdict = r.ready
            ? `<p class="notice" style="margin:16px 20px;"><strong>Configuration checks passed.</strong> Image availability is verified when a preview deploys: CI must push each image before Ephemera can start it. Open a pull request, or create a preview below.${back}</p>`
            : `<p class="notice" style="margin:16px 20px;"><strong>Fix the errors below before previews can work.</strong> Warnings will not stop a preview but may make it behave differently from docker compose.${back}</p>`;
        const services = r.services.length ? `<table class="table"><thead><tr><th>Service</th><th>Image</th><th>Deploys</th><th>Tag per commit</th><th>Link</th></tr></thead><tbody>
            ${r.services.map(s => `<tr>
                <td class="mono">${escapeHtml(s.name)}</td>
                <td class="mono text-muted text-sm">${escapeHtml(s.image || '-')}</td>
                <td>${s.deployable ? 'yes' : '<span style="color:#ef4444;">no</span>'}</td>
                <td>${s.commit_image ? 'configured' : '<span class="text-muted">no</span>'}</td>
                <td>${s.public ? (s.primary ? '<strong>Open preview</strong>' : 'yes') : '<span class="text-muted">-</span>'}</td>
            </tr>`).join('')}</tbody></table>` : '';
        body.innerHTML = verdict + guideButton + checks.map(c => `
            <div class="check check-${c.level}">
                <span class="check-icon">${icon[c.level]}</span>
                <div>
                    <div class="check-title">${escapeHtml(c.title)}</div>
                    ${c.detail ? `<div class="check-detail">${escapeHtml(c.detail)}</div>` : ''}
                    ${c.fix ? `<div class="check-fix">${escapeHtml(c.fix)}</div>` : ''}
                </div>
            </div>`).join('') + services;
    } catch (e) {
        body.innerHTML = `<div class="empty-state"><p><strong>Setup check failed.</strong> ${escapeHtml(e.message)}</p></div>`;
    }
}

// ─── Image setup guide ──────────────────────────────────

let guideTexts = {};

async function openSetupGuide(owner, repo, prNumber = null) {
    const body = document.getElementById('guideBody');
    document.getElementById('guideTitle').textContent = `Set up image builds: ${owner}/${repo}`;
    body.innerHTML = '<div class="loading-spinner"><div class="spinner"></div></div>';
    openModal('guideModal');
    try {
        const g = await apiCall(`/api/v1/repositories/${encodeURIComponent(owner)}/${encodeURIComponent(repo)}/setup-guide${prNumber ? `?pr=${prNumber}` : ''}`);
        renderSetupGuide(g);
    } catch (e) {
        body.innerHTML = `<div class="empty-state"><p><strong>Could not generate the setup.</strong> ${escapeHtml(e.message)}</p></div>`;
    }
}

function codeBlock(key, text) {
    guideTexts[key] = text;
    return `<div class="code-block"><button class="btn btn-ghost btn-sm copy" onclick="copyGuide('${key}', this)">Copy</button><pre>${escapeHtml(text)}</pre></div>`;
}

function renderSetupGuide(g) {
    const body = document.getElementById('guideBody');
    guideTexts = {};
    if (g.status !== 'needs_setup') {
        body.innerHTML = `<p style="color:#bbb;">${richText(g.message)}</p>`;
        return;
    }
    const services = g.services.map(s => `<code>${escapeHtml(s.name)}</code>`).join(', ');
    body.innerHTML = `
        <p style="color:#bbb;margin-top:0;">${richText(g.message)} Ephemera runs images; it doesn't build them. These changes make your CI build ${services} for every commit, so each preview runs that commit's code.</p>
        <div class="guide-step">
            <h3><span class="num">1</span>Add this workflow</h3>
            <p>Create <span class="code-path">${escapeHtml(g.workflow_path)}</span> with:</p>
            ${codeBlock('workflow', g.workflow)}
        </div>
        <div class="guide-step">
            <h3><span class="num">2</span>Point docker-compose.yml at the images</h3>
            <p>Add the <code>image:</code> line to each service below. Ephemera replaces <code>\${EPHEMERA_SHA}</code> with the pull request's commit.</p>
            ${codeBlock('compose', g.compose_snippet)}
        </div>
        <div class="guide-step">
            <h3><span class="num">3</span>Give Ephemera access to the images</h3>
            <ol>${g.registry_steps.map(step => `<li>${richText(step)}</li>`).join('')}</ol>
            <p class="text-sm"><a href="${escapeHtml(g.docs_url)}" target="_blank" style="color:#6366f1;">GitHub's guide to package visibility</a></p>
        </div>
        ${g.notes.map(n => `<div class="guide-note">${richText(n)}</div>`).join('')}`;
}

async function copyGuide(key, button) {
    const text = guideTexts[key] || '';
    try {
        await navigator.clipboard.writeText(text);
    } catch (e) {
        // Clipboard API blocked (e.g. not a secure context): select the text instead.
        const pre = button.parentElement.querySelector('pre');
        const range = document.createRange();
        range.selectNodeContents(pre);
        const sel = window.getSelection();
        sel.removeAllRanges();
        sel.addRange(range);
        showToast('Selected. Press Ctrl+C (or Cmd+C) to copy.');
        return;
    }
    const old = button.textContent;
    button.textContent = 'Copied';
    setTimeout(() => { button.textContent = old; }, 1500);
}

// ─── Preview access (protected links) ───────────────────

function accessHTML(owner, repo, s) {
    const option = (value, title, detail) => `
        <label style="display:flex;gap:10px;align-items:flex-start;padding:10px 20px;cursor:pointer;">
            <input type="radio" name="previewAccess" value="${value}" ${String(s.protect_previews) === value ? 'checked' : ''}
                   onchange="saveAccess('${escapeHtml(owner)}', '${escapeHtml(repo)}', this.value === 'true')" style="margin-top:4px;">
            <span><strong style="color:#fff;">${title}</strong><br><span class="text-muted text-sm">${detail}</span></span>
        </label>`;
    return `<div style="padding:6px 0;">
        ${option('false', 'Anyone with the link', 'Preview links are public. Fine for open-source work and demos without real data.')}
        ${option('true', 'Collaborators only', 'Viewers sign in with GitHub; only the PR author, the repository\'s collaborators and Ephemera admins get in.')}
    </div><p class="reg-help" id="repoAccessState">${accessStateText(s)}</p>
    ${s.previews_failed ? `<p style="padding:0 20px 16px;"><button class="btn btn-primary btn-sm" onclick="saveAccess('${escapeHtml(owner)}', '${escapeHtml(repo)}', ${s.protect_previews})">Try again</button></p>` : ''}`;
}

// Whether the chosen access is in effect on every running preview yet.
function accessStateText(s) {
    const what = s.protect_previews ? 'collaborators-only' : 'public';
    if (s.previews_failed) {
        const still = s.protect_previews ? 'still public' : 'may still ask for sign-in';
        return `Couldn't change ${s.previews_failed} of ${s.previews} running preview${s.previews === 1 ? '' : 's'}: ${s.previews_failed === 1 ? 'it is' : 'they are'} ${still}. Use Try again below.`;
    }
    if (!s.previews) return `No running previews. New previews will be ${what}.`;
    if (s.previews_applied >= s.previews) {
        return `In effect: ${s.previews === 1 ? 'the running preview is' : `all ${s.previews} running previews are`} ${what}.`;
    }
    return `Applying to running previews: ${s.previews_applied} of ${s.previews} ${what} so far…`;
}

let accessPoll = null;

async function loadAccess(owner, repo) {
    const body = document.getElementById('repoAccessBody');
    try {
        const s = await apiCall(`/api/v1/repositories/${encodeURIComponent(owner)}/${encodeURIComponent(repo)}/settings`);
        body.innerHTML = accessHTML(owner, repo, s);
        document.getElementById('repoAccessMeta').textContent = s.updated_by_login ? `Set by ${s.updated_by_login} ${timeAgo(s.updated_at)}` : '';
        pollAccess(owner, repo, s);
    } catch (e) {
        body.innerHTML = `<div class="empty-state"><p>Could not load preview access. ${escapeHtml(e.message)}</p></div>`;
    }
}

// Until every running preview has the chosen access, check every 2 seconds
// (for up to a minute) so the card says when the change is in effect.
function pollAccess(owner, repo, s, started = Date.now()) {
    if (accessPoll) clearTimeout(accessPoll);
    if (!s.previews || s.previews_applied >= s.previews || s.previews_applied + s.previews_failed >= s.previews) return;
    if (Date.now() - started > 60000) {
        const el = document.getElementById('repoAccessState');
        if (el) el.textContent += ' This is taking longer than usual (a preview may be mid-deploy); reload to see the latest.';
        return;
    }
    accessPoll = setTimeout(async () => {
        try {
            const next = await apiCall(`/api/v1/repositories/${encodeURIComponent(owner)}/${encodeURIComponent(repo)}/settings`);
            if (next.previews_failed) { await loadAccess(owner, repo); return; }  // shows Try again
            const el = document.getElementById('repoAccessState');
            if (el) el.textContent = accessStateText(next);
            pollAccess(owner, repo, next, started);
        } catch (e) { /* the next load retries */ }
    }, 2000);
}

async function saveAccess(owner, repo, protect) {
    try {
        await apiCall(`/api/v1/repositories/${encodeURIComponent(owner)}/${encodeURIComponent(repo)}/settings`, {
            method: 'PUT', body: JSON.stringify({ protect_previews: protect }),
        });
        showToast(protect
            ? 'Making previews collaborators-only now.'
            : 'Making preview links public again now.');
        await loadAccess(owner, repo);
    } catch (e) {
        showToast('Could not change preview access: ' + e.message, 'error');
        await loadAccess(owner, repo);
    }
}

// ─── Private images (registry credentials) ──────────────

const REGISTRY_HELP = {
    'ghcr.io': 'GitHub Container Registry: your GitHub username and a personal access token (classic) with only the read:packages scope.',
    'docker.io': 'Docker Hub: your Docker ID and an access token with Read-only permission.',
    'other': 'The registry host (for example us-docker.pkg.dev), a username and a read-only token or password.',
};

async function loadRegistries(owner, repo) {
    const body = document.getElementById('repoRegistriesBody');
    let creds = [];
    try {
        creds = await apiCall(`/api/v1/repositories/${encodeURIComponent(owner)}/${encodeURIComponent(repo)}/registries`) || [];
    } catch (e) {
        body.innerHTML = `<div class="empty-state"><p>Could not load registry tokens. ${escapeHtml(e.message)}</p></div>`;
        return;
    }
    const list = creds.length ? `<table class="table"><thead><tr><th>Registry</th><th>Username</th><th>Added by</th><th>Updated</th><th></th></tr></thead><tbody>
        ${creds.map(c => `<tr>
            <td class="mono">${escapeHtml(c.registry)}</td>
            <td class="mono text-muted">${escapeHtml(c.username)}</td>
            <td class="text-muted">${escapeHtml(c.created_by_login || '-')}</td>
            <td class="text-muted text-sm">${timeAgo(c.updated_at)}</td>
            <td style="text-align:right;"><button class="btn btn-danger btn-sm" onclick="deleteRegistry('${escapeHtml(owner)}', '${escapeHtml(repo)}', ${c.id}, '${escapeHtml(c.registry)}')">Remove</button></td>
        </tr>`).join('')}</tbody></table>`
        : '<p class="reg-help" style="padding-top:16px;">No tokens yet: previews can only pull public images. Add one to keep your images private.</p>';
    body.innerHTML = list + `
        <form class="reg-form" onsubmit="saveRegistry(event, '${escapeHtml(owner)}', '${escapeHtml(repo)}')">
            <div><label for="regKind">Registry</label>
                <select id="regKind" onchange="registryKindChanged()">
                    <option value="ghcr.io">GitHub Container Registry (ghcr.io)</option>
                    <option value="docker.io">Docker Hub</option>
                    <option value="other">Other registry…</option>
                </select></div>
            <div id="regHostWrap" hidden><label for="regHost">Registry host</label><input id="regHost" placeholder="us-docker.pkg.dev" autocomplete="off"></div>
            <div><label for="regUser">Username</label><input id="regUser" autocomplete="off" required></div>
            <div><label for="regToken">Read-only token</label><input id="regToken" type="password" autocomplete="new-password" required></div>
            <div><button class="btn btn-primary" type="submit">Save token</button></div>
        </form>
        <p class="reg-help" id="regHelp">${escapeHtml(REGISTRY_HELP['ghcr.io'])} Tokens are encrypted and never shown again; previews use them from their next deploy.</p>`;
}

function registryKindChanged() {
    const kind = document.getElementById('regKind').value;
    document.getElementById('regHostWrap').hidden = kind !== 'other';
    document.getElementById('regHelp').textContent = REGISTRY_HELP[kind] + ' Tokens are encrypted and never shown again; previews use them from their next deploy.';
}

async function saveRegistry(event, owner, repo) {
    event.preventDefault();
    const kind = document.getElementById('regKind').value;
    const registry = kind === 'other' ? document.getElementById('regHost').value.trim() : kind;
    const button = event.target.querySelector('button[type=submit]');
    button.disabled = true;
    try {
        await apiCall(`/api/v1/repositories/${encodeURIComponent(owner)}/${encodeURIComponent(repo)}/registries`, {
            method: 'PUT',
            body: JSON.stringify({ registry, username: document.getElementById('regUser').value.trim(), token: document.getElementById('regToken').value }),
        });
        showToast(`Saved. Previews of ${owner}/${repo} pull from ${registry} with it from their next deploy.`);
        await loadRegistries(owner, repo);
    } catch (e) {
        showToast('Could not save the token: ' + e.message, 'error');
        button.disabled = false;
    }
}

async function deleteRegistry(owner, repo, id, registry) {
    if (!confirm(`Remove the ${registry} token? Previews that need it will fail to pull private images from their next deploy.`)) return;
    try {
        await apiCall(`/api/v1/repositories/${encodeURIComponent(owner)}/${encodeURIComponent(repo)}/registries/${id}`, { method: 'DELETE' });
        showToast(`Removed the ${registry} token.`);
        await loadRegistries(owner, repo);
    } catch (e) {
        showToast('Could not remove it: ' + e.message, 'error');
    }
}

async function loadUsage(owner, repo) {
    const el = document.getElementById('repoUsage');
    try {
        const u = await apiCall(`/api/v1/repositories/${encodeURIComponent(owner)}/${encodeURIComponent(repo)}/usage`);
        el.textContent = u.limit > 0
            ? `${u.used} of ${u.limit} previews used`
            : `${u.used} preview${u.used === 1 ? '' : 's'} running (no limit)`;
        el.style.color = u.limit > 0 && u.used >= u.limit ? '#f87171' : '';
        el.title = u.limit > 0 && u.used >= u.limit
            ? 'At the limit: stop a preview you don\'t need (Details, then Stop preview) to make room.' : '';
    } catch (e) {
        el.textContent = '';
    }
}

async function loadPulls(owner, repo) {
    loadUsage(owner, repo);
    const body = document.getElementById('repoPullsBody');
    try {
        const pulls = await apiCall(`/api/v1/repositories/${encodeURIComponent(owner)}/${encodeURIComponent(repo)}/pulls`);
        cachedPulls = pulls || [];
        if (!pulls.length) {
            body.innerHTML = '<div class="empty-state"><p>No open pull requests. Open one and its preview is created automatically.</p></div>';
            return;
        }
        body.innerHTML = `<table class="table"><thead><tr><th>PR</th><th>Title</th><th>Author</th><th>Preview</th><th></th></tr></thead><tbody>
            ${pulls.map(p => {
                const status = p.environment_status;
                const live = ['ready', 'provisioning', 'pending', 'updating'].includes(status);
                const action = status === 'ready' && p.environment_url
                    ? `<a class="btn btn-ghost btn-sm" href="${escapeHtml(p.environment_url)}" target="_blank">Open preview</a>`
                    : live ? ''
                    : `<button class="btn btn-primary btn-sm" onclick="createPreview('${escapeHtml(owner)}/${escapeHtml(repo)}', ${p.number}, this)">${status === 'failed' ? 'Retry preview' : 'Create preview'}</button>`;
                const check = `<button class="btn btn-ghost btn-sm" onclick="checkPull('${escapeHtml(owner)}/${escapeHtml(repo)}', ${p.number})">Check this PR</button>`;
                return `<tr>
                    <td>#${p.number}</td>
                    <td>${escapeHtml(p.title)}</td>
                    <td class="text-muted">${escapeHtml(p.author_login)}</td>
                    <td>${status ? statusBadge(status) : '<span class="text-muted">none</span>'}</td>
                    <td style="text-align:right;white-space:nowrap;">${check} ${action}</td>
                </tr>`;
            }).join('')}</tbody></table>`;
    } catch (e) {
        body.innerHTML = `<div class="empty-state"><p><strong>Could not load pull requests.</strong> ${escapeHtml(e.message)}</p></div>`;
    }
}

async function createPreview(fullName, prNumber, button) {
    if (button) { button.disabled = true; button.textContent = 'Requesting…'; }
    try {
        await apiCall('/api/v1/environments/', {
            method: 'POST',
            body: JSON.stringify({ repository_full_name: fullName, pr_number: prNumber }),
        });
        showToast(`Preview requested for #${prNumber}. This list updates as it deploys.`);
        const [owner, repo] = fullName.split('/');
        await loadPulls(owner, repo);
        startAutoRefresh();
    } catch (e) {
        showToast('Could not create preview: ' + e.message, 'error');
        if (button) { button.disabled = false; button.textContent = 'Create preview'; }
    }
}

function renderOverview() {
    // What a user wants to know at a glance: what they can open, what is on
    // its way, what needs them, and whether their repositories are connected.
    const count = statuses => cachedEnvironments.filter(e => statuses.includes((e.status || '').toLowerCase())).length;
    const attention = count(['failed']);
    document.getElementById('statReady').textContent = count(['ready']);
    document.getElementById('statDeploying').textContent = count(['pending', 'provisioning', 'updating']);
    document.getElementById('statAttention').textContent = attention;
    document.getElementById('statAttention').parentElement.classList.toggle('stat-attention', attention > 0);
    document.getElementById('statRepos').textContent = Array.isArray(cachedRepositories) ? cachedRepositories.length : '-';

    renderGettingStarted();

    // Show only 5 most recent environments
    document.getElementById('recentEnvsTable').innerHTML = envTableHTML(cachedEnvironments.slice(0, 5));
}

function renderEnvironments() {
    document.getElementById('allEnvsTable').innerHTML = envTableHTML(cachedEnvironments);
}

function renderCredentials() {
    const container = document.getElementById('credentialsList');

    if (cachedCredentials.length === 0) {
        container.innerHTML = `
            <div class="empty-state">
                <svg fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="1.5">
                    <path stroke-linecap="round" stroke-linejoin="round" d="M15 7a2 2 0 012 2m4 0a6 6 0 01-7.743 5.743L11 17H9v2H7v2H4a1 1 0 01-1-1v-2.586a1 1 0 01.293-.707l5.964-5.964A6 6 0 1121 9z"/>
                </svg>
                <p>No stored credentials. Previews from pull requests do not need any: Ephemera deploys them with its own cluster access. Store credentials here only for your own CI workflows that fetch them with an API token.</p>
                <button class="btn btn-primary" onclick="openModal('credentialModal')">Add Credentials</button>
            </div>
        `;
        return;
    }

    container.innerHTML = `
        <table class="table">
            <thead>
                <tr>
                    <th>Name</th>
                    <th>Provider</th>
                    <th>Status</th>
                    <th>Created</th>
                    <th></th>
                </tr>
            </thead>
            <tbody>
                ${cachedCredentials.map(cred => `
                    <tr>
                        <td>${escapeHtml(cred.name || cred.provider.toUpperCase())}</td>
                        <td><span class="mono">${escapeHtml(cred.provider.toUpperCase())}</span></td>
                        <td>${activeBadge(cred.is_active)}</td>
                        <td class="text-muted text-sm">${timeAgo(cred.created_at)}</td>
                        <td>
                            <button class="btn btn-danger btn-sm" onclick="deleteCredential(${cred.id})">Delete</button>
                        </td>
                    </tr>
                `).join('')}
            </tbody>
        </table>
    `;
}

function renderTokens() {
    const container = document.getElementById('tokensList');

    if (cachedTokens.length === 0) {
        container.innerHTML = `
            <div class="empty-state">
                <svg fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="1.5">
                    <path stroke-linecap="round" stroke-linejoin="round" d="M7 20l4-16m2 16l4-16M6 9h14M4 15h14"/>
                </svg>
                <p>No API tokens yet. Generate a token to use in your GitHub workflows.</p>
                <button class="btn btn-primary" onclick="openModal('tokenModal')">Generate Token</button>
            </div>
        `;
        return;
    }

    container.innerHTML = `
        <table class="table">
            <thead>
                <tr>
                    <th>Name</th>
                    <th>Prefix</th>
                    <th>Status</th>
                    <th>Created</th>
                    <th></th>
                </tr>
            </thead>
            <tbody>
                ${cachedTokens.map(token => `
                    <tr>
                        <td>${escapeHtml(token.name || 'API Token')}</td>
                        <td class="mono text-muted">${escapeHtml(token.token_prefix)}...</td>
                        <td>${activeBadge(token.is_active && !token.revoked_at)}</td>
                        <td class="text-muted text-sm">${timeAgo(token.created_at)}</td>
                        <td>
                            ${token.is_active && !token.revoked_at
                                ? `<button class="btn btn-danger btn-sm" onclick="revokeToken(${token.id})">Revoke</button>`
                                : ''}
                        </td>
                    </tr>
                `).join('')}
            </tbody>
        </table>
    `;
}

// ─── Actions ────────────────────────────────────────────

async function addCredential() {
    const provider = document.getElementById('credProvider').value;
    const name = document.getElementById('credName').value;
    const credJson = document.getElementById('credJson').value;

    if (!credJson.trim()) {
        showToast('Please paste your service account JSON', 'error');
        return;
    }

    try {
        JSON.parse(credJson);
    } catch (e) {
        showToast('Invalid JSON format', 'error');
        return;
    }

    try {
        await apiCall('/api/v1/credentials/', {
            method: 'POST',
            body: JSON.stringify({
                provider: provider,
                name: name || null,
                credentials_json: credJson
            })
        });

        closeModal('credentialModal');
        document.getElementById('credName').value = '';
        document.getElementById('credJson').value = '';
        showToast('Credentials added successfully');
        await loadCredentials();
        renderCredentials();
    } catch (e) {
        showToast('Failed to add credentials: ' + e.message, 'error');
    }
}

async function deleteCredential(id) {
    if (!confirm('Are you sure you want to delete this credential?')) return;

    try {
        await apiCall(`/api/v1/credentials/${id}`, { method: 'DELETE' });
        showToast('Credential deleted');
        await loadCredentials();
        renderCredentials();
    } catch (e) {
        showToast('Failed to delete credential: ' + e.message, 'error');
    }
}

async function generateToken() {
    const name = document.getElementById('tokenName').value;
    const desc = document.getElementById('tokenDesc').value;

    if (!name.trim()) {
        showToast('Please enter a token name', 'error');
        return;
    }

    try {
        const token = await apiCall('/api/v1/tokens/', {
            method: 'POST',
            body: JSON.stringify({
                name: name,
                description: desc || 'Generated from web dashboard'
            })
        });

        closeModal('tokenModal');
        document.getElementById('tokenName').value = '';
        document.getElementById('tokenDesc').value = '';

        // Show the new token
        document.getElementById('newTokenValue').textContent = token.token;
        openModal('tokenDisplayModal');

        await loadTokens();
        renderTokens();
    } catch (e) {
        showToast('Failed to generate token: ' + e.message, 'error');
    }
}

async function revokeToken(id) {
    if (!confirm('Are you sure you want to revoke this token?')) return;

    try {
        await apiCall(`/api/v1/tokens/${id}`, { method: 'DELETE' });
        showToast('Token revoked');
        await loadTokens();
        renderTokens();
    } catch (e) {
        showToast('Failed to revoke token: ' + e.message, 'error');
    }
}

function copyToken() {
    const tokenValue = document.getElementById('newTokenValue').textContent;
    navigator.clipboard.writeText(tokenValue).then(() => {
        showToast('Token copied to clipboard');
    });
}

// ─── Utilities ──────────────────────────────────────────

function escapeHtml(text) {
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}

// ─── Initialize ─────────────────────────────────────────

window.addEventListener('DOMContentLoaded', () => {
    loadUserInfo();
    navigate();
    startAutoRefresh();
});
