// ============================================================
// shared.js  —  Zimnat Insurance Claims Portal
// Shared layout, auth, notifications, role-based access
// ============================================================

const API = 'http://127.0.0.1:8000/api';

// ── AUTH HELPERS ─────────────────────────────────────────────
function getToken()   { return localStorage.getItem('access_token'); }
function getUser()    { try { return JSON.parse(localStorage.getItem('user')) || {}; } catch { return {}; } }
function isLoggedIn() { return !!getToken(); }

function requireAuth() {
  if (!isLoggedIn()) { window.location.href = '/'; return false; }
  return true;
}

// ── ROLE-BASED PAGE PROTECTION ────────────────────────────────
// Call at the top of any restricted page.
// e.g. requireRole(['ADMIN']) on manage-users page
// e.g. requireRole(['ADJUSTER','ADMIN']) on adjuster page
function requireRole(allowedRoles) {
  if (!requireAuth()) return false;
  const user = getUser();
  if (!allowedRoles.includes(user.role)) {
    const homeMap = {
      'CLIENT':   '/dashboard/',
      'ADJUSTER': '/adjuster/',
      'ADMIN':    '/manage-users/',
      'GARAGE':   '/garage/',
    };
    window.location.href = homeMap[user.role] || '/';
    return false;
  }
  return true;
}

function logout() {
  const token = localStorage.getItem('refresh_token');
  fetch(`${API}/auth/logout/`, {
    method: 'POST',
    headers: authHeaders(),
    body: JSON.stringify({ refresh: token })
  }).catch(() => {});
  localStorage.clear();
  window.location.href = '/';
}

function authHeaders() {
  return {
    'Content-Type': 'application/json',
    'Authorization': `Bearer ${getToken()}`
  };
}

async function apiFetch(url, options = {}) {
  const res = await fetch(`${API}${url}`, {
    ...options,
    headers: { ...authHeaders(), ...(options.headers || {}) }
  });
  if (res.status === 401) { logout(); return null; }
  return res;
}

// ── ROLE-BASED SIDEBAR NAV ────────────────────────────────────
function getNavForRole(role, activePage) {

  if (role === 'CLIENT') return `
    <div class="nav-section-label">Main</div>
    <a href="/dashboard/" class="nav-item ${activePage==='dashboard'?'active':''}">
      <span class="icon">🏠</span> Dashboard
    </a>
    <a href="/submit-claim/" class="nav-item ${activePage==='submit'?'active':''}">
      <span class="icon">📝</span> Submit Claim
    </a>
    <a href="/track-claim/" class="nav-item ${activePage==='track'?'active':''}">
      <span class="icon">🔍</span> Track Claims
      <span class="badge" id="pending-badge" style="display:none">0</span>
    </a>
    <a href="/reports/" class="nav-item ${activePage==='reports'?'active':''}">
      <span class="icon">📊</span> Reports
    </a>
    <div class="nav-section-label">Account</div>
    <a href="/profile/" class="nav-item ${activePage==='profile'?'active':''}">
      <span class="icon">👤</span> My Profile
    </a>
    <a href="#" class="nav-item" onclick="showNotifPanel()">
      <span class="icon">🔔</span> Notifications
      <span class="badge" id="notif-nav-badge" style="display:none">0</span>
    </a>
    <a href="#" class="nav-item"><span class="icon">⚙️</span> Settings</a>
  `;

  if (role === 'ADJUSTER') return `
    <div class="nav-section-label">Adjuster</div>
    <a href="/adjuster/" class="nav-item ${activePage==='adjuster'?'active':''}">
      <span class="icon">🏠</span> Dashboard
    </a>
    <a href="/monthly-reports/" class="nav-item ${activePage==='reports'?'active':''}">
      <span class="icon">📊</span> Monthly Reports
    </a>
    <div class="nav-section-label">Account</div>
    <a href="/profile/" class="nav-item ${activePage==='profile'?'active':''}">
      <span class="icon">👤</span> My Profile
    </a>
    <a href="#" class="nav-item" onclick="showNotifPanel()">
      <span class="icon">🔔</span> Notifications
      <span class="badge" id="notif-nav-badge" style="display:none">0</span>
    </a>
    <a href="#" class="nav-item"><span class="icon">⚙️</span> Settings</a>
  `;

  if (role === 'ADMIN') return `
    <div class="nav-section-label">Admin</div>
    <a href="/dashboard/" class="nav-item ${activePage==='dashboard'?'active':''}">
      <span class="icon">🏠</span> Dashboard
    </a>
    <a href="/manage-users/" class="nav-item ${activePage==='manage-users'?'active':''}">
      <span class="icon">👥</span> Manage Users
    </a>
    <a href="/adjuster/" class="nav-item ${activePage==='adjuster'?'active':''}">
      <span class="icon">⚖️</span> Claims Review
    </a>
    <a href="/reports/" class="nav-item ${activePage==='reports'?'active':''}">
      <span class="icon">📊</span> Reports
    </a>
    <div class="nav-section-label">Account</div>
    <a href="/profile/" class="nav-item ${activePage==='profile'?'active':''}">
      <span class="icon">👤</span> My Profile
    </a>
    <a href="#" class="nav-item" onclick="showNotifPanel()">
      <span class="icon">🔔</span> Notifications
      <span class="badge" id="notif-nav-badge" style="display:none">0</span>
    </a>
    <a href="#" class="nav-item"><span class="icon">⚙️</span> Settings</a>
  `;

  if (role === 'GARAGE') return `
    <div class="nav-section-label">Garage</div>
    <a href="/garage/" class="nav-item ${activePage==='garage'?'active':''}">
      <span class="icon">🏠</span> Dashboard
    </a>
    <a href="/garage/" class="nav-item ${activePage==='jobs'?'active':''}">
      <span class="icon">🔧</span> Repair Jobs
      <span class="badge" id="pending-badge" style="display:none">0</span>
    </a>
    <div class="nav-section-label">Account</div>
    <a href="/profile/" class="nav-item ${activePage==='profile'?'active':''}">
      <span class="icon">👤</span> My Profile
    </a>
    <a href="#" class="nav-item" onclick="showNotifPanel()">
      <span class="icon">🔔</span> Notifications
      <span class="badge" id="notif-nav-badge" style="display:none">0</span>
    </a>
    <a href="#" class="nav-item"><span class="icon">⚙️</span> Settings</a>
  `;

  return getNavForRole('CLIENT', activePage); // fallback
}

