/* JARVIS SSE client (POST + ReadableStream).
 *
 * The browser EventSource API only supports GET. /api/chat/stream is POST,
 * so we implement the SSE wire-format by hand on top of fetch + ReadableStream.
 *
 * Events emitted by the backend (see gateway/app.py + agent.chat_stream):
 *   start             {session_id}
 *   tool_call         {tool, input, status}
 *   tool_result       {tool, output}
 *   approval_required {request_id, action, risk_level, payload}
 *   final             {message, tool_calls, needs_approval, ...}
 *
 * Usage:
 *   const stop = JarvisSSE.chatStream({
 *     message, sessionId, agentId, context,
 *     onEvent: (event, data) => { ... },
 *     onError: (err) => { ... },
 *     onDone:  () => { ... },
 *   });
 *   // Call stop() to abort early.
 */
(function () {
  function parseSSEBlock(block) {
    // A block is one event separated by blank line. Lines:
    //   event: <name>
    //   data: <text>
    let event = 'message';
    const dataLines = [];
    block.split('\n').forEach((line) => {
      if (!line) return;
      if (line.startsWith(':')) return; // comment
      const idx = line.indexOf(':');
      const field = idx >= 0 ? line.slice(0, idx) : line;
      let value = idx >= 0 ? line.slice(idx + 1) : '';
      if (value.startsWith(' ')) value = value.slice(1);
      if (field === 'event') event = value;
      else if (field === 'data') dataLines.push(value);
    });
    let data = dataLines.join('\n');
    let parsed = data;
    if (data) {
      try { parsed = JSON.parse(data); } catch (_) { /* keep raw */ }
    }
    return { event, data: parsed };
  }

  function chatStream(opts) {
    const {
      message,
      sessionId,
      agentId,
      context,
      onEvent,
      onError,
      onDone,
      userId,
      userRole,
    } = opts;

    const ctrl = new AbortController();
    const headers = { 'Content-Type': 'application/json', 'Accept': 'text/event-stream' };
    const token = (window.JarvisAPI && window.JarvisAPI.getToken && window.JarvisAPI.getToken()) || '';
    if (token) headers.Authorization = `Bearer ${token}`;

    const body = JSON.stringify({
      message: message || '',
      session_id: sessionId || '',
      user_id: userId || '',
      user_role: userRole || 'viewer',
      context: Object.assign({ agent_id: agentId || 'it-ops' }, context || {}),
    });

    (async () => {
      try {
        const resp = await fetch('/api/chat/stream', { method: 'POST', headers, body, signal: ctrl.signal });
        if (!resp.ok || !resp.body) {
          const text = await resp.text().catch(() => '');
          throw Object.assign(new Error(`SSE failed: ${resp.status} ${text || resp.statusText}`), { status: resp.status });
        }
        const reader = resp.body.getReader();
        const decoder = new TextDecoder('utf-8');
        let buf = '';
        while (true) {
          const { value, done } = await reader.read();
          if (done) break;
          buf += decoder.decode(value, { stream: true });
          let sep;
          while ((sep = buf.indexOf('\n\n')) !== -1) {
            const block = buf.slice(0, sep);
            buf = buf.slice(sep + 2);
            if (!block.trim()) continue;
            const { event, data } = parseSSEBlock(block);
            try { onEvent && onEvent(event, data); } catch (e) { console.error('onEvent threw', e); }
          }
        }
        onDone && onDone();
      } catch (err) {
        if (err.name === 'AbortError') { onDone && onDone(); return; }
        console.error('SSE error', err);
        onError && onError(err);
      }
    })();

    return function stop() { ctrl.abort(); };
  }

  window.JarvisSSE = { chatStream, parseSSEBlock };
})();
