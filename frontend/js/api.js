/**
 * 前端 API 调用模块
 * 统一处理 HTTP 请求、Token 管理、错误提示
 */

const API_BASE = '/api';

// ==================== Token 管理 ====================

function getToken() {
    return localStorage.getItem('access_token');
}

function setToken(token) {
    localStorage.setItem('access_token', token);
}

function clearToken() {
    localStorage.removeItem('access_token');
    localStorage.removeItem('user_info');
}

function getUserInfo() {
    const info = localStorage.getItem('user_info');
    return info ? JSON.parse(info) : null;
}

function setUserInfo(info) {
    localStorage.setItem('user_info', JSON.stringify(info));
}

function isLoggedIn() {
    return !!getToken();
}

function isAdmin() {
    const info = getUserInfo();
    return info && info.role === 'admin';
}

function isOperator() {
    const info = getUserInfo();
    return info && info.role === 'operator';
}

function isAdminOrOperator() {
    const info = getUserInfo();
    return info && (info.role === 'admin' || info.role === 'operator');
}

// ==================== 通用请求 ====================

async function request(method, path, data = null, isFormData = false) {
    const token = getToken();
    const headers = {};

    if (token) {
        headers['Authorization'] = `Bearer ${token}`;
    }

    if (!isFormData && data) {
        headers['Content-Type'] = 'application/json';
    }

    const config = {
        method: method.toUpperCase(),
        headers,
    };

    if (data) {
        config.body = isFormData ? data : JSON.stringify(data);
    }

    try {
        const response = await fetch(API_BASE + path, config);

        if (response.status === 401) {
            clearToken();
            window.location.href = '/index.html';
            return null;
        }

        let result;
        const contentType = response.headers.get('content-type') || '';
        if (contentType.includes('application/json')) {
            result = await response.json();
        } else {
            result = await response.blob();
        }

        if (!response.ok) {
            let errMsg;
            if (result && result.detail) {
                if (typeof result.detail === 'string') {
                    errMsg = result.detail;
                } else if (Array.isArray(result.detail)) {
                    errMsg = result.detail.map(e => e.msg || JSON.stringify(e)).join(', ');
                } else {
                    errMsg = JSON.stringify(result.detail);
                }
            } else {
                errMsg = `请求失败 (${response.status})`;
            }
            throw new Error(errMsg);
        }

        return result;
    } catch (err) {
        if (err.message && !err.message.includes('Failed to fetch')) {
            throw err;
        }
        throw new Error('网络连接失败，请检查后端服务是否运行');
    }
}

const api = {
    get: (path) => request('GET', path),
    post: (path, data) => request('POST', path, data),
    put: (path, data) => request('PUT', path, data),
    delete: (path, data) => request('DELETE', path, data),
    postForm: (path, formData) => request('POST', path, formData, true),
};

// ==================== 认证 API ====================

async function login(username, password) {
    const formData = new FormData();
    formData.append('username', username);
    formData.append('password', password);

    const result = await api.postForm('/auth/login', formData);
    if (result) {
        setToken(result.access_token);
        setUserInfo({
            id: result.user_id,
            username: result.username,
            role: result.role,
            team_id: result.team_id,
        });
    }
    return result;
}

async function logout() {
    clearToken();
    window.location.href = '/index.html';
}

async function getMe() {
    return await api.get('/auth/me');
}

// ==================== 队伍 API ====================

async function getTeams() {
    return await api.get('/teams');
}

async function createTeam(name, contactPerson) {
    return await api.post('/teams', { name, contact_person: contactPerson });
}

async function deleteTeam(teamId) {
    return await api.delete(`/teams/${teamId}`);
}

// ==================== 工人 API ====================

async function getWorkers(params = {}) {
    const qs = new URLSearchParams(params).toString();
    return await api.get(`/workers${qs ? '?' + qs : ''}`);
}

async function getWorker(workerId) {
    return await api.get(`/workers/${workerId}`);
}


// 兼容旧名 apiRequest（workers.html 中直接调用过）
async function apiRequest(path, options = {}) {
    const token = getToken();
    const headers = { ...(options.headers || {}) };
    if (token) headers['Authorization'] = `Bearer ${token}`;
    if (!headers['Content-Type'] && options.body && typeof options.body === 'string') {
        headers['Content-Type'] = 'application/json';
    }
    const resp = await fetch(path, { ...options, headers });
    if (resp.status === 401) { clearToken(); window.location.href = '/index.html'; return null; }
    const ct = resp.headers.get('content-type') || '';
    const result = ct.includes('application/json') ? await resp.json() : await resp.blob();
    if (!resp.ok) throw new Error((result && result.detail) ? result.detail : `请求失败 (${resp.status})`);
    return result;
}

