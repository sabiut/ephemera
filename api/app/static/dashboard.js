// Ephemera Dashboard

const API_BASE = window.location.origin;

// ─── Auth ───────────────────────────────────────────────

function getToken() {
    const token = localStorage.getItem('ephemera_token');
    if (!token) {
        window.location.href = '/';
        return null;
    }
    return token;
}

function logout() {
    localStorage.removeItem('ephemera_token');
    window.location.href = '/';
}

// ─── API Helper ─────────────────────────────────────────

async function apiCall(endpoint, options = {}) {
    const token = getToken();
    if (!token) return;

    const headers = {
        'Authorization': `Bearer ${token}`,
        'Content-Type': 'application/json',
        ...options.headers
    };

    const response = await fetch(`${API_BASE}${endpoint}`, {
        ...options,
        headers
    });

    if (response.status === 401) {
        localStorage.removeItem('ephemera_token');
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

const views = ['overview', 'environments', 'credentials', 'tokens'];

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
let cachedCredentials = [];
let cachedTokens = [];
let refreshInterval = null;

async function loadView(view) {
    switch (view) {
        case 'overview':
            await Promise.all([loadEnvironments(), loadCredentials(), loadTokens()]);
            renderOverview();
            break;
        case 'environments':
            await loadEnvironments();
            renderEnvironments();
            startAutoRefresh();
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

function startAutoRefresh() {
    if (refreshInterval) clearInterval(refreshInterval);
    refreshInterval = setInterval(async () => {
        const hash = window.location.hash.slice(1) || 'overview';
        if (hash === 'environments') {
            await loadEnvironments();
            renderEnvironments();
        }
    }, 30000);
}

async function loadEnvironments() {
    try {
        cachedEnvironments = await apiCall('/api/v1/environments/') || [];
    } catch (e) {
        console.error('Failed to load environments:', e);
        cachedEnvironments = [];
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

function envTableHTML(envs) {
    if (!envs || envs.length === 0) {
        return `
            <div class="empty-state">
                <svg fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="1.5">
                    <path stroke-linecap="round" stroke-linejoin="round" d="M5 12h14M5 12a2 2 0 01-2-2V6a2 2 0 012-2h14a2 2 0 012 2v4a2 2 0 01-2 2M5 12a2 2 0 00-2 2v4a2 2 0 002 2h14a2 2 0 002-2v-4a2 2 0 00-2-2"/>
                </svg>
                <p>No environments yet. Create a PR on a connected repository to get started.</p>
            </div>
        `;
    }

    return `
        <table class="table">
            <thead>
                <tr>
                    <th>Repository</th>
                    <th>PR</th>
                    <th>Branch</th>
                    <th>Status</th>
                    <th>URL</th>
                    <th>Created</th>
                </tr>
            </thead>
            <tbody>
                ${envs.map(env => `
                    <tr>
                        <td class="mono">${escapeHtml(env.repository_full_name || '-')}</td>
                        <td>#${env.pr_number || '-'}</td>
                        <td class="mono text-muted">${escapeHtml(env.branch_name || '-')}</td>
                        <td>${statusBadge(env.status)}</td>
                        <td>${env.environment_url
                            ? `<a href="${escapeHtml(env.environment_url)}" target="_blank" style="color: #6366f1;">${escapeHtml(env.environment_url)}</a>`
                            : '<span class="text-muted">-</span>'}</td>
                        <td class="text-muted text-sm">${timeAgo(env.created_at)}</td>
                    </tr>
                `).join('')}
            </tbody>
        </table>
    `;
}

function renderOverview() {
    const active = cachedEnvironments.filter(e => ['ready', 'provisioning', 'pending', 'updating'].includes((e.status || '').toLowerCase()));

    document.getElementById('statTotalEnvs').textContent = cachedEnvironments.length;
    document.getElementById('statActiveEnvs').textContent = active.length;
    document.getElementById('statCredentials').textContent = cachedCredentials.length;
    document.getElementById('statTokens').textContent = cachedTokens.length;

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
                <p>No cloud credentials yet. Add your credentials to enable environment provisioning.</p>
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
});
