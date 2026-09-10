// ═══════════════════════════════════════════════════════
//  ASK PAGE — chat UI, wired to /chat/stream (SSE)
// ═══════════════════════════════════════════════════════

const emptyState  = document.getElementById('empty-state');
const chatLog     = document.getElementById('chat-log');
const chatForm    = document.getElementById('chat-form');
const chatInput   = document.getElementById('chat-input');
const sendBtn     = document.getElementById('send-btn');
const resetBtn    = document.getElementById('reset-btn');
const introBar    = document.getElementById('chat-intro-bar');

let streaming = false;

// ── Textarea autosize ──────────────────────────────────
function autosize() {
    chatInput.style.height = 'auto';
    chatInput.style.height = Math.min(chatInput.scrollHeight, 200) + 'px';
}
chatInput.addEventListener('input', autosize);

// Enter to send, Shift+Enter for newline
chatInput.addEventListener('keydown', e => {
    if (e.key === 'Enter' && !e.shiftKey) {
        e.preventDefault();
        chatForm.requestSubmit();
    }
});

// ── Example chips ──────────────────────────────────────
document.querySelectorAll('.example-chip').forEach(btn => {
    btn.addEventListener('click', () => {
        if (streaming) return;
        const q = btn.dataset.question;
        if (!q) return;
        chatInput.value = q;
        autosize();
        chatForm.requestSubmit();
    });
});

// ── Form submit ─────────────────────────────────────────
chatForm.addEventListener('submit', e => {
    e.preventDefault();
    if (streaming) return;
    const text = chatInput.value.trim();
    if (!text) return;
    chatInput.value = '';
    autosize();
    sendMessage(text);
});

// ── Reset ───────────────────────────────────────────────
resetBtn.addEventListener('click', async () => {
    if (streaming) return;
    try {
        await fetch('/reset', { method: 'POST' });
    } catch (err) {
        console.error('Failed to reset conversation:', err);
    }
    chatLog.innerHTML = '';
    chatLog.classList.remove('visible');
    introBar.classList.add('hidden');
    emptyState.classList.remove('hidden');
    chatInput.focus();
});

// ── Message rendering helpers ───────────────────────────
function showChatLog() {
    emptyState.classList.add('hidden');
    chatLog.classList.add('visible');
    introBar.classList.remove('hidden');
}

function appendUserMessage(text) {
    const el = document.createElement('div');
    el.className = 'chat-message chat-message-user';
    el.innerHTML = `
        <div class="chat-sender">You</div>
        <div class="chat-bubble">${escapeHtml(text)}</div>`;
    chatLog.appendChild(el);
    scrollToBottom();
    return el;
}

function appendAssistantMessage() {
    const el = document.createElement('div');
    el.className = 'chat-message chat-message-assistant';
    el.innerHTML = `
        <div class="chat-sender">Archive</div>
        <div class="chat-bubble">
            <div class="chat-status">Thinking…</div>
            <div class="chat-answer" style="display:none;"></div>
            <div class="chat-viz" style="display:none;"></div>
            <div class="chat-sparql" style="display:none;"></div>
        </div>`;
    chatLog.appendChild(el);
    scrollToBottom();
    return el;
}

function setStatus(el, text) {
    const statusEl = el.querySelector('.chat-status');
    if (statusEl) statusEl.textContent = text;
}

function renderFinal(el, { response, sparql_query, viz }) {
    const statusEl  = el.querySelector('.chat-status');
    const answerEl  = el.querySelector('.chat-answer');
    const vizEl     = el.querySelector('.chat-viz');
    const sparqlEl  = el.querySelector('.chat-sparql');

    if (statusEl) statusEl.remove();

    answerEl.style.display = '';
    answerEl.innerHTML = renderMarkdownish(response || 'No response generated.');

    if (viz && viz.is_visual && viz.image) {
        vizEl.style.display = '';
        vizEl.innerHTML = `
            <img class="chat-viz-image" src="${viz.image}" alt="${escapeHtml(viz.title || 'Chart')}">
            ${viz.description ? `<div class="chat-viz-caption">${escapeHtml(viz.description)}</div>` : ''}
        `;
    }

    if (sparql_query) {
        sparqlEl.style.display = '';
        sparqlEl.innerHTML = `
            <details class="sparql-details">
                <summary>View SPARQL query</summary>
                <pre class="sparql-code">${escapeHtml(sparql_query)}</pre>
            </details>`;
    }

    scrollToBottom();
}