// ==================== 提交 API ====================

async function getSubmissions(params = {}) {
    const qs = new URLSearchParams(params).toString();
    return await api.get(`/submissions${qs ? '?' + qs : ''}`);
}

async function createSubmission(teamId, year, month, isHistorical = false) {
    const formData = new FormData();
    formData.append('team_id', teamId);
    formData.append('year', year);
    formData.append('month', month);
    formData.append('is_historical', isHistorical);
    return await api.postForm('/submissions/create', formData);
}

async function uploadFile(submissionId, fileType, file) {
    const formData = new FormData();
    formData.append('file_type', fileType);
    formData.append('file', file);
    return await api.postForm(`/submissions/${submissionId}/upload`, formData);
}

async function runCheck(submissionId) {
    return await api.post(`/submissions/${submissionId}/check`);
}

// ==================== 报告 API ====================

async function getReports(params = {}) {
    const qs = new URLSearchParams(params).toString();
    return await api.get(`/reports${qs ? '?' + qs : ''}`);
}

async function getReport(reportId) {
    return await api.get(`/reports/${reportId}`);
}

async function deleteReport(reportId) {
    return await api.delete(`/reports/${reportId}`);
}

async function exportReport(reportId) {
    const token = getToken();
    const response = await fetch(`${API_BASE}/reports/${reportId}/export`, {
        headers: { 'Authorization': `Bearer ${token}` }
    });
    if (!response.ok) {
        const text = await response.text();
        throw new Error('导出失败: ' + text);
    }
    const blob = await response.blob();
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `核对报告_${reportId}.xlsx`;
    a.click();
    URL.revokeObjectURL(url);
}

// ==================== 银行联号库 API ====================

async function getBankRoutingStats() {
    return await api.get('/bank-routing/stats');
}

async function searchBankRouting(q, limit = 20) {
    return await api.get(`/bank-routing/search?q=${encodeURIComponent(q)}&limit=${limit}`);
}

async function importBankRouting(file) {
    const formData = new FormData();
    formData.append('file', file);
    return await api.postForm('/bank-routing/import', formData);
}

// ==================== 历史数据导入 API ====================

async function analyzeHistorical(teamId, year, month, filesInfo, receiptFile) {
    const formData = new FormData();
    formData.append('team_id', teamId);
    formData.append('year', year);
    formData.append('month', month);

    filesInfo.forEach((item, idx) => {
        formData.append(`file_type_${idx}`, item.type);
        formData.append(`file_${idx}`, item.file);
    });

    formData.append('receipt_file', receiptFile);
    return await api.postForm('/historical/analyze', formData);
}

async function confirmHistorical(submissionId, approvedIdCards) {
    return await api.post('/historical/confirm', {
        submission_id: submissionId,
        approved_id_cards: approvedIdCards,
    });
}

// ==================== 公告 API ====================

async function getAnnouncements() {
    return await api.get('/announcements');
}

async function createAnnouncement(content, type = 'normal') {
    return await api.post('/announcements', { content, type });
}

async function deleteAnnouncement(id) {
    return await api.delete(`/announcements/${id}`);
}

// ==================== 公告铃铛系统 ====================

