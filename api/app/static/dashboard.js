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
        const error = await response.json();
        throw new Error(error.detail || 'API request failed');
    }

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
    }
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
            await Promise.all([loadEnvironments(), loadCredentials(), loadTokens(), loadRepositories()]);
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
            if (hash === 'overview') {
                await loadEnvironments();
                renderOverview();
            } else if (hash === 'environments') {
                await loadEnvironments();
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

function statusBadge(status) {
    const s = (status || 'pending').toLowerCase();
    return `<span class="badge badge-${s}"><span class="badge-dot"></span>${s}</span>`;
}

function activeBadge(isActive) {
    return isActive
        ? '<span class="badge badge-active"><span class="badge-dot"></span>active</span>'
        : '<span class="badge badge-inactive"><span class="badge-dot"></span>inactive</span>';
}

function timeAgo(dateStr) {
    if (!dateStr) return '-';
    const seconds = Math.floor((Date.now() - new Date(dateStr).getTime()) / 1000);
    if (seconds < 60) return 'just now';
    if (seconds < 3600) return `${Math.floor(seconds / 60)}m ago`;
    if (seconds < 86400) return `${Math.floor(seconds / 3600)}h ago`;
    return `${Math.floor(seconds / 86400)}d ago`;
}

function envErrorHTML(env) {
    if ((env.status || '').toLowerCase() !== 'failed' || !env.error_message) return '';
    const full = env.error_message;
    const short = full.length > 140 ? full.slice(0, 140) + '…' : full;
    return `<div class="text-muted text-sm" title="${escapeHtml(full)}" style="margin-top:4px;max-width:420px;">${escapeHtml(short)}</div>`;
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
                </tr>
            </thead>
            <tbody>
                ${envs.map(env => `
                    <tr>
                        <td class="mono">${escapeHtml(env.repository_full_name || '-')}</td>
                        <td>#${env.pr_number || '-'}</td>
                        <td class="text-muted">${escapeHtml(env.owner_login || '-')}</td>
                        <td class="mono text-muted">${escapeHtml(env.branch_name || '-')}</td>
                        <td>${statusBadge(env.status)}${envErrorHTML(env)}</td>
                        <td>${envPreviewHTML(env)}</td>
                        <td class="mono text-muted text-sm">${escapeHtml((env.commit_sha || '').slice(0, 8) || '-')}</td>
                        <td class="text-muted text-sm">${timeAgo(env.created_at)}</td>
                    </tr>
                `).join('')}
            </tbody>
        </table>
    `;
}

// ─── Repositories and onboarding ────────────────────────

async function loadRepositories() {
    try {
        const body = await apiCall('/api/v1/repositories');
        cachedRepositories = (body && body.repositories) || [];
        installUrl = body && body.install_url;
        repositoriesError = null;
    } catch (e) {
        cachedRepositories = null;
        repositoriesError = e.message || 'Request failed';
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
            : install + ' Signing in does not connect repositories; installing the App does.' },
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
            <p class="text-muted text-sm">After installing, come back and reload this page.</p>
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

async function runSetupCheck(owner, repo) {
    const body = document.getElementById('repoSetupBody');
    try {
        const r = await apiCall(`/api/v1/repositories/${encodeURIComponent(owner)}/${encodeURIComponent(repo)}/check`);
        document.getElementById('repoSetupRef').textContent = r.compose_file ? `${r.compose_file} on ${r.ref}` : `on ${r.ref}`;
        const icon = { ok: '✓', warning: '!', error: '✕' };
        const order = { error: 0, warning: 1, ok: 2 };
        const checks = [...r.checks].sort((a, b) => order[a.level] - order[b.level]);
        const verdict = r.ready
            ? '<p class="notice" style="margin:16px 20px;">Ready for previews. Open a pull request, or create a preview below.</p>'
            : '<p class="notice" style="margin:16px 20px;">Fix the errors below before previews can work. Warnings will not stop a preview but may make it behave differently from docker compose.</p>';
        const services = r.services.length ? `<table class="table"><thead><tr><th>Service</th><th>Image</th><th>Deploys</th><th>Built per commit</th><th>Link</th></tr></thead><tbody>
            ${r.services.map(s => `<tr>
                <td class="mono">${escapeHtml(s.name)}</td>
                <td class="mono text-muted text-sm">${escapeHtml(s.image || '-')}</td>
                <td>${s.deployable ? 'yes' : '<span style="color:#ef4444;">no</span>'}</td>
                <td>${s.commit_image ? 'yes' : '<span class="text-muted">no</span>'}</td>
                <td>${s.public ? (s.primary ? '<strong>Open preview</strong>' : 'yes') : '<span class="text-muted">-</span>'}</td>
            </tr>`).join('')}</tbody></table>` : '';
        body.innerHTML = verdict + checks.map(c => `
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

async function loadPulls(owner, repo) {
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
                return `<tr>
                    <td>#${p.number}</td>
                    <td>${escapeHtml(p.title)}</td>
                    <td class="text-muted">${escapeHtml(p.author_login)}</td>
                    <td>${status ? statusBadge(status) : '<span class="text-muted">none</span>'}</td>
                    <td style="text-align:right;">${action}</td>
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
    const active = cachedEnvironments.filter(e => ['ready', 'provisioning', 'pending', 'updating'].includes((e.status || '').toLowerCase()));

    document.getElementById('statTotalEnvs').textContent = cachedEnvironments.length;
    document.getElementById('statActiveEnvs').textContent = active.length;
    document.getElementById('statCredentials').textContent = cachedCredentials.length;
    document.getElementById('statTokens').textContent = cachedTokens.length;

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
        // Update overview stat
        document.getElementById('statCredentials').textContent = cachedCredentials.length;
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
        document.getElementById('statCredentials').textContent = cachedCredentials.length;
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
        document.getElementById('statTokens').textContent = cachedTokens.length;
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
        document.getElementById('statTokens').textContent = cachedTokens.length;
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
