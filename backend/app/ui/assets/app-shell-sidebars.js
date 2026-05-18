(function () {
  try {
    if (window.__AUTOFLIP_SHARED_SIDEBARS__) return;
    window.__AUTOFLIP_SHARED_SIDEBARS__ = true;

    const scriptEl = document.currentScript;
    const appHome = (scriptEl && scriptEl.dataset && scriptEl.dataset.appHome) || 'index.html';
    const FAVORITES_STORAGE_KEY = 'osrs_flip_favorites_v1';
    const FAVORITES_COLLAPSED_STORAGE_KEY = 'osrs_flip_favorites_collapsed_v1';
    const AI_COPILOT_COLLAPSED_STORAGE_KEY = 'osrs_flip_ai_copilot_collapsed_v1';
    const SHARED_AI_CHAT_STORAGE_KEY = 'osrs_flip_shared_ai_chat_v1';
    const SHARED_AI_STATUS_STORAGE_KEY = 'osrs_flip_shared_ai_status_v1';
    const SHARED_AI_PENDING_REQUEST_STORAGE_KEY = 'osrs_flip_shared_ai_pending_request_v1';
    const SHARED_AI_DEBUG_STORAGE_KEY = 'osrs_flip_shared_ai_debug_v1';
    const AI_TIMEOUT_MS = 45000;
    let activeAdvisorRequestController = null;
    let activeAdvisorRequestTimeout = null;
    let latestAdvisorRequestId = 0;
    let navigationInProgress = false;
    const aiCopilotDebugState = {
      activeRequest: null,
      lastRequest: null,
      lastBackendDebug: null,
      events: [],
    };


    function escapeHtml(value) {
      return String(value == null ? '' : value)
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#39;');
    }


function withApiBase(path) {
  const base = String((localStorage.getItem('osrs_api_base') || '')).trim().replace(/\/$/, '');
  return `${base}${path}`;
}

function safeJsonParse(raw, fallback) {
  try { return raw ? JSON.parse(raw) : fallback; } catch (_error) { return fallback; }
}

function loadStoredChatMessages() {
  try { return safeJsonParse(localStorage.getItem(SHARED_AI_CHAT_STORAGE_KEY), []); } catch (_error) { return []; }
}

function saveStoredChatMessages(messages) {
  try { localStorage.setItem(SHARED_AI_CHAT_STORAGE_KEY, JSON.stringify(Array.isArray(messages) ? messages.slice(-40) : [])); } catch (_error) {}
}

function loadStoredPendingRequest() {
  try { return safeJsonParse(localStorage.getItem(SHARED_AI_PENDING_REQUEST_STORAGE_KEY), null); } catch (_error) { return null; }
}

function saveStoredPendingRequest(payload) {
  try {
    if (payload) localStorage.setItem(SHARED_AI_PENDING_REQUEST_STORAGE_KEY, JSON.stringify(payload));
    else localStorage.removeItem(SHARED_AI_PENDING_REQUEST_STORAGE_KEY);
  } catch (_error) {}
}

function setStoredStatus(text, kind) {
  const payload = { text: String(text || ''), kind: String(kind || ''), at: new Date().toISOString() };
  try { localStorage.setItem(SHARED_AI_STATUS_STORAGE_KEY, JSON.stringify(payload)); } catch (_error) {}
  const statusEl = document.getElementById('sharedAdvisorStatus');
  if (statusEl) {
    statusEl.textContent = payload.text;
    statusEl.className = `shared-status ${payload.kind || ''}`.trim();
  }
}

function restoreStoredStatus() {
  const statusEl = document.getElementById('sharedAdvisorStatus');
  if (!statusEl) return;
  const payload = safeJsonParse(localStorage.getItem(SHARED_AI_STATUS_STORAGE_KEY), null);
  if (payload && payload.text) {
    statusEl.textContent = payload.text;
    statusEl.className = `shared-status ${payload.kind || ''}`.trim();
  }
}

function pushAiCopilotDebugEvent(label, payload = null) {
  const entry = { at: new Date().toISOString(), label };
  if (payload !== null && payload !== undefined) entry.payload = payload;
  aiCopilotDebugState.events.push(entry);
  if (aiCopilotDebugState.events.length > 30) aiCopilotDebugState.events = aiCopilotDebugState.events.slice(-30);
}

function buildAiCopilotDebugSnapshot() {
  const replyEl = document.getElementById('sharedAdvisorReply');
  const promptEl = document.getElementById('sharedAdvisorPrompt');
  const statusEl = document.getElementById('sharedAdvisorStatus');
  const buttonEl = document.getElementById('sharedAskAiBtn');
  const active = aiCopilotDebugState.activeRequest ? {
    ...aiCopilotDebugState.activeRequest,
    age_ms: aiCopilotDebugState.activeRequest.started_at_ms ? Date.now() - aiCopilotDebugState.activeRequest.started_at_ms : null,
  } : null;
  return {
    generated_at: new Date().toISOString(),
    source: 'shared-sidebar',
    advisor_status: {
      text: statusEl?.textContent || '',
      class_name: statusEl?.className || '',
    },
    frontend: {
      timeout_ms: AI_TIMEOUT_MS,
      latest_request_id: latestAdvisorRequestId,
      request_in_flight: Boolean(activeAdvisorRequestController),
      ask_button_disabled: Boolean(buttonEl?.disabled),
      input_chars: String(promptEl?.value || '').length,
      rendered_message_count: replyEl?.querySelectorAll('.shared-chat-message').length || 0,
      current_path: window.location.pathname,
    },
    active_request: active,
    last_request: aiCopilotDebugState.lastRequest,
    backend_debug: aiCopilotDebugState.lastBackendDebug,
    recent_events: aiCopilotDebugState.events.slice(-20),
  };
}

function renderAiCopilotDebug() {
  const snapshot = buildAiCopilotDebugSnapshot();
  try { localStorage.setItem(SHARED_AI_DEBUG_STORAGE_KEY, JSON.stringify(snapshot)); } catch (_error) {}
  window.dispatchEvent(new CustomEvent('autoflip:shared-ai-debug-updated', { detail: snapshot }));
}

function setAskButtonState(disabled) {
  const buttonEl = document.getElementById('sharedAskAiBtn');
  if (buttonEl) buttonEl.disabled = !!disabled;
}

function restoreStoredChat() {
  const replyEl = document.getElementById('sharedAdvisorReply');
  if (!replyEl) return;
  const messages = loadStoredChatMessages();
  if (!messages.length) {
    replyEl.innerHTML = '<div class="shared-empty">Good prompts: weakest trades, lower risk for 12h, what to replace first, or whether your budget looks too concentrated.</div>';
    return;
  }
  replyEl.innerHTML = messages.map((message) => `
    <div class="shared-chat-message ${escapeHtml(message.role || 'assistant')}">
      <div class="shared-chat-head"><span>${message.role === 'user' ? 'You' : 'Advisor'}</span><span>${escapeHtml(message.at || '')}</span></div>
      ${message.html || '<p></p>'}
    </div>`).join('');
  replyEl.scrollTop = replyEl.scrollHeight;
}

    function ensureStyles() {
      if (document.getElementById('sharedSidebarStyles')) return;
      const style = document.createElement('style');
      style.id = 'sharedSidebarStyles';
      style.textContent = `
        body.shared-sidebars-active {
          --shared-left-rail-width: 290px;
          --shared-right-rail-width: 340px;
          padding-left: calc(var(--shared-left-rail-width) + 28px) !important;
          padding-right: calc(var(--shared-right-rail-width) + 28px) !important;
        }
        body.shared-sidebars-active .page-wrap,
        body.shared-sidebars-active .shell,
        body.shared-sidebars-active .wrap {
          max-width: calc(100vw - var(--shared-left-rail-width) - var(--shared-right-rail-width) - 96px) !important;
          width: min(100%, calc(100vw - var(--shared-left-rail-width) - var(--shared-right-rail-width) - 96px)) !important;
          margin-left: auto !important;
          margin-right: auto !important;
        }
        .shared-shell-sidebar {
          position: fixed;
          top: 0;
          bottom: 0;
          z-index: 60;
          overflow-y: auto;
          overflow-x: hidden;
          box-sizing: border-box;
          background: linear-gradient(180deg, rgba(27, 17, 10, 0.985), rgba(15, 9, 5, 0.99));
          box-shadow: 0 18px 36px rgba(0,0,0,0.32), inset 0 0 0 1px rgba(245,231,200,0.04);
          color: #f6ead1;
        }
        .shared-shell-sidebar * { box-sizing: border-box; }
        .shared-shell-sidebar a, .shared-shell-sidebar button, .shared-shell-sidebar textarea { font: inherit; }
        .shared-favorites-sidebar {
          left: 0;
          width: var(--shared-left-rail-width);
          border-right: 1px solid rgba(184, 137, 45, 0.28);
          padding: 18px 22px 18px 14px;
          transition: transform 0.24s ease, box-shadow 0.24s ease;
        }
        body.shared-favorites-collapsed .shared-favorites-sidebar {
          transform: translateX(calc(-100% + 22px));
          box-shadow: 0 0 0 rgba(0,0,0,0);
        }
        .shared-ai-sidebar {
          right: 0;
          width: var(--shared-right-rail-width);
          border-left: 1px solid rgba(184, 137, 45, 0.28);
          padding: 18px 14px 18px 22px;
          transition: transform 0.24s ease, box-shadow 0.24s ease;
        }
        body.shared-ai-collapsed .shared-ai-sidebar {
          transform: translateX(calc(100% - 22px));
          box-shadow: 0 0 0 rgba(0,0,0,0);
        }
        .shared-edge-hitbox {
          position: absolute;
          top: 0;
          bottom: 0;
          width: 22px;
          cursor: pointer;
          z-index: 2;
          opacity: 0.78;
          transition: opacity 0.18s ease, box-shadow 0.18s ease;
        }
        .shared-edge-hitbox:hover { opacity: 1; }
        .shared-edge-hitbox::after {
          content: '';
          position: absolute;
          top: 0;
          bottom: 0;
          width: 2px;
          background: linear-gradient(180deg, rgba(241,223,178,0.92), rgba(184,137,45,0.66), rgba(212,167,67,0.88));
        }
        .shared-favorites-sidebar .shared-edge-hitbox {
          right: 0;
          background: linear-gradient(270deg, rgba(212,167,67,0.44) 0%, rgba(166,117,34,0.3) 18%, rgba(120,76,22,0.04) 68%, rgba(120,76,22,0) 100%);
        }
        .shared-favorites-sidebar .shared-edge-hitbox::after { right: 0; }
        .shared-ai-sidebar .shared-edge-hitbox {
          left: 0;
          background: linear-gradient(90deg, rgba(212,167,67,0.44) 0%, rgba(166,117,34,0.3) 18%, rgba(120,76,22,0.04) 68%, rgba(120,76,22,0) 100%);
        }
        .shared-ai-sidebar .shared-edge-hitbox::after { left: 0; }
        .shared-toggle-btn {
          position: absolute;
          top: 50%;
          transform: translateY(-50%);
          width: 16px;
          height: 54px;
          border-radius: 999px;
          border: 1px solid rgba(212, 167, 67, 0.44);
          background: linear-gradient(180deg, rgba(55, 34, 18, 0.98), rgba(24, 15, 9, 0.98));
          box-shadow: 0 8px 18px rgba(0, 0, 0, 0.3);
          color: #f1dfb2;
          font-size: 11px;
          line-height: 1;
          display: flex;
          align-items: center;
          justify-content: center;
          z-index: 3;
          cursor: pointer;
        }
        .shared-favorites-sidebar .shared-toggle-btn { right: 3px; }
        .shared-ai-sidebar .shared-toggle-btn { left: 3px; }
        .shared-sidebar-header { margin-bottom: 14px; }
        .shared-sidebar-title { margin: 0 0 10px; font-size: 18px; color: #f8fafc; }
        .shared-sidebar-subtitle { margin: 0; font-size: 12px; color: #b79a6a; line-height: 1.45; }
        .shared-count-pill, .shared-pill {
          display: inline-flex;
          align-items: center;
          gap: 6px;
          margin-top: 8px;
          padding: 6px 10px;
          border-radius: 999px;
          border: 1px solid rgba(212,167,67,0.18);
          background: rgba(45,28,17,0.88);
          color: #f1dfb2;
          font-size: 12px;
          font-weight: 700;
        }
        .shared-favorites-list, .shared-chat-list {
          display: flex;
          flex-direction: column;
          gap: 8px;
        }
        .shared-favorite-item, .shared-card, .shared-chat-message {
          background: rgba(31, 19, 12, 0.9);
          border: 1px solid rgba(177, 132, 45, 0.18);
          border-radius: 14px;
          padding: 12px;
        }
        .shared-favorite-item {
          cursor: pointer;
          transition: background 0.15s ease, transform 0.15s ease;
        }
        .shared-favorite-item:hover { background: rgba(58, 34, 18, 0.95); transform: translateY(-1px); }
        .shared-favorite-name { font-weight: 700; color: #f8fafc; }
        .shared-favorite-meta { margin-top: 4px; font-size: 12px; color: #d8c6a1; }
        .shared-favorite-stats { display: flex; gap: 8px; flex-wrap: wrap; margin-top: 8px; }
        .shared-favorite-stat { padding: 4px 8px; border-radius: 999px; background: rgba(60,37,20,0.74); border: 1px solid rgba(212,167,67,0.14); color: #e9d4a8; font-size: 11px; font-weight: 700; }
        .shared-empty { font-size: 13px; color: #b79a6a; line-height: 1.55; padding: 8px 2px; }
        .shared-card h2 { margin: 0 0 8px; font-size: 18px; color: #f6ead1; }
        .shared-card p { margin: 0; color: #d8c6a1; line-height: 1.55; }
        .shared-chat-list {
          min-height: 220px;
          max-height: 40vh;
          overflow: auto;
          margin-top: 10px;
        }
        .shared-chat-message { color: #f6ead1; }
        .shared-chat-message.user { background: rgba(58,34,18,0.96); }
        .shared-chat-head {
          display: flex;
          justify-content: space-between;
          gap: 10px;
          margin-bottom: 6px;
          font-size: 11px;
          text-transform: uppercase;
          letter-spacing: 0.06em;
          color: #d7b06a;
        }
        .shared-chat-message p { margin: 0; line-height: 1.55; }
        .shared-chat-message ul { margin: 8px 0 0 18px; color: #d8c6a1; }
        .shared-chat-message li + li { margin-top: 6px; }
        .shared-chat-composer { margin-top: 12px; display: grid; gap: 8px; }
        .shared-chat-composer label { color: #f1dfb2; font-size: 13px; font-weight: 700; }
        .shared-chat-composer textarea {
          width: 100%;
          min-height: 110px;
          resize: vertical;
          border-radius: 14px;
          border: 1px solid rgba(184,137,45,0.24);
          background: rgba(15,9,5,0.86);
          color: #f6ead1;
          padding: 12px;
          outline: none;
        }
        .shared-chat-composer textarea:focus {
          border-color: rgba(239,202,119,0.58);
          box-shadow: 0 0 0 2px rgba(197,139,53,0.12);
        }
        .shared-chat-actions { display: flex; gap: 8px; flex-wrap: wrap; }
        .shared-btn, .shared-prompt-chip {
          appearance: none;
          border: 1px solid rgba(184,137,45,0.24);
          background: rgba(120,78,23,0.52);
          color: #f0ddb1;
          border-radius: 12px;
          padding: 10px 12px;
          font-weight: 700;
          cursor: pointer;
        }
        .shared-btn.primary {
          background: linear-gradient(180deg, rgba(244,196,91,0.98), rgba(214,151,39,0.96));
          color: #2a1a0f;
          border-color: rgba(255,214,124,0.78);
        }
        .shared-prompt-row { display: flex; gap: 8px; flex-wrap: wrap; margin-top: 10px; }
        .shared-status { margin-top: 8px; color: #d8c6a1; font-size: 12px; line-height: 1.45; }
        .shared-status.success { color: #b6efc1; }
        .shared-status.warn { color: #f2d99a; }
        .shared-status.error { color: #f0b4aa; }
        body.shared-favorites-collapsed .shared-favorites-sidebar .shared-toggle-btn::before { content: '❯'; }
        body:not(.shared-favorites-collapsed) .shared-favorites-sidebar .shared-toggle-btn::before { content: '❮'; }
        body.shared-ai-collapsed .shared-ai-sidebar .shared-toggle-btn::before { content: '❮'; }
        body:not(.shared-ai-collapsed) .shared-ai-sidebar .shared-toggle-btn::before { content: '❯'; }
        @media (max-width: 1320px) {
          body.shared-sidebars-active {
            padding-left: 18px !important;
            padding-right: 18px !important;
          }
          body.shared-sidebars-active .page-wrap,
          body.shared-sidebars-active .shell,
          body.shared-sidebars-active .wrap {
            max-width: none !important;
            width: auto !important;
          }
          .shared-shell-sidebar {
            position: static;
            width: auto;
            border: 1px solid rgba(184, 137, 45, 0.18);
            box-shadow: none;
            transform: none !important;
            margin-bottom: 18px;
            padding: 18px;
          }
          .shared-edge-hitbox, .shared-toggle-btn { display: none !important; }
          .shared-chat-list { max-height: none; }
        }
      `;
      document.head.appendChild(style);
    }

    function loadFavorites() {
      try {
        const raw = localStorage.getItem(FAVORITES_STORAGE_KEY);
        const parsed = raw ? JSON.parse(raw) : [];
        return Array.isArray(parsed) ? parsed.filter((entry) => entry && entry.name) : [];
      } catch (_error) {
        return [];
      }
    }

    function saveFavorites(items) {
      try {
        localStorage.setItem(FAVORITES_STORAGE_KEY, JSON.stringify((items || []).slice(0, 100)));
      } catch (_error) {}
    }

    function formatCoins(value) {
      const num = Number(value);
      if (!Number.isFinite(num)) return null;
      if (Math.abs(num) >= 1000000) return `${(num / 1000000).toFixed(2).replace(/\.00$/, '')}m`;
      if (Math.abs(num) >= 1000) return `${(num / 1000).toFixed(1).replace(/\.0$/, '')}k`;
      return `${Math.round(num)}`;
    }

    function renderFavoritesList() {
      const listEl = document.getElementById('sharedFavoritesList');
      const countEl = document.getElementById('sharedFavoritesCount');
      if (!listEl || !countEl) return;
      const items = loadFavorites();
      countEl.textContent = `${items.length} tracked item${items.length === 1 ? '' : 's'}`;
      if (!items.length) {
        listEl.innerHTML = '<div class="shared-empty">No favorites yet. Favorite items from the app dashboard, then they will stay visible here on every account page.</div>';
        return;
      }
      listEl.innerHTML = items.map((item, index) => {
        const spread = formatCoins(item.profit_per_item || item.spread_gp || item.profit_after_tax);
        const roi = Number(item.roi_pct || item.roi_pct_after_tax);
        const volume = formatCoins(item.recent_volume || item.volume_1h || item.day_volume);
        const meta = [item.buy_price && item.sell_price ? `${formatCoins(item.buy_price)} → ${formatCoins(item.sell_price)}` : '', item.category || item.confidence_band || ''].filter(Boolean).join(' • ');
        return `
          <div class="shared-favorite-item" data-favorite-index="${index}">
            <div class="shared-favorite-name">${escapeHtml(item.name || 'Unknown item')}</div>
            <div class="shared-favorite-meta">${escapeHtml(meta || 'Saved watch item')}</div>
            <div class="shared-favorite-stats">
              ${spread ? `<span class="shared-favorite-stat">Spread ${escapeHtml(spread)}</span>` : ''}
              ${Number.isFinite(roi) ? `<span class="shared-favorite-stat">ROI ${roi.toFixed(2)}%</span>` : ''}
              ${volume ? `<span class="shared-favorite-stat">Vol ${escapeHtml(volume)}</span>` : ''}
            </div>
          </div>`;
      }).join('');
      Array.from(listEl.querySelectorAll('[data-favorite-index]')).forEach((node) => {
        node.addEventListener('click', () => {
          const index = Number(node.getAttribute('data-favorite-index'));
          const entry = loadFavorites()[index];
          if (!entry) return;
          try {
            localStorage.setItem('osrs_flip_open_favorite_item_v1', JSON.stringify(entry));
          } catch (_error) {}
          window.location.href = appHome;
        });
      });
    }


function appendChatMessage(role, html) {
  const replyEl = document.getElementById('sharedAdvisorReply');
  if (!replyEl) return;
  const emptyEl = replyEl.querySelector('.shared-empty');
  if (emptyEl) emptyEl.remove();
  const item = document.createElement('div');
  item.className = `shared-chat-message ${role}`;
  const timestamp = new Date().toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' });
  item.innerHTML = `<div class="shared-chat-head"><span>${role === 'user' ? 'You' : 'Advisor'}</span><span>${timestamp}</span></div>${html}`;
  replyEl.appendChild(item);
  replyEl.scrollTop = replyEl.scrollHeight;
  const nextMessages = loadStoredChatMessages();
  nextMessages.push({ role, html, at: timestamp });
  saveStoredChatMessages(nextMessages);
}

function renderAdvisorReply(data) {
      const reply = data && data.reply_json;
      if (!reply) {
        appendChatMessage('assistant', `<p>${escapeHtml((data && data.error) || 'No reply returned.')}</p>`);
        return;
      }
      const actions = Array.isArray(reply.actions) ? reply.actions : [];
      const topPicks = Array.isArray(reply.top_picks) ? reply.top_picks : [];
      const notes = Array.isArray(reply.notes) ? reply.notes : [];
      let html = `<p>${escapeHtml(reply.summary || 'Reply ready.')}</p>`;
      if (actions.length) html += `<ul>${actions.slice(0, 6).map((action) => `<li>${escapeHtml(action.item || '-')}: ${escapeHtml(action.decision || '-')}${action.reason ? ` — ${escapeHtml(action.reason)}` : ''}</li>`).join('')}</ul>`;
      else if (topPicks.length) html += `<ul>${topPicks.slice(0, 6).map((pick) => `<li>${escapeHtml(typeof pick === 'string' ? pick : JSON.stringify(pick))}</li>`).join('')}</ul>`;
      else if (notes.length) html += `<ul>${notes.slice(0, 6).map((note) => `<li>${escapeHtml(note)}</li>`).join('')}</ul>`;
      appendChatMessage('assistant', html);
    }

    async function readJsonResponse(response) {
      const text = await response.text();
      let data = {};
      try {
        data = text ? JSON.parse(text) : {};
      } catch (_error) {
        throw new Error(`HTTP ${response.status} ${response.statusText}: ${text.slice(0, 240)}`);
      }
      if (!response.ok) {
        throw new Error(`HTTP ${response.status} ${response.statusText}: ${text.slice(0, 240)}`);
      }
      return data;
    }


    async function askAdvisor(prompt, options = {}) {
      const normalizedPrompt = String(prompt || '').trim();
      if (!normalizedPrompt) return;
      const statusEl = document.getElementById('sharedAdvisorStatus');
      const promptEl = document.getElementById('sharedAdvisorPrompt');
      const requestId = Number(options.requestId || (latestAdvisorRequestId + 1));
      const resume = !!options.resume;
      latestAdvisorRequestId = Math.max(latestAdvisorRequestId, requestId);

      if (activeAdvisorRequestController && !resume) {
        pushAiCopilotDebugEvent('request_aborted_for_new_submit', {
          request_id: aiCopilotDebugState.activeRequest?.request_id || latestAdvisorRequestId,
          reason: 'replaced_by_new_submit',
        });
        try { activeAdvisorRequestController.abort(); } catch (_error) {}
        activeAdvisorRequestController = null;
      }
      if (activeAdvisorRequestTimeout) {
        clearTimeout(activeAdvisorRequestTimeout);
        activeAdvisorRequestTimeout = null;
      }

      if (!resume) appendChatMessage('user', `<p>${escapeHtml(normalizedPrompt).replace(/
/g, '<br>')}</p>`);
      if (statusEl) {
        statusEl.textContent = resume ? 'Resuming advisor request on this page...' : 'Analyzing OSRS trade context...';
        statusEl.className = 'shared-status warn';
      }
      setStoredStatus(resume ? 'Resuming advisor request on this page...' : 'Analyzing OSRS trade context...', 'warn');
      setAskButtonState(true);

      const controller = new AbortController();
      activeAdvisorRequestController = controller;
      const startedAtMs = Number(options.startedAtMs || Date.now());
      const startedAtIso = options.startedAtIso || new Date(startedAtMs).toISOString();
      aiCopilotDebugState.activeRequest = {
        request_id: requestId,
        started_at: startedAtIso,
        started_at_ms: startedAtMs,
        prompt: normalizedPrompt,
        prompt_chars: normalizedPrompt.length,
        resumed: resume,
        transport: 'fetch:/ai/advice',
      };
      saveStoredPendingRequest({
        request_id: requestId,
        prompt: normalizedPrompt,
        started_at_ms: startedAtMs,
        started_at: startedAtIso,
        page_path: window.location.pathname,
      });
      pushAiCopilotDebugEvent(resume ? 'request_resumed' : 'request_started', {
        request_id: requestId,
        prompt_chars: normalizedPrompt.length,
        resumed: resume,
      });
      renderAiCopilotDebug();

      activeAdvisorRequestTimeout = setTimeout(() => {
        pushAiCopilotDebugEvent('frontend_timeout_fired', { request_id: requestId, timeout_ms: AI_TIMEOUT_MS });
        renderAiCopilotDebug();
        try { controller.abort('shared-timeout'); } catch (_error) {}
      }, AI_TIMEOUT_MS);

      try {
        const response = await fetch(withApiBase('/ai/advice'), {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ message: normalizedPrompt }),
          signal: controller.signal,
        });
        const data = await readJsonResponse(response);
        if (requestId !== latestAdvisorRequestId) return;
        renderAdvisorReply(data);
        aiCopilotDebugState.lastBackendDebug = data.debug || null;
        aiCopilotDebugState.lastRequest = {
          request_id: requestId,
          started_at: startedAtIso,
          finished_at: new Date().toISOString(),
          duration_ms: Date.now() - startedAtMs,
          prompt: normalizedPrompt,
          prompt_chars: normalizedPrompt.length,
          outcome: data.error && !data.reply_json ? 'backend_error_fallback' : 'reply_rendered',
          response_model: data.model || null,
          backend_mode: data.reply_json?.mode || null,
          backend_error: data.error || null,
        };
        aiCopilotDebugState.activeRequest = null;
        saveStoredPendingRequest(null);
        pushAiCopilotDebugEvent('response_received', {
          request_id: requestId,
          model: data.model || null,
          backend_mode: data.reply_json?.mode || null,
          has_reply_json: Boolean(data.reply_json),
          error: data.error || null,
        });
        if (statusEl) {
          statusEl.textContent = `Reply ready${data && data.model ? ` (${data.model})` : ''}`;
          statusEl.className = 'shared-status success';
        }
        setStoredStatus(`Reply ready${data && data.model ? ` (${data.model})` : ''}`, 'success');
        if (promptEl) promptEl.value = '';
        renderAiCopilotDebug();
      } catch (error) {
        if (requestId !== latestAdvisorRequestId) return;
        const abortReason = controller.signal?.reason || null;
        if (error && error.name === 'AbortError' && navigationInProgress) {
          setStoredStatus('Continuing advisor request on the next page...', 'warn');
          pushAiCopilotDebugEvent('request_handed_off', { request_id: requestId, abort_reason: abortReason || 'navigation' });
          renderAiCopilotDebug();
          return;
        }
        aiCopilotDebugState.lastRequest = {
          request_id: requestId,
          started_at: startedAtIso,
          finished_at: new Date().toISOString(),
          duration_ms: Date.now() - startedAtMs,
          prompt: normalizedPrompt,
          prompt_chars: normalizedPrompt.length,
          outcome: error && error.name === 'AbortError' ? 'aborted' : 'request_error',
          abort_reason: abortReason,
          error_name: error?.name || null,
          error_message: String(error && error.message ? error.message : error),
        };
        aiCopilotDebugState.activeRequest = null;
        saveStoredPendingRequest(null);
        pushAiCopilotDebugEvent(error && error.name === 'AbortError' ? 'request_aborted' : 'request_failed', {
          request_id: requestId,
          abort_reason: abortReason,
          error_name: error?.name || null,
          error_message: String(error && error.message ? error.message : error),
        });
        appendChatMessage('assistant', `<p>The advisor hit a temporary issue, so here is the safe OSRS fallback: focus on weaker or slower trades first, then retry with a shorter market question.</p><p class="shared-status warn">${escapeHtml(String(error && error.message ? error.message : error))}</p>`);
        if (statusEl) {
          statusEl.textContent = error && error.name === 'AbortError' ? 'Advisor switched to a quicker preview.' : 'Advisor paused unexpectedly.';
          statusEl.className = 'shared-status warn';
        }
        setStoredStatus(error && error.name === 'AbortError' ? 'Advisor switched to a quicker preview.' : 'Advisor paused unexpectedly.', 'warn');
        renderAiCopilotDebug();
      } finally {
        if (requestId === latestAdvisorRequestId) setAskButtonState(false);
        if (activeAdvisorRequestTimeout) {
          clearTimeout(activeAdvisorRequestTimeout);
          activeAdvisorRequestTimeout = null;
        }
        if (activeAdvisorRequestController === controller) activeAdvisorRequestController = null;
        renderAiCopilotDebug();
      }
    }

    function setCollapsedState(storageKey, bodyClass, isCollapsed) {
      document.body.classList.toggle(bodyClass, Boolean(isCollapsed));
      try { localStorage.setItem(storageKey, isCollapsed ? 'true' : 'false'); } catch (_error) {}
    }

    function initializeCollapsedStates() {
      let favoritesCollapsed = false;
      let aiCollapsed = false;
      try { favoritesCollapsed = localStorage.getItem(FAVORITES_COLLAPSED_STORAGE_KEY) === 'true'; } catch (_error) {}
      try { aiCollapsed = localStorage.getItem(AI_COPILOT_COLLAPSED_STORAGE_KEY) === 'true'; } catch (_error) {}
      setCollapsedState(FAVORITES_COLLAPSED_STORAGE_KEY, 'shared-favorites-collapsed', favoritesCollapsed);
      setCollapsedState(AI_COPILOT_COLLAPSED_STORAGE_KEY, 'shared-ai-collapsed', aiCollapsed);
    }

    function buildSidebars() {
      if (document.getElementById('sharedFavoritesSidebar') || document.getElementById('sharedAiSidebar')) return;
      document.body.classList.add('shared-sidebars-active');
      const favorites = document.createElement('aside');
      favorites.id = 'sharedFavoritesSidebar';
      favorites.className = 'shared-shell-sidebar shared-favorites-sidebar';
      favorites.innerHTML = `
        <div class="shared-edge-hitbox" id="sharedFavoritesEdgeHitbox" aria-hidden="true"></div>
        <button class="shared-toggle-btn" id="sharedFavoritesToggleBtn" type="button" aria-label="Toggle favorites sidebar"></button>
        <div class="shared-sidebar-header">
          <h2 class="shared-sidebar-title">Favorites</h2>
          <p class="shared-sidebar-subtitle">Persistent watchlist pinned on the left side of your account pages.</p>
          <div id="sharedFavoritesCount" class="shared-count-pill">0 tracked items</div>
        </div>
        <div id="sharedFavoritesList" class="shared-favorites-list"></div>
      `;

      const ai = document.createElement('aside');
      ai.id = 'sharedAiSidebar';
      ai.className = 'shared-shell-sidebar shared-ai-sidebar';
      ai.innerHTML = `
        <div class="shared-edge-hitbox" id="sharedAiEdgeHitbox" aria-hidden="true"></div>
        <button class="shared-toggle-btn" id="sharedAiToggleBtn" type="button" aria-label="Toggle AI copilot sidebar"></button>
        <div class="shared-sidebar-header">
          <h2 class="shared-sidebar-title">AI Copilot</h2>
          <p class="shared-sidebar-subtitle">Sticky copilot pinned on the right so you can test AI behavior while staying on profile, membership, favorites, security, settings, and debug pages.</p>
          <div class="shared-pill">Preview only</div>
        </div>
        <div class="shared-card">
          <h2>Advisor Chat</h2>
          <p>Ask simple market questions, test fallback behavior, and keep the copilot visible while you use Debug.</p>
          <p id="sharedAdvisorStatus" class="shared-status">Preview only. Ask for priorities, tradeoffs, or a partial plan.</p>
          <div id="sharedAdvisorReply" class="shared-chat-list">
            <div class="shared-empty">Good prompts: weakest trades, lower risk for 12h, what to replace first, or whether your budget looks too concentrated.</div>
          </div>
          <div class="shared-prompt-row">
            <button type="button" class="shared-prompt-chip" data-shared-prompt="Hello">Hello</button>
            <button type="button" class="shared-prompt-chip" data-shared-prompt="Which current trades look weakest and why?">Weakest trades</button>
            <button type="button" class="shared-prompt-chip" data-shared-prompt="How should I lower risk without abandoning profit entirely?">Lower risk</button>
          </div>
          <div class="shared-chat-composer">
            <label for="sharedAdvisorPrompt">Ask the advisor</label>
            <textarea id="sharedAdvisorPrompt" placeholder="What should I cancel and what should I replace for 12 hours away?"></textarea>
            <div class="shared-chat-actions">
              <button id="sharedAskAiBtn" class="shared-btn primary" type="button">Ask Copilot</button>
              <button id="sharedClearAiBtn" class="shared-btn" type="button">Clear Chat</button>
            </div>
          </div>
        </div>
      `;

      document.body.insertBefore(favorites, document.body.firstChild);
      document.body.appendChild(ai);
    }

    function wireEvents() {
      const favoritesToggle = document.getElementById('sharedFavoritesToggleBtn');
      const favoritesHitbox = document.getElementById('sharedFavoritesEdgeHitbox');
      const aiToggle = document.getElementById('sharedAiToggleBtn');
      const aiHitbox = document.getElementById('sharedAiEdgeHitbox');
      const askBtn = document.getElementById('sharedAskAiBtn');
      const clearBtn = document.getElementById('sharedClearAiBtn');
      const promptEl = document.getElementById('sharedAdvisorPrompt');
      if (favoritesToggle) favoritesToggle.addEventListener('click', () => setCollapsedState(FAVORITES_COLLAPSED_STORAGE_KEY, 'shared-favorites-collapsed', !document.body.classList.contains('shared-favorites-collapsed')));
      if (favoritesHitbox) favoritesHitbox.addEventListener('click', () => setCollapsedState(FAVORITES_COLLAPSED_STORAGE_KEY, 'shared-favorites-collapsed', !document.body.classList.contains('shared-favorites-collapsed')));
      if (aiToggle) aiToggle.addEventListener('click', () => setCollapsedState(AI_COPILOT_COLLAPSED_STORAGE_KEY, 'shared-ai-collapsed', !document.body.classList.contains('shared-ai-collapsed')));
      if (aiHitbox) aiHitbox.addEventListener('click', () => setCollapsedState(AI_COPILOT_COLLAPSED_STORAGE_KEY, 'shared-ai-collapsed', !document.body.classList.contains('shared-ai-collapsed')));
      if (askBtn && promptEl) {
        askBtn.addEventListener('click', async () => {
          const prompt = String(promptEl.value || '').trim();
          if (!prompt) return;
          await askAdvisor(prompt);
          promptEl.value = '';
        });
        promptEl.addEventListener('keydown', (event) => {
          if (event.key === 'Enter' && (event.metaKey || event.ctrlKey)) {
            event.preventDefault();
            askBtn.click();
          }
        });
      }
      if (clearBtn) {
        clearBtn.addEventListener('click', () => {
          const replyEl = document.getElementById('sharedAdvisorReply');
          if (replyEl) replyEl.innerHTML = '<div class="shared-empty">Chat cleared. Ask a fresh question to test the copilot.</div>';
          saveStoredChatMessages([]);
          saveStoredPendingRequest(null);
          aiCopilotDebugState.activeRequest = null;
          aiCopilotDebugState.lastRequest = null;
          aiCopilotDebugState.lastBackendDebug = null;
          aiCopilotDebugState.events = [];
          setStoredStatus('Preview only. Ask for priorities, tradeoffs, or a partial plan.', '');
          try { localStorage.removeItem(SHARED_AI_DEBUG_STORAGE_KEY); } catch (_error) {}
          renderAiCopilotDebug();
        });
      }
      Array.from(document.querySelectorAll('[data-shared-prompt]')).forEach((button) => {
        button.addEventListener('click', () => {
          const prompt = button.getAttribute('data-shared-prompt') || '';
          const promptEl = document.getElementById('sharedAdvisorPrompt');
          if (promptEl) {
            promptEl.value = prompt;
            promptEl.focus();
          }
        });
      });
      window.addEventListener('storage', (event) => {
        if (!event) return;
        if (event.key === FAVORITES_STORAGE_KEY) renderFavoritesList();
        if (event.key === SHARED_AI_CHAT_STORAGE_KEY) restoreStoredChat();
        if (event.key === SHARED_AI_STATUS_STORAGE_KEY) restoreStoredStatus();
      });
      window.addEventListener('pagehide', () => {
        navigationInProgress = true;
        if (activeAdvisorRequestController) {
          try { activeAdvisorRequestController.abort('navigation'); } catch (_error) {}
        }
      });
    }

    function init() {
      ensureStyles();
      buildSidebars();
      initializeCollapsedStates();
      renderFavoritesList();
      restoreStoredChat();
      restoreStoredStatus();
      wireEvents();
      const pending = loadStoredPendingRequest();
      if (pending && pending.prompt && (!pending.started_at_ms || (Date.now() - Number(pending.started_at_ms) < AI_TIMEOUT_MS * 4))) {
        latestAdvisorRequestId = Math.max(latestAdvisorRequestId, Number(pending.request_id) || 0);
        setStoredStatus('Continuing advisor request on this page...', 'warn');
        setAskButtonState(true);
        queueMicrotask(() => { askAdvisor(pending.prompt, { resume: true, requestId: pending.request_id, startedAtMs: pending.started_at_ms, startedAtIso: pending.started_at }); });
      }
      renderAiCopilotDebug();
    }

    if (document.readyState === 'loading') {
      document.addEventListener('DOMContentLoaded', init, { once: true });
    } else {
      init();
    }
  } catch (_error) {
    // never break page rendering
  }
})();