(function injectAnnStyles() {
    if (document.getElementById('ann-styles')) return;
    const s = document.createElement('style');
    s.id = 'ann-styles';
    s.textContent = `
        #ann-bell-wrap { position: relative; }
        #ann-bell-btn {
            background: rgba(255,255,255,0.15);
            border: 1px solid rgba(255,255,255,0.3);
            color: white; padding: 6px 10px;
            border-radius: 6px; cursor: pointer;
            font-size: 16px; position: relative;
            transition: background 0.2s;
        }
        #ann-bell-btn:hover { background: rgba(255,255,255,0.25); }
        #ann-badge {
            position: absolute; top: -5px; right: -5px;
            background: #e74c3c; color: white;
            border-radius: 10px; font-size: 11px;
            min-width: 18px; height: 18px;
            display: flex; align-items: center;
            justify-content: center; padding: 0 4px;
            font-weight: bold; pointer-events: none;
        }
        #ann-panel {
            position: absolute; top: calc(100% + 8px); right: 0;
            width: 360px; background: white;
            border-radius: 12px;
            box-shadow: 0 8px 24px rgba(0,0,0,0.18);
            z-index: 2000; overflow: hidden;
        }
        .ann-panel-header {
            padding: 14px 16px; font-weight: 600;
            font-size: 14px; color: #333;
            border-bottom: 1px solid #f0f0f0;
            display: flex; align-items: center;
            justify-content: space-between;
        }
        .ann-panel-close {
            background: none; border: none;
            cursor: pointer; color: #aaa; font-size: 16px;
            line-height: 1; padding: 2px 6px; border-radius: 4px;
        }
        .ann-panel-close:hover { background: #f5f5f5; color: #666; }
        .ann-list { max-height: 320px; overflow-y: auto; }
        .ann-list-item {
            padding: 12px 16px;
            border-bottom: 1px solid #f5f5f5;
        }
        .ann-list-item.unread { background: #fffbe6; }
        .ann-item-top { display: flex; justify-content: space-between; align-items: flex-start; }
        .ann-item-content { font-size: 13px; color: #333; line-height: 1.5; flex: 1; white-space: pre-wrap; }
        .ann-item-meta { font-size: 11px; color: #aaa; margin-top: 4px; display: flex; gap: 6px; align-items: center; }
        .ann-type-tag {
            background: #fff3e0; color: #e67e22;
            padding: 1px 6px; border-radius: 4px; font-size: 10px;
        }
        .ann-list-empty { padding: 28px; text-align: center; color: #aaa; font-size: 13px; }
        .ann-del-btn {
            background: none; border: none; color: #ddd;
            cursor: pointer; font-size: 12px;
            padding: 2px 5px; border-radius: 3px;
            flex-shrink: 0; margin-left: 8px;
        }
        .ann-del-btn:hover { color: #e74c3c; background: #fce8e8; }
        .ann-compose-area {
            padding: 12px 16px;
            border-top: 1px solid #f0f0f0;
            background: #fafafa;
        }
        .ann-type-select {
            width: 100%; margin-bottom: 8px;
            padding: 6px 10px; border: 1px solid #ddd;
            border-radius: 6px; font-size: 13px; background: white;
        }
        .ann-textarea {
            width: 100%; border: 1px solid #ddd;
            border-radius: 6px; padding: 8px 10px;
            font-size: 13px; resize: none; height: 56px;
            font-family: inherit; box-sizing: border-box;
        }
        .ann-textarea:focus { outline: none; border-color: #2d6a9f; }
        .ann-post-btn {
            margin-top: 8px; width: 100%;
            background: #2d6a9f; color: white;
            border: none; border-radius: 6px;
            padding: 8px; cursor: pointer; font-size: 13px;
        }
        .ann-post-btn:hover { background: #1a3a5c; }
        #ann-fs-overlay {
            position: fixed; inset: 0;
            background: rgba(0,0,0,0.65);
            z-index: 9999;
            display: flex; align-items: center; justify-content: center;
        }
        #ann-fs-modal {
            background: white; border-radius: 16px;
            padding: 40px 36px; max-width: 540px;
            width: 92%; box-shadow: 0 20px 60px rgba(0,0,0,0.3);
            text-align: center;
        }
        .ann-fs-icon { font-size: 44px; margin-bottom: 12px; }
        .ann-fs-title { font-size: 20px; font-weight: 700; color: #1a3a5c; margin-bottom: 20px; }
        .ann-fs-counter { font-size: 12px; color: #aaa; margin-bottom: 12px; }
        .ann-fs-body {
            font-size: 15px; color: #444; line-height: 1.7;
            text-align: left; background: #f8f9fa;
            border-radius: 8px; padding: 16px 20px;
            margin-bottom: 16px; white-space: pre-wrap;
        }
        .ann-fs-meta { font-size: 12px; color: #aaa; margin-bottom: 24px; }
        .ann-fs-read-btn {
            background: #2d6a9f; color: white; border: none;
            border-radius: 8px; padding: 12px 36px;
            font-size: 15px; cursor: pointer; font-weight: 500;
        }
        .ann-fs-read-btn:hover { background: #1a3a5c; }
    `;
    document.head.appendChild(s);
})();

let _annList = [];
let _annPanelOpen = false;
let _fsQueue = [];