// ── RENDER SIDEBAR ────────────────────────────────────────────
function renderLayout(activePage) {
  if (!requireAuth()) return;

  const user      = getUser();
  const role      = user.role || 'CLIENT';
  const initials  = ((user.first_name||'U')[0] + (user.last_name||'')[0]).toUpperCase();
  const roleLabel = { CLIENT:'Client', ADJUSTER:'Adjuster', ADMIN:'Administrator', GARAGE:'Garage' }[role] || role;

  document.body.insertAdjacentHTML('afterbegin', `
    <aside class="sidebar">
      <div class="sidebar-brand">
        <div class="logo">🛡️</div>
        <div class="brand-text">
          <div class="name">Zimnat Insurance</div>
          <div class="sub">Claims Portal</div>
        </div>
      </div>
      <nav class="sidebar-nav">
        ${getNavForRole(role, activePage)}
      </nav>
      <div class="sidebar-footer">
        <div class="user-card">
          <div class="user-avatar">${initials}</div>
          <div class="user-info">
            <div class="user-name">${user.first_name||''} ${user.last_name||''}</div>
            <div class="user-role">${roleLabel}</div>
          </div>
          <button class="logout-btn" onclick="logout()" title="Logout">⏏</button>
        </div>
      </div>
    </aside>

    <div class="notif-panel" id="notif-panel">
      <div class="notif-header">
        <h3>🔔 Notifications</h3>
        <button class="notif-mark-read" onclick="markAllRead()">Mark all read</button>
      </div>
      <div class="notif-list" id="notif-list">
        <div style="padding:24px;text-align:center;color:#9ca3af;font-size:13px">Loading…</div>
      </div>
    </div>
  `);

  loadNotifications();
  loadPendingCount();

  document.addEventListener('click', e => {
    const panel = document.getElementById('notif-panel');
    const btn   = document.querySelector('.notif-btn');
    if (panel && btn && !panel.contains(e.target) && !btn.contains(e.target)) {
      panel.classList.remove('show');
    }
  });
}

// ── RENDER TOPBAR ──────────────────────────────────────────────
function renderTopbar(title, subtitle = '') {
  const mainContent = document.querySelector('.main-content');
  mainContent.insertAdjacentHTML('afterbegin', `
    <div class="topbar">
      <div class="topbar-title">
        ${title}
        ${subtitle ? `<span>${subtitle}</span>` : ''}
      </div>
      <div class="topbar-search">
        <span>🔍</span>
        <input type="text" placeholder="Search claims…" id="global-search"/>
      </div>
      <div class="topbar-actions">
        <button class="notif-btn" onclick="toggleNotifPanel()" title="Notifications">
          🔔
          <span class="notif-badge" id="notif-count" style="display:none">0</span>
        </button>
      </div>
    </div>
  `);
}

