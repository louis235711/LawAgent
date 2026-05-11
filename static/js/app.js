/* LawAgent Frontend - Chat UI Logic */

// ─── State ────────────────────────────────────────────
const state = {
  sessions: [],           // { id, title, updatedAt }
  activeId: null,
  messages: [],           // current session messages
  streaming: false,
  uploadedDoc: null,      // { name } or null
};

// ─── DOM refs ─────────────────────────────────────────
const $ = (sel) => document.querySelector(sel);
const $$ = (sel) => document.querySelectorAll(sel);

const dom = {
  sidebar: $('#sidebar'),
  overlay: $('#sidebarOverlay'),
  sessionList: $('#sessionList'),
  messages: $('#messagesContainer'),
  welcome: $('#welcomeScreen'),
  input: $('#messageInput'),
  sendBtn: $('#btnSend'),
  title: $('#chatTitle'),
  uploadBar: $('#uploadBar'),
  uploadName: $('#uploadFileName'),
  fileInput: $('#fileInput'),
  toast: $('#toast'),
};

// ─── Marked & Highlight config ────────────────────────
marked.setOptions({ breaks: true, gfm: true });
marked.use({
  renderer: {
    code(code, lang) {
      const valid = lang && hljs.getLanguage(lang);
      const highlighted = valid ? hljs.highlight(code, { language: lang }).value : hljs.highlightAuto(code).value;
      return `<pre><code class="hljs language-${lang || ''}">${highlighted}</code></pre>`;
    },
  },
});

// ─── Sessions (localStorage) ───────────────────────────
function loadSessions() {
  try {
    state.sessions = JSON.parse(localStorage.getItem('lawagent_sessions') || '[]');
  } catch { state.sessions = []; }
  state.sessions.sort((a, b) => b.updatedAt - a.updatedAt);
}

function saveSessions() {
  localStorage.setItem('lawagent_sessions', JSON.stringify(state.sessions));
}

function addSession(id, title) {
  const existing = state.sessions.find(s => s.id === id);
  if (existing) {
    existing.updatedAt = Date.now();
    existing.title = title || existing.title;
  } else {
    state.sessions.unshift({ id, title: title || '新对话', updatedAt: Date.now() });
  }
  // Keep max 50 sessions
  if (state.sessions.length > 50) state.sessions = state.sessions.slice(0, 50);
  saveSessions();
}

function updateSessionTitle(id, firstMsg) {
  const s = state.sessions.find(s => s.id === id);
  if (s && s.title === '新对话') {
    s.title = firstMsg.slice(0, 30) + (firstMsg.length > 30 ? '...' : '');
    saveSessions();
  }
}

function deleteSession(id) {
  state.sessions = state.sessions.filter(s => s.id !== id);
  saveSessions();
  if (state.activeId === id) {
    state.activeId = null;
    state.messages = [];
    state.uploadedDoc = null;
    renderSessions();
    showWelcome();
  }
}

// ─── Render sidebar ───────────────────────────────────
function renderSessions() {
  dom.sessionList.innerHTML = state.sessions.length === 0
    ? '<div style="padding:20px;text-align:center;color:#aaa;font-size:13px">暂无对话记录</div>'
    : state.sessions.map(s => `
      <div class="session-item${s.id === state.activeId ? ' active' : ''}" data-id="${s.id}">
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/></svg>
        <span class="session-title">${escapeHtml(s.title)}</span>
        <button class="btn-delete-session" data-del="${s.id}" title="删除">
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="3 6 5 6 21 6"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/></svg>
        </button>
      </div>
    `).join('');

  // Click handlers
  dom.sessionList.querySelectorAll('.session-item').forEach(el => {
    el.addEventListener('click', (e) => {
      if (e.target.closest('.btn-delete-session')) return;
      switchSession(el.dataset.id);
    });
  });
  dom.sessionList.querySelectorAll('.btn-delete-session').forEach(btn => {
    btn.addEventListener('click', (e) => {
      e.stopPropagation();
      if (confirm('确定删除此对话？')) deleteSession(btn.dataset.del);
    });
  });
}