function _getReadIds() {
    try { return new Set(JSON.parse(localStorage.getItem('read_ann_ids') || '[]')); }
    catch(e) { return new Set(); }
}
function _markRead(id) {
    const ids = _getReadIds();
    ids.add(id);
    localStorage.setItem('read_ann_ids', JSON.stringify([...ids]));
}

async function initAnnouncements() {
    const navbarRight = document.querySelector('.navbar-right');
    if (!navbarRight || document.getElementById('ann-bell-wrap')) return;

    const wrap = document.createElement('div');
    wrap.id = 'ann-bell-wrap';
    wrap.innerHTML = `
        <button id="ann-bell-btn" title="公告通知" onclick="_toggleAnnPanel(event)">
            🔔
            <span id="ann-badge" style="display:none"></span>
        </button>
        <div id="ann-panel" style="display:none">
            <div class="ann-panel-header">
                <span>📢 系统公告</span>
                <button class="ann-panel-close" onclick="_toggleAnnPanel(event)">✕</button>
            </div>
            <div class="ann-list" id="ann-list-body"></div>
            ${isAdmin() ? `
            <div class="ann-compose-area">
                <select id="ann-type-select" class="ann-type-select">
                    <option value="normal">普通公告</option>
                    <option value="fullscreen">全屏公告（登录后弹出）</option>
                </select>
                <textarea id="ann-input" class="ann-textarea" placeholder="输入公告内容..."></textarea>
                <button class="ann-post-btn" onclick="_postAnn()">发布</button>
            </div>` : ''}
        </div>
    `;
    const logoutBtn = navbarRight.querySelector('.btn-logout');
    navbarRight.insertBefore(wrap, logoutBtn);

    document.addEventListener('click', function(e) {
        const panel = document.getElementById('ann-panel');
        const w = document.getElementById('ann-bell-wrap');
        if (panel && w && !w.contains(e.target) && panel.style.display !== 'none') {
            panel.style.display = 'none';
            _annPanelOpen = false;
        }
    });

    await _refreshAnn();
}

function _toggleAnnPanel(e) {
    if (e) e.stopPropagation();
    const panel = document.getElementById('ann-panel');
    if (!panel) return;
    _annPanelOpen = !_annPanelOpen;
    panel.style.display = _annPanelOpen ? 'block' : 'none';
    if (_annPanelOpen) {
        _annList.forEach(a => _markRead(a.id));
        _updateBadge();
        _renderAnnList();
    }
}

function _updateBadge() {
    const readIds = _getReadIds();
    const unread = _annList.filter(a => !readIds.has(a.id)).length;
    const badge = document.getElementById('ann-badge');
    if (!badge) return;
    if (unread > 0) {
        badge.textContent = unread > 99 ? '99+' : unread;
        badge.style.display = 'flex';
    } else {
        badge.style.display = 'none';
    }
}

function _renderAnnList() {
    const body = document.getElementById('ann-list-body');
    if (!body) return;
    const readIds = _getReadIds();
    if (_annList.length === 0) {
        body.innerHTML = '<div class="ann-list-empty">暂无公告</div>';
        return;
    }
    body.innerHTML = _annList.map(a => {
        const unread = !readIds.has(a.id);
        const tag = a.type === 'fullscreen' ? '<span class="ann-type-tag">全屏</span>' : '';
        const del = isAdmin() ? `<button class="ann-del-btn" onclick="_deleteAnn(${a.id})" title="删除">✕</button>` : '';
        return `<div class="ann-list-item ${unread ? 'unread' : ''}" id="ann-item-${a.id}">
            <div class="ann-item-top">
                <div class="ann-item-content">${escapeHtml(a.content)}</div>
                ${del}
            </div>
            <div class="ann-item-meta">${tag}${a.author_name} · ${formatDate(a.created_at)}</div>
        </div>`;
    }).join('');
}

async function _refreshAnn() {
    try {
        _annList = await getAnnouncements() || [];
        _updateBadge();
        if (_annPanelOpen) _renderAnnList();
        const readIds = _getReadIds();
        _fsQueue = _annList.filter(a => a.type === 'fullscreen' && !readIds.has(a.id));
        if (_fsQueue.length > 0) _showNextFs();
    } catch(e) {}
}

