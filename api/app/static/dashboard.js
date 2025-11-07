// Dashboard JavaScript

const API_BASE = window.location.origin;

// Get token from localStorage
function getToken() {
    const token = localStorage.getItem('ephemera_token');
    if (!token) {
        window.location.href = '/';
        return null;
    }
    return token;
}

// API helper
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
        // Token invalid, redirect to login
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

// Load user info
async function loadUserInfo() {
    try {
        const user = await apiCall('/auth/me');
        document.getElementById('userName').textContent = user.github_login;
        document.getElementById('userAvatar').src = user.avatar_url;
    } catch (error) {
        console.error('Failed to load user info:', error);
    }
}

// Load credentials
async function loadCredentials() {
    try {
        const credentials = await apiCall('/api/v1/credentials/');
        const container = document.getElementById('credentialsList');

        if (credentials.length === 0) {
            container.innerHTML = '<p style="color: #666;">No credentials added yet. Add your cloud credentials to get started.</p>';
        } else {
            container.innerHTML = '<ul class="list">' +
                credentials.map(cred => `
                    <li class="list-item">
                        <div>
                            <strong>${cred.name || cred.provider.toUpperCase()}</strong>
                            <br>
                            <small style="color: #666;">Provider: ${cred.provider.toUpperCase()}</small>
                        </div>
                        <div>
                            <span class="badge ${cred.is_active ? '' : 'badge-inactive'}">
                                ${cred.is_active ? 'Active' : 'Inactive'}
                            </span>
                        </div>
                    </li>
                `).join('') +
                '</ul>';
        }
    } catch (error) {
        console.error('Failed to load credentials:', error);
        document.getElementById('credentialsList').innerHTML =
            '<p style="color: #dc3545;">Failed to load credentials</p>';
    }
}

// Load tokens
async function loadTokens() {
    try {
        const tokens = await apiCall('/api/v1/tokens/');
        const container = document.getElementById('tokensList');

        if (tokens.length === 0) {
            container.innerHTML = '<p style="color: #666;">No API tokens yet. Generate one to use in your GitHub workflows.</p>';
        } else {
            container.innerHTML = '<ul class="list">' +
                tokens.map(token => `
                    <li class="list-item">
                        <div>
                            <strong>${token.name || 'API Token'}</strong>
                            <br>
                            <small style="color: #666;">Prefix: ${token.token_prefix}...</small>
                            <br>
                            <small style="color: #999;">Created: ${new Date(token.created_at).toLocaleDateString()}</small>
                        </div>
                        <div>
                            <span class="badge ${token.is_active && !token.revoked_at ? '' : 'badge-inactive'}">
                                ${token.is_active && !token.revoked_at ? 'Active' : 'Revoked'}
                            </span>
                        </div>
                    </li>
                `).join('') +
                '</ul>';
        }
    } catch (error) {
        console.error('Failed to load tokens:', error);
        document.getElementById('tokensList').innerHTML =
            '<p style="color: #dc3545;">Failed to load tokens</p>';
    }
}

// Show add credential form
function showAddCredentialForm() {
    document.getElementById('addCredentialForm').classList.remove('hidden');
}

// Hide add credential form
function hideAddCredentialForm() {
    document.getElementById('addCredentialForm').classList.add('hidden');
    document.getElementById('credName').value = '';
    document.getElementById('credJson').value = '';
}

// Add credential
async function addCredential() {
    const provider = document.getElementById('credProvider').value;
    const name = document.getElementById('credName').value;
    const credJson = document.getElementById('credJson').value;

    if (!credJson.trim()) {
        alert('Please paste your service account JSON');
        return;
    }

    // Validate JSON
    try {
        JSON.parse(credJson);
    } catch (e) {
        alert('Invalid JSON format. Please check your service account JSON.');
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

        alert('Credentials added successfully!');
        hideAddCredentialForm();
        loadCredentials();
    } catch (error) {
        alert('Failed to add credentials: ' + error.message);
    }
}

// Generate token
async function generateToken() {
    const name = prompt('Enter a name for this token (e.g., "GitHub Actions - my-app"):');
    if (!name) return;

    try {
        const token = await apiCall('/api/v1/tokens/', {
            method: 'POST',
            body: JSON.stringify({
                name: name,
                description: 'Generated from web dashboard'
            })
        });

        // Show the new token
        document.getElementById('newTokenValue').textContent = token.token;
        document.getElementById('newTokenDisplay').classList.remove('hidden');

        // Reload tokens list
        loadTokens();

        // Scroll to token display
        document.getElementById('newTokenDisplay').scrollIntoView({ behavior: 'smooth' });
    } catch (error) {
        alert('Failed to generate token: ' + error.message);
    }
}

// Copy token to clipboard
function copyToken() {
    const tokenValue = document.getElementById('newTokenValue').textContent;
    navigator.clipboard.writeText(tokenValue).then(() => {
        alert('Token copied to clipboard!');
    });
}

// Initialize dashboard
window.addEventListener('DOMContentLoaded', () => {
    loadUserInfo();
    loadCredentials();
    loadTokens();
});