// ─── Switch session ───────────────────────────────────
async function switchSession(id) {
  if (state.streaming) return;
  state.activeId = id;
  state.messages = [];
  state.uploadedDoc = null;

  // Load messages from API
  try {
    const r = await fetch(`/api/session/${id}/history`);
    if (r.ok) {
      const data = await r.json();
      state.messages = (data.messages || []).map(m => ({
        role: m.role === 'ai' ? 'ai' : 'user',
        content: m.content,
        message_type: m.message_type,
      }));
      if (data.has_document) {
        state.uploadedDoc = { name: data.document_name || '已上传文档' };
      }
    }
  } catch { /* ignore */ }

  renderSessions();
  renderMessages();
  updateUploadBar();
  dom.title.textContent = state.sessions.find(s => s.id === id)?.title || 'LawAgent';
}

// ─── Render messages ──────────────────────────────────
function renderMessages() {
  dom.welcome.style.display = state.messages.length === 0 ? 'flex' : 'none';

  // Remove existing message wrappers
  dom.messages.querySelectorAll('.msg-wrapper').forEach(el => el.remove());

  state.messages.forEach((m, i) => {
    const el = createMessageElement(m, i === state.messages.length - 1);
    dom.messages.appendChild(el);
  });

  scrollToBottom();
}

function createMessageElement(m, isLast) {
  const wrapper = document.createElement('div');
  wrapper.className = 'msg-wrapper';

  const row = document.createElement('div');
  row.className = `message-row ${m.role}`;

  const avatar = document.createElement('div');
  avatar.className = 'avatar-icon';
  avatar.textContent = m.role === 'ai' ? 'AI' : 'U';

  const bubble = document.createElement('div');
  bubble.className = 'message-bubble';
  bubble.innerHTML = m.role === 'ai' ? marked.parse(m.content) : `<p>${escapeHtml(m.content)}</p>`;

  // Copy button for AI messages
  if (m.role === 'ai') {
    const actions = document.createElement('div');
    actions.className = 'msg-actions';
    const copyBtn = document.createElement('button');
    copyBtn.className = 'btn-copy';
    copyBtn.textContent = '复制';
    copyBtn.addEventListener('click', () => copyMessage(m.content, copyBtn));
    actions.appendChild(copyBtn);
    bubble.appendChild(actions);
  }

  row.appendChild(avatar);
  row.appendChild(bubble);
  wrapper.appendChild(row);

  // Pipeline footer for AI messages
  if (m.role === 'ai') {
    const pipeline = formatPipeline(m.metadata);
    if (pipeline) {
      const footer = document.createElement('div');
      footer.className = 'pipeline-footer';
      footer.textContent = pipeline;
      wrapper.appendChild(footer);
    }
  }

  // Highlight code blocks
  bubble.querySelectorAll('pre code').forEach(block => {
    hljs.highlightElement(block);
  });

  return wrapper;
}

// ─── Streaming message element ────────────────────────
function createStreamingBubble() {
  const row = document.createElement('div');
  row.className = 'message-row ai';
  row.id = 'streamingRow';

  const avatar = document.createElement('div');
  avatar.className = 'avatar-icon';
  avatar.textContent = 'AI';

  const bubble = document.createElement('div');
  bubble.className = 'message-bubble';
  bubble.id = 'streamingBubble';
  bubble.innerHTML = '<div class="typing-dots"><span></span><span></span><span></span></div>';

  row.appendChild(avatar);
  row.appendChild(bubble);
  return row;
}

// ─── Copy ─────────────────────────────────────────────
async function copyMessage(text, btn) {
  try {
    await navigator.clipboard.writeText(text);
    btn.textContent = '已复制';
    btn.classList.add('copied');
    setTimeout(() => { btn.textContent = '复制'; btn.classList.remove('copied'); }, 2000);
  } catch { /* ignore */ }
}