function renderError(el, message) {
    const statusEl = el.querySelector('.chat-status');
    const answerEl = el.querySelector('.chat-answer');
    if (statusEl) statusEl.remove();
    answerEl.style.display = '';
    answerEl.classList.add('chat-error');
    answerEl.textContent = message || 'Something went wrong answering that.';
    scrollToBottom();
}

function scrollToBottom() {
    chatLog.scrollTop = chatLog.scrollHeight;
}

function escapeHtml(str) {
    const div = document.createElement('div');
    div.textContent = str;
    return div.innerHTML;
}

// Very small markdown-ish renderer: paragraphs + bold + line breaks.
// Kept intentionally minimal — the assistant's answers are plain prose.
function renderMarkdownish(text) {
    const escaped = escapeHtml(text);
    return escaped
        .split(/\n{2,}/)
        .map(block => `<p>${block.replace(/\n/g, '<br>').replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')}</p>`)
        .join('');
}

function appendAssistantMessageFinal(content, sparql_query, viz) {
    const el = appendAssistantMessage();
    renderFinal(el, { response: content, sparql_query, viz });
    return el;
}

// ── Restore history on load ─────────────────────────────
// The conversation is persisted server-side (LangGraph checkpointer, keyed
// by the thread_id in the session cookie), so it survives navigating to
// /graph and back or reloading the page — we just need to ask for it.
async function restoreHistory() {
    try {
        const resp = await fetch('/history');
        if (!resp.ok) return;
        const data = await resp.json();
        const messages = data.messages || [];
        if (!messages.length) return;

        showChatLog();
        messages.forEach(m => {
            if (m.role === 'user') {
                appendUserMessage(m.content);
            } else if (m.role === 'assistant') {
                appendAssistantMessageFinal(m.content, m.sparql_query, m.viz);
            }
        });
    } catch (err) {
        console.error('Failed to restore chat history:', err);
    }
}

restoreHistory();

// ── Streaming send ──────────────────────────────────────
async function sendMessage(text) {
    showChatLog();
    appendUserMessage(text);
    const assistantEl = appendAssistantMessage();

    streaming = true;
    sendBtn.disabled = true;

    try {
        const resp = await fetch('/chat/stream', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ message: text }),
        });

        if (!resp.ok || !resp.body) {
            throw new Error(`Request failed (${resp.status})`);
        }

        const reader  = resp.body.getReader();
        const decoder = new TextDecoder();
        let buffer = '';

        while (true) {
            const { done, value } = await reader.read();
            if (done) break;
            buffer += decoder.decode(value, { stream: true });

            // SSE events are separated by a blank line
            let sepIndex;
            while ((sepIndex = buffer.indexOf('\n\n')) !== -1) {
                const rawEvent = buffer.slice(0, sepIndex);
                buffer = buffer.slice(sepIndex + 2);
                handleSseEvent(rawEvent, assistantEl);
            }
        }
    } catch (err) {
        console.error('Chat stream failed:', err);
        renderError(assistantEl, "Couldn't reach the archive — please try again.");
    } finally {
        streaming = false;
        sendBtn.disabled = false;
    }
}

function handleSseEvent(rawEvent, assistantEl) {
    let eventName = 'message';
    let dataLines = [];

    rawEvent.split('\n').forEach(line => {
        if (line.startsWith('event:')) {
            eventName = line.slice(6).trim();
        } else if (line.startsWith('data:')) {
            dataLines.push(line.slice(5).trim());
        }
    });

    if (!dataLines.length) return;

    let data;
    try {
        data = JSON.parse(dataLines.join('\n'));
    } catch (err) {
        console.error('Failed to parse SSE data:', err, dataLines);
        return;
    }

    if (eventName === 'status') {
        setStatus(assistantEl, data.text || 'Working…');
    } else if (eventName === 'done') {
        renderFinal(assistantEl, data);
    } else if (eventName === 'error') {
        renderError(assistantEl, data.text);
    }
}