// ── NOTIFICATIONS ──────────────────────────────────────────────
function toggleNotifPanel() { document.getElementById('notif-panel').classList.toggle('show'); }
function showNotifPanel()   { document.getElementById('notif-panel').classList.add('show'); }

async function loadNotifications() {
  try {
    const res   = await apiFetch('/notifications/');
    if (!res) return;
    const data  = await res.json();
    const items = data.results || data;
    const unread = items.filter(n => !n.is_read).length;

    const badge    = document.getElementById('notif-count');
    const navBadge = document.getElementById('notif-nav-badge');
    if (unread > 0) {
      if (badge)    { badge.textContent    = unread; badge.style.display    = 'flex'; }
      if (navBadge) { navBadge.textContent = unread; navBadge.style.display = 'inline-flex'; }
    }

    const list = document.getElementById('notif-list');
    if (!list) return;
    if (!items.length) {
      list.innerHTML = '<div style="padding:24px;text-align:center;color:#9ca3af;font-size:13px">No notifications yet</div>';
      return;
    }
    list.innerHTML = items.slice(0, 10).map(n => `
      <div class="notif-item ${!n.is_read?'unread':''}" onclick="markRead('${n.id}', this)">
        ${!n.is_read ? '<div class="notif-dot"></div>' : '<div style="width:8px"></div>'}
        <div class="notif-content">
          <div class="notif-title">${n.subject}</div>
          <div class="notif-desc">${(n.body||'').substring(0,80)}…</div>
          <div class="notif-time">${timeAgo(n.created_at)}</div>
        </div>
      </div>
    `).join('');
  } catch(e) {}
}

async function markRead(id, el) {
  el.classList.remove('unread');
  el.querySelector('.notif-dot')?.remove();
  await apiFetch(`/notifications/${id}/read/`, { method: 'POST' });
}

async function markAllRead() {
  document.querySelectorAll('.notif-item.unread').forEach(el => el.classList.remove('unread'));
  const badge    = document.getElementById('notif-count');
  const navBadge = document.getElementById('notif-nav-badge');
  if (badge)    badge.style.display = 'none';
  if (navBadge) navBadge.style.display = 'none';
}

async function loadPendingCount() {
  try {
    const res   = await apiFetch('/claims/?status=SUBMITTED');
    if (!res) return;
    const data  = await res.json();
    const count = data.count || (data.results || data).length;
    const badge = document.getElementById('pending-badge');
    if (count > 0 && badge) { badge.textContent = count; badge.style.display = 'inline-flex'; }
  } catch(e) {}
}

// ── HELPERS ────────────────────────────────────────────────────
function timeAgo(dateStr) {
  const diff = Math.floor((Date.now() - new Date(dateStr)) / 1000);
  if (diff < 60)    return 'Just now';
  if (diff < 3600)  return `${Math.floor(diff/60)}m ago`;
  if (diff < 86400) return `${Math.floor(diff/3600)}h ago`;
  return `${Math.floor(diff/86400)}d ago`;
}

function formatDate(d) {
  if (!d) return '—';
  return new Date(d).toLocaleDateString('en-ZW', { day:'numeric', month:'short', year:'numeric' });
}

function formatCurrency(amount) {
  if (!amount) return '—';
  return `USD ${parseFloat(amount).toLocaleString('en-US', { minimumFractionDigits: 2 })}`;
}

function statusBadge(status) {
  const map = {
    DRAFT:'badge-draft', SUBMITTED:'badge-submitted', UNDER_REVIEW:'badge-review',
    APPROVED:'badge-approved', DECLINED:'badge-declined', SETTLED:'badge-settled',
  };
  const labels = {
    DRAFT:'Draft', SUBMITTED:'Submitted', UNDER_REVIEW:'Under Review',
    APPROVED:'Approved', DECLINED:'Declined', SETTLED:'Settled'
  };
  return `<span class="badge ${map[status]||'badge-draft'}">${labels[status]||status}</span>`;
}

function showToast(msg, type = 'success') {
  const toast = document.createElement('div');
  toast.style.cssText = `
    position:fixed; bottom:24px; left:50%; transform:translateX(-50%);
    background:${type==='success'?'var(--green-dark)':'#dc2626'};
    color:white; padding:12px 24px; border-radius:10px;
    font-size:14px; font-weight:500; z-index:9999;
    box-shadow:0 4px 20px rgba(0,0,0,0.2); animation:slideUp 0.3s ease;
  `;
  toast.textContent = msg;
  document.body.appendChild(toast);
  setTimeout(() => toast.remove(), 3500);
}