// ─── Send message ─────────────────────────────────────
async function sendMessage(message) {
  if (state.streaming || !message.trim()) return;
  state.streaming = true;

  // Ensure session exists
  if (!state.activeId) {
    try {
      const r = await fetch('/api/session', { method: 'POST' });
      const data = await r.json();
      state.activeId = data.session_id;
      addSession(state.activeId, '新对话');
      renderSessions();
    } catch {
      showToast('创建会话失败，请检查服务是否运行');
      state.streaming = false;
      return;
    }
  }

  dom.welcome.style.display = 'none';
  dom.input.value = '';
  dom.input.style.height = 'auto';
  dom.sendBtn.disabled = true;

  // Add user message to UI
  state.messages.push({ role: 'user', content: message });
  appendMessageToDOM({ role: 'user', content: message });
  updateSessionTitle(state.activeId, message);

  // Add streaming bubble
  const streamRow = createStreamingBubble();
  dom.messages.appendChild(streamRow);
  const streamBubble = $('#streamingBubble');
  scrollToBottom();

  let fullContent = '';
  let finalMeta = null;
  const STREAM_URL = `/api/chat/${state.activeId}/stream`;

  try {
    const response = await fetch(STREAM_URL, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ message }),
    });

    if (!response.ok) {
      throw new Error(`HTTP ${response.status}`);
    }

    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = '';

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });

      // Process complete SSE events
      const lines = buffer.split('\n');
      buffer = lines.pop() || ''; // keep incomplete line in buffer

      for (const line of lines) {
        if (!line.startsWith('data: ')) continue;
        try {
          const data = JSON.parse(line.slice(6));
          if (data.error) {
            throw new Error(data.message || 'Stream error');
          }
          if (data.done) {
            finalMeta = data;
          } else if (data.delta) {
            fullContent += data.delta;
            streamBubble.innerHTML = marked.parse(fullContent);
            // Re-highlight code blocks
            streamBubble.querySelectorAll('pre code').forEach(block => {
              hljs.highlightElement(block);
            });
            scrollToBottom();
          }
        } catch (e) {
          if (e.message === 'Stream error') throw e;
          // Skip malformed JSON lines
        }
      }
    }
  } catch (e) {
    console.error('Stream error:', e);
    fullContent = fullContent || '抱歉，服务暂时不可用，请稍后重试。';
  } finally {
    state.streaming = false;
    dom.sendBtn.disabled = false;
    dom.input.focus();
  }

  // Replace streaming bubble with final rendered message
  const aiMsg = {
    role: 'ai',
    content: fullContent,
    metadata: finalMeta?.metadata || { message_type: '咨询' },
  };
  state.messages.push(aiMsg);
  const finalEl = createMessageElement(aiMsg, true);
  streamRow.replaceWith(finalEl);

  // Update session
  addSession(state.activeId, null); // refresh time
  renderSessions();
  scrollToBottom();
}

function appendMessageToDOM(m) {
  const row = createMessageElement(m, false);
  dom.messages.appendChild(row);
}

// ─── Scroll ───────────────────────────────────────────
function scrollToBottom() {
  requestAnimationFrame(() => {
    dom.messages.scrollTop = dom.messages.scrollHeight;
  });
}

// ─── Upload ───────────────────────────────────────────
async function uploadPDF(file) {
  if (!state.activeId) {
    try {
      const r = await fetch('/api/session', { method: 'POST' });
      const data = await r.json();
      state.activeId = data.session_id;
      addSession(state.activeId, '新对话');
      renderSessions();
    } catch { return; }
  }

  const form = new FormData();
  form.append('file', file);

  try {
    showToast('正在解析文档...');
    const r = await fetch(`/api/upload/${state.activeId}`, { method: 'POST', body: form });
    if (!r.ok) {
      const err = await r.json();
      showToast(err.detail || '上传失败');
      return;
    }
    const data = await r.json();
    state.uploadedDoc = { name: data.filename };
    updateUploadBar();
    showToast(`文档 "${data.filename}" 已就绪，共 ${data.chunks} 个片段`);
  } catch {
    showToast('上传失败，请检查网络');
  }
}

function updateUploadBar() {
  if (state.uploadedDoc) {
    dom.uploadBar.hidden = false;
    dom.uploadName.textContent = state.uploadedDoc.name;
  } else {
    dom.uploadBar.hidden = true;
  }
}

function removeDocument() {
  state.uploadedDoc = null;
  updateUploadBar();
  showToast('文档已移除');
}

// ─── Toast ────────────────────────────────────────────
let toastTimer;
function showToast(msg) {
  clearTimeout(toastTimer);
  dom.toast.hidden = false;
  dom.toast.textContent = msg;
  dom.toast.classList.remove('fadeout');
  toastTimer = setTimeout(() => {
    dom.toast.classList.add('fadeout');
    setTimeout(() => { dom.toast.hidden = true; }, 300);
  }, 2500);
}