function _showNextFs() {
    if (_fsQueue.length === 0) return;
    const ann = _fsQueue[0];
    let ov = document.getElementById('ann-fs-overlay');
    if (!ov) { ov = document.createElement('div'); ov.id = 'ann-fs-overlay'; document.body.appendChild(ov); }
    ov.innerHTML = `<div id="ann-fs-modal">
        <div class="ann-fs-icon">📢</div>
        <div class="ann-fs-title">系统公告</div>
        ${_fsQueue.length > 1 ? `<div class="ann-fs-counter">还有 ${_fsQueue.length} 条未读公告</div>` : ''}
        <div class="ann-fs-body">${escapeHtml(ann.content)}</div>
        <div class="ann-fs-meta">${ann.author_name} · ${formatDate(ann.created_at)}</div>
        <button class="ann-fs-read-btn" onclick="_confirmReadFs(${ann.id})">我已阅读</button>
    </div>`;
    ov.style.display = 'flex';
}

function _confirmReadFs(id) {
    _markRead(id);
    _fsQueue = _fsQueue.filter(a => a.id !== id);
    _updateBadge();
    if (_fsQueue.length > 0) { _showNextFs(); return; }
    const ov = document.getElementById('ann-fs-overlay');
    if (ov) ov.style.display = 'none';
}

async function _postAnn() {
    const input = document.getElementById('ann-input');
    const typeEl = document.getElementById('ann-type-select');
    const content = input?.value?.trim();
    if (!content) { showToast('请输入公告内容', 'warning'); return; }
    try {
        await createAnnouncement(content, typeEl?.value || 'normal');
        input.value = '';
        await _refreshAnn();
        _renderAnnList();
        showToast('公告已发布', 'success');
    } catch(e) { showToast(e.message, 'error'); }
}

async function _deleteAnn(id) {
    if (!confirm('确认删除这条公告？')) return;
    try {
        await deleteAnnouncement(id);
        _annList = _annList.filter(a => a.id !== id);
        _updateBadge();
        _renderAnnList();
        showToast('已删除', 'success');
    } catch(e) { showToast(e.message, 'error'); }
}

// ==================== 工具函数 ====================

function showToast(message, type = 'info') {
    // 移除旧的 toast
    const old = document.getElementById('toast-msg');
    if (old) old.remove();

    const colors = {
        success: '#28a745',
        error: '#dc3545',
        warning: '#ffc107',
        info: '#17a2b8',
    };

    const toast = document.createElement('div');
    toast.id = 'toast-msg';
    toast.style.cssText = `
        position: fixed; top: 20px; right: 20px; z-index: 9999;
        padding: 12px 20px; border-radius: 8px; color: white;
        background: ${colors[type] || colors.info};
        box-shadow: 0 4px 12px rgba(0,0,0,0.2);
        font-size: 14px; max-width: 400px; word-break: break-all;
        animation: slideIn 0.3s ease;
    `;
    toast.textContent = message;
    document.body.appendChild(toast);

    setTimeout(() => {
        toast.style.opacity = '0';
        toast.style.transition = 'opacity 0.3s';
        setTimeout(() => toast.remove(), 300);
    }, 4000);
}

function escapeHtml(str) {
    if (!str) return '';
    return str.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}

function formatDate(dateStr) {
    if (!dateStr) return '-';
    const d = new Date(dateStr);
    return d.toLocaleString('zh-CN', {
        year: 'numeric', month: '2-digit', day: '2-digit',
        hour: '2-digit', minute: '2-digit'
    });
}

function maskIdCard(idCard) {
    if (!idCard || idCard.length < 8) return idCard;
    return idCard.slice(0, 4) + '**********' + idCard.slice(-2);
}

function checkLoginStatus() {
    if (!isLoggedIn()) {
        window.location.href = '/index.html';
        return false;
    }
    return true;
}

function renderUserInfo() {
    const info = getUserInfo();
    if (!info) return;
    const el = document.getElementById('user-display');
    if (el) {
        const roleLabel = info.role === 'admin' ? '管理员' : info.role === 'operator' ? '操作员' : '队伍负责人';
        el.textContent = `${info.username} (${roleLabel})`;
    }
    // team_leader 隐藏 admin-only 元素
    if (!isAdminOrOperator()) {
        document.querySelectorAll('.admin-only').forEach(el => {
            el.style.display = 'none';
        });
    }
    // operator 隐藏 superadmin-only 元素（仅 admin 可见）
    if (!isAdmin()) {
        document.querySelectorAll('.superadmin-only').forEach(el => {
            el.style.display = 'none';
        });
    }
}
