/* JARVIS API client (zero-build, vanilla JS).
 *
 * Loaded as a plain <script> in JARVIS.html — exposes a single global
 * `window.JarvisAPI` object. Components use it via React hooks below.
 *
 * Conventions:
 *   - Token is stored in sessionStorage under 'jarvis.token'.
 *   - All API calls auto-attach `Authorization: Bearer <token>` if present.
 *   - Errors throw with .status and .body for consumer handling.
 */
(function () {
  const TOKEN_KEY = 'jarvis.token';
  const USER_KEY = 'jarvis.user';

  function getToken() {
    try { return sessionStorage.getItem(TOKEN_KEY) || ''; } catch (_) { return ''; }
  }
  function setToken(t) {
    try { t ? sessionStorage.setItem(TOKEN_KEY, t) : sessionStorage.removeItem(TOKEN_KEY); } catch (_) {}
  }
  function getUser() {
    try { const raw = sessionStorage.getItem(USER_KEY); return raw ? JSON.parse(raw) : null; } catch (_) { return null; }
  }
  function setUser(u) {
    try { u ? sessionStorage.setItem(USER_KEY, JSON.stringify(u)) : sessionStorage.removeItem(USER_KEY); } catch (_) {}
  }

  async function request(path, opts) {
    opts = opts || {};
    const headers = Object.assign({ 'Accept': 'application/json' }, opts.headers || {});
    const token = getToken();
    if (token && !headers.Authorization) headers.Authorization = `Bearer ${token}`;
    if (opts.body && !(opts.body instanceof FormData) && !headers['Content-Type']) {
      headers['Content-Type'] = 'application/json';
      if (typeof opts.body !== 'string') opts.body = JSON.stringify(opts.body);
    }
    const resp = await fetch(path, { method: opts.method || 'GET', headers, body: opts.body });
    const text = await resp.text();
    let body = null;
    try { body = text ? JSON.parse(text) : null; } catch (_) { body = text; }
    if (!resp.ok) {
      const err = new Error((body && body.detail) || resp.statusText || 'request failed');
      err.status = resp.status;
      err.body = body;
      throw err;
    }
    return body;
  }

  // ====== Auth ======
  async function login(username, password) {
    const data = await request('/api/auth/login', { method: 'POST', body: { username, password } });
    setToken(data.token);
    setUser(data.user);
    return data;
  }
  async function me() {
    return await request('/api/auth/me');
  }
  async function logout() {
    try { await request('/api/auth/logout', { method: 'POST' }); } catch (_) {}
    setToken('');
    setUser(null);
  }

  // ====== Agents catalog ======
  async function listAgents() {
    return (await request('/api/agents')).agents;
  }

  // ====== Conversations ======
  async function listConversations(limit) {
    return (await request(`/api/conversations?limit=${limit || 50}`)).conversations;
  }
  async function createConversation(payload) {
    return await request('/api/conversations', { method: 'POST', body: payload || {} });
  }
  async function patchConversation(sid, title) {
    return await request(`/api/conversations/${encodeURIComponent(sid)}`, { method: 'PATCH', body: { title } });
  }
  async function deleteConversation(sid) {
    return await request(`/api/conversations/${encodeURIComponent(sid)}`, { method: 'DELETE' });
  }
  async function getConversationMessages(sid, limit) {
    return await request(`/api/conversations/${encodeURIComponent(sid)}/messages?limit=${limit || 50}`);
  }

  // ====== Approval ======
  async function decideApproval(requestId, decision, comment) {
    return await request('/api/approval/decision', {
      method: 'POST',
      body: { request_id: requestId, decision, comment: comment || '' },
    });
  }

  // ====== Misc ======
  async function listTools() {
    return (await request('/api/tools')).tools;
  }

  window.JarvisAPI = {
    // token / user
    getToken, setToken, getUser, setUser,
    // auth
    login, me, logout,
    // catalog
    listAgents,
    // conversations
    listConversations, createConversation, patchConversation, deleteConversation, getConversationMessages,
    // approval
    decideApproval,
    // misc
    listTools,
    request,
  };
})();