// ─── Helpers ──────────────────────────────────────────
function escapeHtml(text) {
  const div = document.createElement('div');
  div.textContent = text;
  return div.innerHTML;
}

function formatPipeline(meta) {
  if (!meta) return '';
  const parts = ['安全检测 ✓'];

  const intent = meta.intent || meta.message_type;
  if (intent) parts.push(`意图 → ${intent}`);

  const agent = meta.agent;
  if (agent) {
    const agentNames = {
      legal_consultation: '法律咨询 Agent',
      case_analysis: '案情分析 Agent',
      document_qa: '文档问答 Agent',
      document_writing: '文书撰写 Agent',
      follow_up: '追问处理 Agent',
    };
    parts.push(agentNames[agent] || agent);
  }

  if (meta.law_count) parts.push(`RAG 检索 (${meta.law_count} 条法条)`);
  if (meta.case_count) parts.push(`联网搜索 (${meta.case_count} 条类案)`);
  if (meta.chunks_found !== undefined) parts.push(`文档检索 (${meta.chunks_found} 片段)`);
  if (meta.template) parts.push(`模板: ${meta.template_name || meta.template}`);
  if (meta.review) parts.push('全文审查');
  if (meta.mode === 'context_only') parts.push('纯上下文回答');
  if (meta.blocked) parts.push(`已拦截: ${meta.reason || ''}`);

  return parts.join(' → ');
}

function showWelcome() {
  dom.welcome.style.display = 'flex';
  dom.title.textContent = 'LawAgent';
  updateUploadBar();
  // Remove existing message rows
  dom.messages.querySelectorAll('.message-row').forEach(el => el.remove());
}

// ─── Event Listeners ──────────────────────────────────

// Send
dom.sendBtn.addEventListener('click', () => sendMessage(dom.input.value));
dom.input.addEventListener('keydown', (e) => {
  if (e.key === 'Enter' && !e.shiftKey) {
    e.preventDefault();
    sendMessage(dom.input.value);
  }
});

// Auto-resize textarea
dom.input.addEventListener('input', () => {
  dom.input.style.height = 'auto';
  dom.input.style.height = Math.min(dom.input.scrollHeight, 180) + 'px';
});

// New chat
$('#btnNewChat').addEventListener('click', () => {
  if (state.streaming) return;
  state.activeId = null;
  state.messages = [];
  state.uploadedDoc = null;
  renderSessions();
  showWelcome();
});

// Switch session
dom.sessionList.addEventListener('click', (e) => {
  const item = e.target.closest('.session-item');
  if (item && !e.target.closest('.btn-delete-session')) {
    switchSession(item.dataset.id);
  }
});

// Sidebar toggle (mobile)
$('#btnMenu').addEventListener('click', () => {
  dom.sidebar.classList.toggle('open');
  dom.overlay.classList.toggle('show');
});
dom.overlay.addEventListener('click', () => {
  dom.sidebar.classList.remove('open');
  dom.overlay.classList.remove('show');
});

// Upload
$('#btnUpload').addEventListener('click', () => dom.fileInput.click());
dom.fileInput.addEventListener('change', () => {
  if (dom.fileInput.files.length) {
    uploadPDF(dom.fileInput.files[0]);
    dom.fileInput.value = '';
  }
});
$('#btnRemoveDoc').addEventListener('click', removeDocument);

// Quick actions
dom.messages.addEventListener('click', (e) => {
  const btn = e.target.closest('.quick-btn');
  if (btn) sendMessage(btn.dataset.prompt);
});

// Keyboard shortcuts
document.addEventListener('keydown', (e) => {
  if ((e.ctrlKey || e.metaKey) && e.key === 'n') {
    e.preventDefault();
    $('#btnNewChat').click();
  }
  if (e.key === 'Escape') {
    dom.input.value = '';
    dom.input.focus();
  }
});

// ─── Init ─────────────────────────────────────────────
loadSessions();
renderSessions();

// If there are sessions, auto-load the latest
if (state.sessions.length > 0) {
  switchSession(state.sessions[0].id);
}
