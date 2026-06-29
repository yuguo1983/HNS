/* Denny Agent WebUI - Session-aware SSE client */

(function () {
  let sessionId = null;
  let es = null;
  let done = false;
  let pendingToolStart = null;

  function updateSidDisplay(sid) {
    document.getElementById('current-sid').textContent = sid ? '#' + sid : '';
  }
  const textarea = document.getElementById('msg-input');
  const sendBtn = document.getElementById('send-btn');
  const messagesEl = document.getElementById('messages');
  const sessionListEl = document.getElementById('session-list');
  const emptyStateEl = document.getElementById('empty-state');

  // --- Session management ---
  async function loadSessions() {
    try {
      const res = await fetch('/api/sessions');
      const data = await res.json();
      renderSessionList(data.sessions || []);
    } catch (e) {
      console.error('loadSessions failed', e);
    }
  }

  function renderSessionList(sessions) {
    sessionListEl.innerHTML = '';
    sessions.forEach(s => {
      const div = document.createElement('div');
      div.className = 'session-item' + (s.session_id === sessionId ? ' active' : '');
      div.dataset.sid = s.session_id;
      div.innerHTML =
        '<span class="sid">#' + esc(s.session_id) + '</span>' +
        '<span class="preview">' + esc(s.name || '(空)') + '</span>';
      div.addEventListener('click', () => selectSession(s.session_id));
      sessionListEl.appendChild(div);
    });
  }

  async function selectSession(sid) {
    if (sid === sessionId) return;
    closeES();
    sessionId = sid;
    localStorage.setItem('denny_session', sessionId);
    updateSidDisplay(sessionId);
    await loadSessions();
    await loadHistory(sid);
  }

  async function loadHistory(sid) {
    try {
      const res = await fetch('/api/sessions/' + sid + '/history');
      const data = await res.json();
      messagesEl.innerHTML = '';
      (data.messages || []).forEach(m => {
        if (typeof m.content === 'string' && m.content) {
          appendMessage(m.role, m.content);
        }
      });
      emptyStateEl.style.display = data.messages && data.messages.length ? 'none' : 'flex';
      document.getElementById('status').querySelector('.phase').textContent = '就绪';
    } catch (e) {
      console.error('loadHistory failed', e);
    }
  }

  async function newSession() {
    closeES();
    try {
      const res = await fetch('/api/sessions', { method: 'POST' });
      const data = await res.json();
      sessionId = data.session_id;
      localStorage.setItem('denny_session', sessionId);
      updateSidDisplay(sessionId);
      messagesEl.innerHTML = '';
      emptyStateEl.style.display = 'flex';
      document.getElementById('status').querySelector('.phase').textContent = '就绪';
      await loadSessions();
      textarea.focus();
    } catch (e) {
      console.error('newSession failed', e);
      alert('创建会话失败：请检查服务是否正常运行');
    }
  }

  async function deleteCurrentSession() {
    if (!sessionId) return;
    try { await fetch('/api/sessions/' + sessionId, { method: 'DELETE' }); } catch (_) {}
    closeES();
    const res = await fetch('/api/sessions');
    const data = await res.json();
    const remaining = data.sessions || [];
    if (remaining.length > 0) {
      await selectSession(remaining[0].session_id);
    } else {
      sessionId = null;
      localStorage.removeItem('denny_session');
      updateSidDisplay(null);
      messagesEl.innerHTML = '';
      emptyStateEl.style.display = 'flex';
      await loadSessions();
    }
  }

  async function clearCurrentSession() {
    if (!sessionId) return;
    try { await fetch('/api/sessions/' + sessionId + '/clear', { method: 'POST' }); } catch (_) {}
    messagesEl.innerHTML = '';
    emptyStateEl.style.display = 'flex';
    document.getElementById('status').querySelector('.phase').textContent = '就绪';
  }

  // --- Init ---
  const savedSid = localStorage.getItem('denny_session');
  loadSessions().then(async () => {
    if (savedSid) {
      const res = await fetch('/api/sessions');
      const data = await res.json();
      if (data.sessions.some(s => s.session_id === savedSid)) {
        sessionId = savedSid;
        updateSidDisplay(sessionId);
        await loadHistory(savedSid);
        await loadSessions();
      } else {
        await newSession();
      }
    } else {
      await newSession();
    }
  }).catch(e => console.error('init failed', e));

  document.getElementById('new-session-btn').addEventListener('click', newSession);
  document.getElementById('clear-btn').addEventListener('click', clearCurrentSession);
  document.getElementById('delete-btn').addEventListener('click', deleteCurrentSession);
  const copyBtn = document.getElementById('copy-sid-btn');
  if (copyBtn) {
    copyBtn.addEventListener('click', function () {
      if (!sessionId) return;
      navigator.clipboard && navigator.clipboard.writeText(sessionId).then(() => {
        const old = copyBtn.textContent;
        copyBtn.textContent = '已复制';
        setTimeout(() => { copyBtn.textContent = old; }, 1200);
      });
    });
  }

  // --- Send ---
  function sendMessage(text) {
    text = (text || '').trim();
    if (!text) return;
    if (!sessionId) {
      alert('请先创建或选择一个会话');
      return;
    }

    setStatus('thinking', '');
    appendMessage('user', text);
    textarea.disabled = true;
    sendBtn.disabled = true;
    emptyStateEl.style.display = 'none';

    const url = '/api/sse/chat?message=' + encodeURIComponent(text) + '&session_id=' + encodeURIComponent(sessionId);
    if (es) { try { es.close(); } catch (_) {} }
    try {
      es = new EventSource(url);
    } catch (e) {
      appendMessage('error', '[错误] 无法创建连接: ' + e.message);
      setStatus('error', e.message);
      closeES();
      return;
    }
    done = false;

    es.addEventListener('status', function (e) {
      try {
        const d = JSON.parse(e.data);
        setStatus(d.phase, d.message);
      } catch (_) {}
    });

    es.addEventListener('tool_start', function (e) {
      try {
        pendingToolStart = JSON.parse(e.data);
      } catch (_) {}
    });

    es.addEventListener('tool_result', function (e) {
      try {
        const d = JSON.parse(e.data);
        if (pendingToolStart && pendingToolStart.tool === d.tool) {
          appendToolBlock(pendingToolStart.tool, pendingToolStart.input, d.result);
          pendingToolStart = null;
        }
      } catch (_) {}
    });

    es.addEventListener('session_id', function (e) {
      try {
        const d = JSON.parse(e.data);
        if (d.session_id && d.session_id !== sessionId) {
          sessionId = d.session_id;
          localStorage.setItem('denny_session', sessionId);
          loadSessions();
        }
      } catch (_) {}
    });

    // 流式文本：逐块拼接到临时气泡
    let streamingBubble = null;
    es.addEventListener('delta', function (e) {
      try {
        const d = JSON.parse(e.data);
        if (!d.content) return;
        if (!streamingBubble) {
          streamingBubble = document.createElement('div');
          streamingBubble.className = 'msg msg-assistant streaming';
          streamingBubble.textContent = '';
          messagesEl.appendChild(streamingBubble);
        }
        streamingBubble.textContent += d.content;
        scroll();
      } catch (_) {}
    });

    es.addEventListener('message', function (e) {
      try {
        const d = JSON.parse(e.data);
        if (!d.content) return;
        // 若已有流式气泡，说明 delta 已展示完整内容，这里只做兜底（不再重复追加）
        if (streamingBubble) {
          // 流式过程中可能因工具调用产生多段，message 作为该回合定稿：保留气泡已累积内容
          return;
        }
        appendMessage('assistant', d.content);
        loadSessions();
      } catch (_) {}
    });

    es.addEventListener('done', function () {
      // 流式气泡定稿
      if (streamingBubble) {
        streamingBubble.classList.remove('streaming');
        streamingBubble = null;
      }
      done = true;
      closeES();
      loadSessions();
    });

    es.addEventListener('error', function (e) {
      if (done) return;
      let msg = '连接失败，请检查服务是否在运行';
      try { if (e.data) { const d = JSON.parse(e.data); if (d.error) msg = d.error; } } catch (_) {}
      appendMessage('error', '[错误] ' + msg);
      setStatus('error', msg);
      closeES();
    });
  }

  function closeES() {
    if (es) { try { es.close(); } catch (_) {} es = null; }
    textarea.disabled = false;
    sendBtn.disabled = false;
  }

  // --- DOM helpers ---
  function setStatus(phase, message) {
    const phaseEl = document.getElementById('status').querySelector('.phase');
    const map = {
      thinking: { label: '思考中...', cls: 'thinking' },
      tools:    { label: '调用工具: ' + (message || ''), cls: 'tools' },
      done:     { label: '就绪', cls: 'done' },
      error:    { label: '错误: ' + (message || ''), cls: 'error' },
    };
    const info = map[phase] || { label: message || '', cls: '' };
    phaseEl.textContent = info.label;
    phaseEl.className = 'phase ' + info.cls;
  }

  function appendMessage(role, content) {
    const div = document.createElement('div');
    div.className = 'msg msg-' + role;
    div.textContent = content;
    messagesEl.appendChild(div);
    scroll();
  }

  function appendToolBlock(tool, input, result) {
    let inputStr = '';
    try { inputStr = JSON.stringify(input, null, 2); } catch (_) { inputStr = String(input); }
    const truncated = result.length >= 1000;
    const block = document.createElement('div');
    block.className = 'tool-block';
    block.innerHTML =
      '<div class="tool-header">' +
        '<span class="tool-badge">工具</span>' +
        '<span class="tool-name">' + esc(tool) + '</span>' +
      '</div>' +
      '<div class="tool-input">' + esc(inputStr) + '</div>' +
      '<div class="tool-result' + (truncated ? ' truncated' : '') + '">' +
        (truncated ? esc(result.slice(0, 1000)) + '\n...(已截断)' : esc(result)) +
      '</div>';
    messagesEl.appendChild(block);
    scroll();
  }

  function scroll() {
    messagesEl.scrollTop = messagesEl.scrollHeight;
  }

  function esc(s) {
    return String(s)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;');
  }

  // --- Input handling ---
  textarea.addEventListener('keydown', function (e) {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      sendMessage(textarea.value);
      textarea.value = '';
    }
  });

  sendBtn.addEventListener('click', function () {
    sendMessage(textarea.value);
    textarea.value = '';
  });
})();
