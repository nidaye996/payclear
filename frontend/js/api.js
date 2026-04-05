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

async function createAnnouncement(content) {
    return await api.post('/announcements', { content });
}

async function deleteAnnouncement(id) {
    return await api.delete(`/announcements/${id}`);
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
