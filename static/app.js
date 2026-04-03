// ── Config ────────────────────────────────────────────────────────────────────

const AGENTS = {
  claude:   { label: 'Claude',   initials: 'C'  },
  deepseek: { label: 'DeepSeek', initials: 'DS' },
  qwen:     { label: 'Qwen',     initials: 'Q'  },
  system:   { label: 'System',   initials: '⚡'  },
};

// ── State ─────────────────────────────────────────────────────────────────────

let SERVER_HOST = location.host;   // overridden by /config fetch below
let ws            = null;
let reconnTimer   = null;
let activeAgents  = 0;
let activeBubbles = {};
let pendingText   = {};
let renderTimers  = {};

// Project state
let currentMode      = 'chat';    // 'chat' or 'project'
let activeProjectId  = null;
let activeProject    = null;
let projectTasks     = [];        // tasks for current project
let pendingIntentMsg = null;      // original message that triggered intent dialog
let sidebarOpen      = true;
let cachedChatHistory = [];       // last received general chat history — used by switchToChat()
let _fvProjectId     = null;      // currently open project in file viewer

// ── marked.js ─────────────────────────────────────────────────────────────────

marked.setOptions({ breaks: true, gfm: true });

function processCodeBlocks(container) {
  container.querySelectorAll('pre').forEach(pre => {
    if (pre.closest('.code-block-wrap')) return; // already wrapped
    const code = pre.querySelector('code');
    const text = code ? code.textContent : '';
    const firstLine = text.split('\n')[0].trim();
    // Extract filename from FILE: comment or language class
    const fileMatch = firstLine.match(/^(?:\/\/|#|<!--)\s*FILE:\s*(.+?)(?:\s*-->)?$/);
    const langClass = code ? [...code.classList].find(c => c.startsWith('language-')) : null;
    const label = fileMatch
      ? fileMatch[1].trim()
      : (langClass ? langClass.replace('language-', '') : 'code');

    const wrap = document.createElement('div');
    wrap.className = 'code-block-wrap collapsed';

    const hdr = document.createElement('div');
    hdr.className = 'code-block-header';
    hdr.innerHTML = `<span class="code-block-lang">${escHtml(label)}</span><span class="code-block-toggle">&#9654; Show</span>`;
    hdr.onclick = () => {
      wrap.classList.toggle('collapsed');
      hdr.querySelector('.code-block-toggle').innerHTML =
        wrap.classList.contains('collapsed') ? '&#9654; Show' : '&#9660; Hide';
    };

    pre.parentNode.insertBefore(wrap, pre);
    wrap.appendChild(hdr);
    wrap.appendChild(pre);
  });
}

function renderMd(text) {
  const raw  = marked.parse(text || '');
  const html = DOMPurify.sanitize(raw, { USE_PROFILES: { html: true } });
  const div  = document.createElement('div');
  div.innerHTML = html;
  div.querySelectorAll('pre code').forEach(el => hljs.highlightElement(el));
  processCodeBlocks(div);
  return div.innerHTML;
}

// ── Sidebar ───────────────────────────────────────────────────────────────────

function toggleSidebar() {
  sidebarOpen = !sidebarOpen;
  document.getElementById('sidebar').classList.toggle('collapsed', !sidebarOpen);
}

function renderSidebarProjects(projects) {
  const list = document.getElementById('sidebar-list');
  // Keep the "General Chat" item, remove others
  const items = list.querySelectorAll('.sidebar-item:not(.sidebar-chat-item)');
  items.forEach(el => el.remove());

  projects.forEach(p => {
    const item = document.createElement('div');
    item.className = 'sidebar-item' + (activeProjectId === p.id ? ' active' : '');
    item.dataset.projectId = p.id;
    item.onclick = (e) => { if (!e.target.closest('.sidebar-item-del')) loadProject(p.id); };
    item.innerHTML = `
      <span class="sidebar-item-name">${escHtml(p.name)}</span>
      <span class="sidebar-badge ${p.status}">${p.status}</span>
      <button class="sidebar-item-del" title="Delete project" onclick="askDeleteProject(${p.id},'${escHtml(p.name).replace(/'/g,"\\'")}')">&#10005;</button>
    `;
    list.appendChild(item);
  });
}

function updateSidebarActive() {
  document.querySelectorAll('.sidebar-item').forEach(el => el.classList.remove('active'));
  if (currentMode === 'chat') {
    document.getElementById('sidebar-chat').classList.add('active');
  } else if (activeProjectId) {
    const item = document.querySelector(`.sidebar-item[data-project-id="${activeProjectId}"]`);
    if (item) item.classList.add('active');
  }
}

function refreshProjectList() {
  if (ws && ws.readyState === WebSocket.OPEN) {
    ws.send(JSON.stringify({ type: 'get_projects' }));
  }
}

// ── Mode switching ───────────────────────────────────────────────────────────

function switchToChat() {
  currentMode = 'chat';
  activeProjectId = null;
  activeProject = null;
  projectTasks = [];

  document.getElementById('header-title-text').textContent = 'AI Group Chat';
  document.getElementById('task-bar').classList.remove('visible');
  document.getElementById('send-btn-text').textContent = 'Send';
  document.getElementById('mention-hints').style.display = 'flex';

  // Re-render cached chat history without closing the WebSocket
  renderHistory(cachedChatHistory);

  updateSidebarActive();
}

function switchToProject(project, messages, tasks) {
  currentMode = 'project';
  activeProjectId = project.id;
  activeProject = project;
  projectTasks = tasks || [];

  document.getElementById('header-title-text').textContent = project.name;
  document.getElementById('send-btn-text').textContent = 'Add to Project';
  document.getElementById('mention-hints').style.display = 'none';

  // Render task bar
  renderTaskBar(projectTasks);

  // Render project messages
  const container = document.getElementById('messages');
  container.innerHTML = '';
  activeBubbles = {};
  pendingText = {};
  renderTimers  = {};
  activeAgents = 0;
  setSending(false);

  (messages || []).forEach(m => {
    if (m.role === 'user') {
      appendUser(m.content, m.timestamp, false);
    } else if (AGENTS[m.role]) {
      appendAgent(m.role, m.content, m.timestamp, false);
    } else {
      // System/orch messages
      appendOrchStatus(m.content, false);
    }
  });
  scrollBottom();

  updateSidebarActive();
}

function loadProject(projectId) {
  if (ws && ws.readyState === WebSocket.OPEN) {
    ws.send(JSON.stringify({ type: 'load_project', project_id: projectId }));
  }
}

// ── Task bar ─────────────────────────────────────────────────────────────────

function renderTaskBar(tasks) {
  const bar   = document.getElementById('task-bar');
  const chips = document.getElementById('task-chips');
  const resumeBtn = document.getElementById('resume-btn');
  chips.innerHTML = '';

  if (!tasks || tasks.length === 0) {
    bar.classList.remove('visible');
    resumeBtn.style.display = 'none';
    return;
  }

  bar.classList.add('visible');
  tasks.forEach(t => {
    const chip = document.createElement('span');
    chip.className = `task-chip ${t.status}`;
    chip.id = `task-chip-${t.id}`;
    chip.title = `${t.title} (${t.assigned_to})`;

    let icon = '\u00B7';
    if (t.status === 'done') icon = '\u2713';
    else if (t.status === 'in_progress') icon = '<span class="spinner"></span>';

    chip.innerHTML = `${t.task_number} ${icon}`;
    chip.onclick = () => scrollToTaskOutput(t.id);
    chips.appendChild(chip);
  });

  // Show Resume button if any tasks are incomplete
  const hasIncomplete = tasks.some(t => t.status !== 'done');
  resumeBtn.style.display = hasIncomplete ? 'block' : 'none';
}

function resumeOrchestration() {
  if (!activeProjectId || !ws || ws.readyState !== WebSocket.OPEN) return;
  document.getElementById('resume-btn').style.display = 'none';
  appendOrchStatus('Reconnecting and resuming where we left off...');
  setSending(true);
  ws.send(JSON.stringify({ type: 'resume_orchestration', project_id: activeProjectId }));
}

function updateTaskChip(taskId, status) {
  const chip = document.getElementById(`task-chip-${taskId}`);
  if (!chip) return;
  chip.className = `task-chip ${status}`;
  // Update the task in our local array too
  const task = projectTasks.find(t => t.id === taskId);
  if (task) task.status = status;

  let icon = '\u00B7';
  if (status === 'done') icon = '\u2713';
  else if (status === 'in_progress') icon = '<span class="spinner"></span>';

  const num = task ? task.task_number : '';
  chip.innerHTML = `${num} ${icon}`;
}

function scrollToTaskOutput(taskId) {
  const el = document.querySelector(`[data-task-id="${taskId}"]`);
  if (el) el.scrollIntoView({ behavior: 'smooth', block: 'start' });
}

// ── WebSocket ─────────────────────────────────────────────────────────────────

function connect() {
  const proto = location.protocol === 'https:' ? 'wss:' : 'ws:';
  ws = new WebSocket(`${proto}//${location.host}/ws`);

  ws.onopen = () => {
    if (reconnTimer) { clearTimeout(reconnTimer); reconnTimer = null; }
    refreshProjectList();
    syncPillStates();
    // If we were viewing a project when we disconnected, reload it to restore task bar state
    if (activeProjectId) loadProject(activeProjectId);
  };

  ws.onclose = () => {
    // Reset any stuck sending state so UI doesn't freeze
    if (activeAgents > 0) {
      activeAgents = 0;
      Object.keys(activeBubbles).forEach(a => finalise(a));
      setSending(false);
    }
    reconnTimer = setTimeout(connect, 3000);
  };

  ws.onmessage = e => handle(JSON.parse(e.data));
}

function handle(msg) {
  switch (msg.type) {
    // Existing chat messages
    case 'history':     cachedChatHistory = msg.messages; if (currentMode === 'chat') renderHistory(msg.messages); break;
    case 'status':      updateStatus(msg.claude_online);      break;
    case 'user':        appendUser(msg.content, msg.timestamp); break;
    case 'agent_count': activeAgents = msg.count;             break;
    case 'typing':      showTyping(msg.agent);                break;
    case 'chunk':       appendChunk(msg.agent, msg.content);  break;
    case 'done':        finalise(msg.agent);                  break;

    // Project messages
    case 'project_list':
      renderSidebarProjects(msg.projects);
      break;
    case 'project_loaded':
      switchToProject(msg.project, msg.messages, msg.tasks);
      break;
    case 'intent_detected':
      handleIntentDetected(msg);
      break;

    // Orchestration messages
    case 'orch_plan':
      projectTasks = msg.tasks;
      renderTaskBar(msg.tasks);
      appendOrchStatus('Plan created with ' + msg.tasks.length + ' tasks: ' +
        msg.tasks.map(t => t.task_number + '. ' + t.title + ' (' + t.assigned_to + ')').join(', '));
      break;
    case 'orch_phase':
      if (msg.phase === 'planning_tick') {
        // Update the existing planning status bubble in-place rather than appending a new one
        const planEl = document.querySelector('.orch-status-planning .orch-status');
        if (planEl) { planEl.textContent = msg.msg; scrollBottom(); break; }
      }
      { const el = appendOrchStatus(msg.msg);
        if (msg.phase === 'planning' && el) el.classList.add('orch-status-planning'); }
      break;
    case 'orch_task_start':
      updateTaskChip(msg.task_id, 'in_progress');
      appendOrchStatus('Task ' + msg.task_number + ': ' + msg.title + ' (assigned to ' + msg.assigned_to + ')');
      break;
    case 'orch_task_done':
      updateTaskChip(msg.task_id, 'done');
      if (msg.files && msg.files.length > 0) {
        // files already shown individually
      }
      if (msg.error) {
        appendOrchStatus('Task error: ' + msg.error);
      }
      break;
    case 'orch_file':
      appendFileWritten(msg.path);
      break;
    case 'orch_complete':
      appendOrchStatus('✅ Build complete!\n\n' + msg.summary);
      { const slug = msg.slug || (activeProject && activeProject.slug);
        showOpenProjectBtn(slug);
        if (activeProject) activeProject.status = 'completed'; }
      setSending(false);
      refreshProjectList();
      _notifyTabTitle('✅ Build complete!');
      break;

    case 'fix_complete':
      { const slug = msg.project_slug || (activeProject && activeProject.slug);
        const n = msg.files_fixed && msg.files_fixed.length
          ? `✅ Fixed ${msg.files_fixed.length} file(s): ${msg.files_fixed.join(', ')}`
          : '✅ No file changes needed.';
        appendOrchStatus(n);
        if (msg.lesson) appendLessonCard(msg.lesson);
        showOpenProjectBtn(slug);
        setSending(false);
        _notifyTabTitle('✅ Fix complete!'); }
      break;

    case 'orch_stats':
      appendStatsCard(msg);
      break;

    case 'cancelled':
      activeAgents = 0;
      Object.keys(activeBubbles).forEach(a => finalise(a));
      setSending(false);
      { const n = document.createElement('div');
        n.style.cssText = 'color:#8b949e;font-size:12px;padding:4px 8px;align-self:center;';
        n.textContent = '\u23f9 Stopped';
        document.getElementById('messages').appendChild(n);
        scrollBottom(); }
      break;
  }
}

// ── Intent dialog ────────────────────────────────────────────────────────────

function handleIntentDetected(msg) {
  pendingIntentMsg = msg.original_message || '';

  if (msg.intent === 'project_new') {
    showIntentDialog(msg.name || 'New Project');
  } else if (msg.intent === 'project_continue') {
    showContinueDialog();
  }
}

function showIntentDialog(suggestedName) {
  const body = document.getElementById('intent-dialog-body');
  document.getElementById('intent-dialog-title').textContent = 'New project detected';

  body.innerHTML = `
    <div>
      <div class="intent-title">Detected project intent</div>
      <div class="intent-name">"${escHtml(suggestedName)}"</div>
    </div>
    <div class="intent-actions">
      <button class="btn btn-orch" onclick="startNewFromIntent('${escHtml(suggestedName).replace(/'/g, "\\'")}')">Start New Project</button>
      <button class="btn btn-ghost" onclick="showContinueDialog()">Add to existing project</button>
      <button class="btn btn-ghost" onclick="justChatAboutIt()">Just chat about it</button>
    </div>
  `;

  document.getElementById('intent-overlay').classList.add('open');
}

function showContinueDialog() {
  closeIntentDialog();
  const body = document.getElementById('intent-dialog-body');
  document.getElementById('intent-dialog-title').textContent = 'Continue project';

  // Fetch projects and show picker
  fetch('/projects').then(r => r.json()).then(projects => {
    if (projects.length === 0) {
      body.innerHTML = `
        <p style="color:var(--muted); font-size:13px;">No existing projects found.</p>
        <div class="intent-actions">
          <button class="btn btn-orch" onclick="closeIntentDialog(); openProjectCreator()">Create New Project</button>
          <button class="btn btn-ghost" onclick="justChatAboutIt()">Just chat about it</button>
        </div>
      `;
    } else {
      let options = projects.map(p =>
        `<option value="${p.id}">${escHtml(p.name)} (${p.status})</option>`
      ).join('');
      body.innerHTML = `
        <div>
          <div class="intent-title">Select a project</div>
          <select class="project-select" id="continue-project-select" style="width:100%; margin-top:8px;">
            ${options}
          </select>
        </div>
        <div class="intent-actions">
          <button class="btn btn-orch" onclick="continueWithProject()">Continue</button>
          <button class="btn btn-ghost" onclick="justChatAboutIt()">Cancel</button>
        </div>
      `;
    }
    document.getElementById('intent-overlay').classList.add('open');
  });
}

function continueWithProject() {
  const select = document.getElementById('continue-project-select');
  if (!select) return;
  const pid = parseInt(select.value);
  const goal = pendingIntentMsg || '';
  closeIntentDialog();

  if (!goal || !ws || ws.readyState !== WebSocket.OPEN) return;

  // Load project first, then start orchestration once project_loaded confirms
  const onceLoaded = (e) => {
    try {
      const msg = JSON.parse(e.data);
      if (msg.type === 'project_loaded' && msg.project && msg.project.id === pid) {
        ws.removeEventListener('message', onceLoaded);
        ws.send(JSON.stringify({ type: 'start_orchestration', project_id: pid, goal }));
        setSending(true);
      }
    } catch {}
  };
  ws.addEventListener('message', onceLoaded);
  // Timeout fallback: if project_loaded never arrives, remove listener
  setTimeout(() => ws.removeEventListener('message', onceLoaded), 5000);
  loadProject(pid);
}

function startNewFromIntent(name) {
  closeIntentDialog();
  // Only pre-fill goal if the message looks like a real spec (>30 chars or contains action verbs)
  const isRealSpec = pendingIntentMsg && (
    pendingIntentMsg.length > 30 ||
    /\b(build|create|make|add|implement|develop|write|design)\b/i.test(pendingIntentMsg)
  );
  document.getElementById('new-project-name').value = name;
  document.getElementById('new-project-desc').value = '';
  document.getElementById('new-project-goal').value = isRealSpec ? pendingIntentMsg : '';
  document.getElementById('new-project-goal-field').style.display = 'block';
  document.getElementById('project-creator-overlay').classList.add('open');
}

function justChatAboutIt() {
  closeIntentDialog();
  // Send as normal chat
  if (pendingIntentMsg && ws && ws.readyState === WebSocket.OPEN) {
    ws.send(JSON.stringify({ content: '@claude ' + pendingIntentMsg }));
    setSending(true);
  }
  pendingIntentMsg = null;
}

function closeIntentDialog() {
  document.getElementById('intent-overlay').classList.remove('open');
}

// ── Project creator ──────────────────────────────────────────────────────────

function openProjectCreator(prefillName, prefillGoal) {
  document.getElementById('new-project-name').value = prefillName || '';
  document.getElementById('new-project-desc').value = '';
  document.getElementById('new-project-goal').value = prefillGoal || '';
  if (prefillGoal) {
    document.getElementById('new-project-goal-field').style.display = 'block';
  } else {
    document.getElementById('new-project-goal-field').style.display = 'none';
    // Clear any leftover pending intent so it doesn't auto-start orchestration
    pendingIntentMsg = null;
  }
  document.getElementById('project-creator-overlay').classList.add('open');
}

function closeProjectCreator() {
  document.getElementById('project-creator-overlay').classList.remove('open');
}

// ── Delete project ────────────────────────────────────────────────────────────

let _deleteProjectId = null;

function askDeleteProject(id, name) {
  _deleteProjectId = id;
  document.getElementById('del-msg').textContent =
    `"${name}" and all its files will be permanently deleted. This cannot be undone.`;
  document.getElementById('del-overlay').classList.add('open');
}

function closeDeleteDialog() {
  document.getElementById('del-overlay').classList.remove('open');
  _deleteProjectId = null;
}

async function confirmDelete() {
  if (!_deleteProjectId) return;
  const id = _deleteProjectId;
  closeDeleteDialog();
  try {
    const res = await fetch(`/projects/${id}`, { method: 'DELETE' });
    const data = await res.json();
    if (data.ok) {
      // If currently viewing this project, switch back to chat
      if (activeProjectId === id) switchToChat();
      refreshProjectList();
    }
  } catch (e) {
    console.error('Delete failed', e);
  }
}

async function createAndStartProject() {
  const name = document.getElementById('new-project-name').value.trim();
  const desc = document.getElementById('new-project-desc').value.trim();
  const goal = document.getElementById('new-project-goal').value.trim() || pendingIntentMsg || '';

  if (!name) return;

  const btn = document.getElementById('create-project-btn');
  btn.disabled = true;
  btn.textContent = 'Creating...';

  try {
    const res = await fetch('/projects', {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ name, description: desc }),
    });
    const project = await res.json();

    if (project.error) {
      btn.disabled = false;
      btn.textContent = 'Create & Start';
      return;
    }

    closeProjectCreator();
    refreshProjectList();
    pendingIntentMsg = null;

    // Load the new project
    loadProject(project.id);

    // Start orchestration only if an explicit goal/spec was typed in the goal field
    const explicitGoal = document.getElementById('new-project-goal').value.trim();
    if (explicitGoal) {
      setTimeout(() => {
        if (ws && ws.readyState === WebSocket.OPEN) {
          ws.send(JSON.stringify({
            type: 'start_orchestration',
            project_id: project.id,
            goal: explicitGoal,
          }));
          setSending(true);
        }
      }, 600);
    } else {
      // No goal yet — prompt user to describe requirements
      setTimeout(() => {
        const hint = document.createElement('div');
        hint.style.cssText = 'color:var(--muted);font-size:13px;padding:8px 12px;align-self:center;text-align:center;';
        hint.textContent = 'Project created. Describe what you want to build and I\'ll get started.';
        const msgs = document.getElementById('messages');
        if (msgs) { msgs.appendChild(hint); msgs.scrollTop = msgs.scrollHeight; }
      }, 700);
    }
  } catch (e) {
    console.error('Failed to create project', e);
  }

  btn.disabled = false;
  btn.textContent = 'Create & Start';
}

// ── Orchestration UI helpers ─────────────────────────────────────────────────

function appendOrchStatus(text, scroll = true) {
  const c = document.getElementById('messages');
  const row = document.createElement('div');
  row.className = 'msg-row ai';
  row.innerHTML = `
    <div class="avatar orch">O</div>
    <div class="bubble-wrap">
      <div class="agent-name orch">ORCHESTRATOR</div>
      <div class="orch-status">${renderMd(text)}</div>
    </div>`;
  c.appendChild(row);
  if (scroll) scrollBottom();
  return row;
}

function appendStatsCard(s) {
  const c = document.getElementById('messages');
  if (!c) return;

  const byAgent = s.by_agent || {};
  const agents  = Object.keys(byAgent);
  const totalTok = agents.reduce((a, k) => a + (byAgent[k].input_tokens||0) + (byAgent[k].output_tokens||0), 0);
  const claudeIn = s.claude_input || 0;
  const claudeOut = s.claude_output || 0;
  const claudeTot = claudeIn + claudeOut;
  const localTok  = s.local_tokens || 0;
  const costUsd   = s.cost_usd || 0;
  const totalTasks = s.total_tasks || 0;

  // Agent bar colors
  const colors = { claude: '#f59e0b', deepseek: '#60a5fa', qwen: '#a78bfa' };

  // Build rows
  let rows = '';
  agents.forEach(agent => {
    const d = byAgent[agent];
    const tot = (d.input_tokens||0) + (d.output_tokens||0);
    const pct = totalTasks > 0 ? Math.round((d.tasks / totalTasks) * 100) : 0;
    const tokLabel = agent === 'claude' ? `${tot.toLocaleString()} tokens` : `~${tot.toLocaleString()} tokens`;
    rows += `<tr>
      <td><span style="color:${colors[agent]||'#ccc'}">${agent.charAt(0).toUpperCase()+agent.slice(1)}</span></td>
      <td>${d.tasks} task${d.tasks!==1?'s':''} (${pct}%)</td>
      <td>${tokLabel}</td>
    </tr>`;
  });

  // Bar segments
  let bars = '';
  agents.forEach(agent => {
    const d = byAgent[agent];
    const tot = (d.input_tokens||0) + (d.output_tokens||0);
    const w = totalTok > 0 ? Math.round((tot / totalTok) * 100) : 0;
    if (w > 0) bars += `<div class="stats-bar-seg" style="width:${w}%;background:${colors[agent]||'#888'}"></div>`;
  });

  const savingsLine = localTok > 0
    ? `~${localTok.toLocaleString()} tokens handled locally — saved ~$${(localTok * 9 / 1_000_000).toFixed(3)} vs all-Claude`
    : '';

  const div = document.createElement('div');
  div.className = 'stats-card';
  div.innerHTML = `
    <div class="stats-title">📊 Build Stats &nbsp;<span style="font-weight:400;color:var(--muted)">⏱ ${s.elapsed||'–'}</span></div>
    <table>${rows}</table>
    <div class="stats-bar">${bars}</div>
    ${claudeTot > 0 ? `<div style="margin-top:6px;font-size:11px;">Claude API: ${claudeIn.toLocaleString()} in + ${claudeOut.toLocaleString()} out = $${costUsd}</div>` : ''}
    ${savingsLine ? `<div class="stats-savings">💰 ${savingsLine}</div>` : ''}
  `;
  c.appendChild(div);
  c.scrollTop = c.scrollHeight;
}

function appendLessonCard(lesson) {
  if (!lesson) return;
  const c = document.getElementById('messages');
  if (!c) return;
  const div = document.createElement('div');
  div.className = 'lesson-card';
  div.innerHTML = `<span class="lesson-icon">💡</span><span><strong>Lesson saved:</strong> ${escHtml(lesson)}</span>`;
  c.appendChild(div);
  c.scrollTop = c.scrollHeight;
}

function showOpenProjectBtn(slug) {
  if (!slug) return;
  const url = 'http://' + SERVER_HOST + '/play/' + slug + '/';
  const d = document.createElement('div');
  d.style.cssText = 'margin:12px 0 4px;display:flex;gap:10px;align-items:center;flex-wrap:wrap;';
  d.innerHTML = '<a href="' + url + '" target="_blank" '
    + 'style="display:inline-block;padding:10px 24px;background:#22c55e;color:#fff;'
    + 'text-decoration:none;border-radius:8px;font-weight:bold;font-size:15px;">'
    + '&#9654; Open Project</a>'
    + '<button onclick="openFilesModal(' + (activeProjectId || 'null') + ')" '
    + 'style="padding:10px 20px;background:#3b3060;color:#c4b5fd;border:1px solid #6d28d9;'
    + 'border-radius:8px;font-size:15px;cursor:pointer;">&#128193; Files</button>';
  const c = document.getElementById('messages');
  if (c) { c.appendChild(d); c.scrollTop = c.scrollHeight; }
}

function appendFileWritten(path, scroll = true) {
  const c = document.getElementById('messages');
  const el = document.createElement('div');
  el.className = 'file-written';
  el.innerHTML = `<span>&#128196;</span> <span>${escHtml(path)} written</span>`;
  c.appendChild(el);
  if (scroll) scrollBottom();
}

// ── Status ────────────────────────────────────────────────────────────────────

let prevOnline = null;

function updateStatus(online) {
  const dot = document.getElementById('dot-claude');
  dot.className = 'dot ' + (online ? 'online' : 'offline');

  if (prevOnline !== null && prevOnline !== online) {
    const msg = online
      ? 'Claude is back online'
      : 'Claude is offline -- Qwen will handle planning';
    document.getElementById('toast-text').textContent = msg;
    const toast = document.getElementById('toast');
    toast.classList.add('show');
    setTimeout(() => toast.classList.remove('show'), 4000);
  }
  prevOnline = online;
}

// ── History ───────────────────────────────────────────────────────────────────

function renderHistory(messages) {
  activeBubbles = {};
  pendingText   = {};
  renderTimers  = {};
  activeAgents  = 0;
  setSending(false);

  document.getElementById('messages').innerHTML = '';
  messages.forEach(m => {
    if (m.role === 'user')        appendUser(m.content, m.timestamp, false);
    else if (AGENTS[m.role])      appendAgent(m.role, m.content, m.timestamp, false);
  });
  scrollBottom();
}

// ── Helpers ───────────────────────────────────────────────────────────────────

function fmtTime(iso) {
  if (!iso) return '';
  const d = new Date(iso.endsWith('Z') ? iso : iso + 'Z');
  return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
}

function escHtml(s) {
  return s.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
}

function scrollBottom() {
  const c = document.getElementById('messages');
  c.scrollTop = c.scrollHeight;
}

// ── Append messages ───────────────────────────────────────────────────────────

function appendUser(content, ts, scroll = true) {
  const c = document.getElementById('messages');
  const row = document.createElement('div');
  row.className = 'msg-row user';
  row.innerHTML = `
    <div class="avatar user">P</div>
    <div class="bubble-wrap">
      <div class="bubble user">${escHtml(content).replace(/\n/g,'<br>')}</div>
      <div class="bubble-time">${fmtTime(ts)}</div>
    </div>`;
  c.appendChild(row);
  if (scroll) scrollBottom();
}

function appendAgent(agent, content, ts, scroll = true) {
  removeTyping(agent);
  const info = AGENTS[agent];
  const c    = document.getElementById('messages');
  const row  = document.createElement('div');
  row.className = 'msg-row ai';
  row.innerHTML = `
    <div class="avatar ${agent}">${info.initials}</div>
    <div class="bubble-wrap">
      <div class="agent-name ${agent}">${info.label}</div>
      <div class="bubble ${agent}">${renderMd(content)}</div>
      <div class="bubble-time">${fmtTime(ts)}</div>
    </div>`;
  c.appendChild(row);
  if (scroll) scrollBottom();
}

// ── Typing indicator ──────────────────────────────────────────────────────────

function showTyping(agent) {
  removeTyping(agent);
  const info = AGENTS[agent];
  if (!info) return;
  const c    = document.getElementById('messages');
  const el   = document.createElement('div');
  el.className = 'msg-row ai';
  el.id = `typing-${agent}`;
  el.innerHTML = `
    <div class="avatar ${agent}">${info.initials}</div>
    <div class="typing-bubble ${agent}">
      <span>${info.label}</span>
      <div class="dots"><span></span><span></span><span></span></div>
    </div>`;
  c.appendChild(el);
  scrollBottom();
}

function removeTyping(agent) {
  if (agent) {
    document.getElementById(`typing-${agent}`)?.remove();
  } else {
    document.querySelectorAll('[id^="typing-"]').forEach(e => e.remove());
  }
}

// ── Streaming ─────────────────────────────────────────────────────────────────

function appendChunk(agent, chunk) {
  removeTyping(agent);

  if (!activeBubbles[agent]) {
    const info = AGENTS[agent];
    if (!info) return;
    const c    = document.getElementById('messages');
    const row  = document.createElement('div');
    row.className = 'msg-row ai';
    row.id = `stream-${agent}`;
    row.innerHTML = `
      <div class="avatar ${agent}">${info.initials}</div>
      <div class="bubble-wrap">
        <div class="agent-name ${agent}">${info.label}</div>
        <div class="bubble ${agent}"></div>
      </div>`;
    c.appendChild(row);
    activeBubbles[agent] = row.querySelector('.bubble');
    pendingText[agent]   = '';
  }

  pendingText[agent] += chunk;
  // Debounce markdown rendering to reduce DOM thrashing during fast streaming
  clearTimeout(renderTimers[agent]);
  renderTimers[agent] = setTimeout(() => {
    if (activeBubbles[agent]) {
      activeBubbles[agent].innerHTML = renderMd(pendingText[agent]);
      scrollBottom();
    }
  }, 100);
}

function finalise(agent) {
  const row = document.getElementById(`stream-${agent}`);
  if (row) {
    const wrap = row.querySelector('.bubble-wrap');
    if (wrap) {
      const t = document.createElement('div');
      t.className   = 'bubble-time';
      t.textContent = fmtTime(new Date().toISOString());
      wrap.appendChild(t);
    }
    row.removeAttribute('id');
  }

  // Guarantee a final full render regardless of debounce timer state
  clearTimeout(renderTimers[agent]);
  delete renderTimers[agent];
  if (activeBubbles[agent] && pendingText[agent]) {
    activeBubbles[agent].innerHTML = renderMd(pendingText[agent]);
  }
  delete activeBubbles[agent];
  delete pendingText[agent];
  removeTyping(agent);
  scrollBottom();

  activeAgents = Math.max(0, activeAgents - 1);
  if (activeAgents === 0) setSending(false);
}

// ── Sending ───────────────────────────────────────────────────────────────────

function sendMessage() {
  const input = document.getElementById('input');
  const text  = input.value.trim();
  if (!text || !ws || ws.readyState !== WebSocket.OPEN) return;

  setSending(true);

  // Always send as a content message; server handles routing.
  // Include project_id when in project mode so server can do smart routing
  // (project query vs build vs plain chat) instead of always triggering orchestration.
  const msg = { content: text };
  if (currentMode === 'project' && activeProjectId) {
    msg.project_id = activeProjectId;
  }
  ws.send(JSON.stringify(msg));

  input.value = '';
  input.style.height = 'auto';
}

function cancelGeneration() {
  if (ws && ws.readyState === WebSocket.OPEN) {
    ws.send(JSON.stringify({ type: 'cancel' }));
  }
}

function setSending(on) {
  document.getElementById('send-btn').disabled = on;
  const btn = document.getElementById('cancel-btn');
  if (on) btn.classList.add('visible');
  else    btn.classList.remove('visible');
}

// ── Tab notification (title flash when tab is hidden) ─────────────────────
let _titleFlashTimer = null;
const _originalTitle = document.title;

function _notifyTabTitle(message) {
  if (!document.hidden) return;   // tab is visible — no need to flash
  let visible = true;
  clearInterval(_titleFlashTimer);
  _titleFlashTimer = setInterval(() => {
    document.title = visible ? message : _originalTitle;
    visible = !visible;
  }, 1000);
  // Stop flashing once the user returns to the tab
  document.addEventListener('visibilitychange', function _stopFlash() {
    if (!document.hidden) {
      clearInterval(_titleFlashTimer);
      _titleFlashTimer = null;
      document.title = _originalTitle;
      document.removeEventListener('visibilitychange', _stopFlash);
    }
  }, {once: false});
}

function insertMention(m) {
  const input = document.getElementById('input');
  const pos   = input.selectionStart;
  const val   = input.value;
  const before = val.slice(0, pos);
  const after  = val.slice(pos);
  const sep    = (before && !before.endsWith(' ')) ? ' ' : '';
  input.value  = before + sep + m + ' ' + after;
  input.focus();
  const np = pos + sep.length + m.length + 1;
  input.setSelectionRange(np, np);
  autoResize(input);
}

function autoResize(el) {
  el.style.height = 'auto';
  el.style.height = Math.min(el.scrollHeight, 130) + 'px';
}

// ── Event listeners ───────────────────────────────────────────────────────────

document.getElementById('input').addEventListener('keydown', e => {
  if (e.key === 'Enter' && !e.shiftKey) {
    e.preventDefault();
    sendMessage();
  }
});

document.getElementById('input').addEventListener('input', function () {
  autoResize(this);
});

// ESC closes any open dialog
document.addEventListener('keydown', e => {
  if (e.key === 'Escape') {
    closeIntentDialog();
    closeProjectCreator();
  }
});

// ── Modal ─────────────────────────────────────────────────────────────────────

let currentModalAgent = null;
let settingsCache = null;

async function loadSettings() {
  if (!settingsCache) settingsCache = await fetch('/settings').then(r => r.json());
  return settingsCache;
}

function invalidateSettings() { settingsCache = null; }

async function openModal(agent) {
  currentModalAgent = agent;
  const overlay = document.getElementById('modal-overlay');
  const settings = await loadSettings();

  if (agent === 'plus') {
    renderPlusModal();
  } else if (agent === 'claude') {
    renderClaudeModal(settings.claude);
  } else {
    renderLocalModal(agent, settings[agent]);
  }

  overlay.classList.add('open');
  document.addEventListener('keydown', onModalEsc);
}

function closeModal() {
  document.getElementById('modal-overlay').classList.remove('open');
  document.getElementById('modal').classList.remove('modal--wide');
  document.removeEventListener('keydown', onModalEsc);
  currentModalAgent = null;
  _fvProjectId = null;
}

function handleOverlayClick(e) {
  if (e.target === document.getElementById('modal-overlay')) closeModal();
}

function onModalEsc(e) { if (e.key === 'Escape') closeModal(); }

// ── Claude modal ──────────────────────────────────────────────────────────────

function renderClaudeModal(cfg) {
  document.getElementById('modal-avatar').className = 'avatar claude';
  document.getElementById('modal-avatar').textContent = 'C';
  document.getElementById('modal-agent-name').textContent = 'Claude';
  document.getElementById('modal-agent-type').textContent = 'Cloud \u00B7 Anthropic API';

  const preview = cfg.api_key_set ? cfg.api_key_preview : '';
  const isEnabled = cfg.enabled !== false;

  document.getElementById('modal-body').innerHTML = `
    <div class="agent-toggle-row">
      <span class="field-label" style="margin:0">Use in tasks</span>
      <label class="toggle-switch">
        <input type="checkbox" id="agent-enabled-toggle" ${isEnabled ? 'checked' : ''}
               onchange="setAgentEnabled('claude', this.checked)">
        <span class="toggle-track"><span class="toggle-thumb"></span></span>
      </label>
    </div>
    <div>
      <div class="field-label">Anthropic API Key</div>
      <div class="key-wrap">
        <input type="password" id="apikey-input"
               placeholder="${cfg.api_key_set ? preview : 'sk-ant-...'}"
               value="${cfg.api_key_set ? '\u2022\u2022\u2022\u2022\u2022\u2022\u2022\u2022\u2022\u2022\u2022\u2022' : ''}" />
        <button class="icon-btn" onclick="toggleKeyVis()" title="Show/hide">&#128065;</button>
      </div>
    </div>
    <div id="key-msg" class="msg-status"></div>
  `;

  document.getElementById('modal-footer').innerHTML = `
    <button class="btn btn-ghost" onclick="testClaude()">Test Connection</button>
    <button class="btn btn-claude" onclick="saveApiKey()">Save Key</button>
  `;

  const input = document.getElementById('apikey-input');
  input.addEventListener('focus', () => {
    if (input.value === '\u2022\u2022\u2022\u2022\u2022\u2022\u2022\u2022\u2022\u2022\u2022\u2022') input.value = '';
  });
}

function toggleKeyVis() {
  const input = document.getElementById('apikey-input');
  input.type = input.type === 'password' ? 'text' : 'password';
}

async function saveApiKey() {
  const key = document.getElementById('apikey-input').value.trim();
  if (!key || key === '\u2022\u2022\u2022\u2022\u2022\u2022\u2022\u2022\u2022\u2022\u2022\u2022') { showKeyMsg('error', 'Enter a valid key'); return; }

  const btn = document.querySelector('.btn-claude');
  btn.disabled = true; btn.textContent = 'Saving...';

  const res = await fetch('/settings/apikey', {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ key }),
  }).then(r => r.json());

  btn.disabled = false; btn.textContent = 'Save Key';
  invalidateSettings();

  if (res.ok) {
    showKeyMsg('ok', 'Connected -- Claude is online');
    updateStatus(true);
  } else {
    showKeyMsg('error', res.error || 'Failed to connect');
  }
}

async function testClaude() {
  const btn = document.querySelector('.btn-ghost');
  btn.disabled = true; btn.textContent = 'Testing...';
  const res = await fetch('/settings/test/claude').then(r => r.json());
  btn.disabled = false; btn.textContent = 'Test Connection';
  showKeyMsg(res.online ? 'ok' : 'error',
    res.online ? 'Claude is reachable' : 'Cannot reach Claude -- check key or network');
}

function showKeyMsg(type, text) {
  const el = document.getElementById('key-msg');
  el.className = `msg-status ${type}`;
  el.textContent = text;
}

// ── Local model modal ─────────────────────────────────────────────────────────

function renderLocalModal(agent, cfg) {
  const info = AGENTS[agent];
  document.getElementById('modal-avatar').className = `avatar ${agent}`;
  document.getElementById('modal-avatar').textContent = info.initials;
  document.getElementById('modal-agent-name').textContent = info.label;
  document.getElementById('modal-agent-type').textContent = 'Local \u00B7 Mac Mini \u00B7 Ollama';

  const isEnabled = cfg.enabled !== false;

  document.getElementById('modal-body').innerHTML = `
    <div class="agent-toggle-row">
      <span class="field-label" style="margin:0">Use in tasks</span>
      <label class="toggle-switch">
        <input type="checkbox" id="agent-enabled-toggle" ${isEnabled ? 'checked' : ''}
               onchange="setAgentEnabled('${agent}', this.checked)">
        <span class="toggle-track"><span class="toggle-thumb"></span></span>
      </label>
    </div>
    <div class="info-grid">
      <div class="info-row"><span>Model</span><span title="${cfg.model}">${cfg.model.length > 34 ? cfg.model.slice(0,34)+'\u2026' : cfg.model}</span></div>
      <div class="info-row"><span>Size</span><span>${cfg.size}</span></div>
      <div class="info-row"><span>Quantization</span><span>${cfg.quant}</span></div>
      <div class="info-row"><span>Keep Alive</span><span>${cfg.keep_alive} after last use</span></div>
    </div>
    <div id="local-msg" class="msg-status"></div>
  `;

  document.getElementById('modal-footer').innerHTML = `
    <button class="btn btn-${agent}" onclick="testLocal('${agent}')">Test Connection</button>
  `;
}

async function testLocal(agent) {
  const btn = document.querySelector(`.btn-${agent}`);
  btn.disabled = true; btn.textContent = 'Testing...';
  const res = await fetch(`/settings/test/${agent}`).then(r => r.json());
  btn.disabled = false; btn.textContent = 'Test Connection';
  const el = document.getElementById('local-msg');
  el.className = `msg-status ${res.online ? 'ok' : 'error'}`;
  el.textContent = res.online
    ? `${AGENTS[agent].label} is loaded and ready`
    : 'Cannot reach model -- is Ollama running?';
}

async function setAgentEnabled(agent, enabled) {
  await fetch('/settings/agent', {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ agent, enabled }),
  });
  invalidateSettings();
  applyPillState(agent, enabled);
  // Special: if disabling Claude, mirror to master model toggle
  if (agent === 'claude') updateStatus(enabled && prevOnline);
}

function applyPillState(agent, enabled) {
  const pill = document.getElementById(`pill-${agent}`);
  const dot  = document.getElementById(`dot-${agent}`);
  if (!pill) return;
  if (enabled) {
    pill.classList.remove('disabled');
    // restore dot — for local agents show online, for claude keep current status dot
    if (agent !== 'claude') dot.className = 'dot online';
  } else {
    pill.classList.add('disabled');
    dot.className = 'dot disabled';
  }
}

async function syncPillStates() {
  const settings = await fetch('/settings').then(r => r.json());
  ['claude', 'deepseek', 'qwen'].forEach(a => {
    applyPillState(a, settings[a]?.enabled !== false);
  });
}

// ── Plus modal ────────────────────────────────────────────────────────────────

async function renderPlusModal() {
  const modal = document.getElementById('modal');
  modal.classList.add('modal--wide');
  const av = document.getElementById('modal-avatar');
  av.className = 'avatar user';
  av.textContent = '+';
  av.style.background = '#1e3a5f';
  document.getElementById('modal-agent-name').textContent = 'Local Models';
  document.getElementById('modal-agent-type').textContent = 'Register Ollama models as chat agents';
  document.getElementById('modal-body').innerHTML = '<div class="fv-loading">Loading available models\u2026</div>';
  document.getElementById('modal-footer').innerHTML =
    '<button class="btn btn-ghost" onclick="closeModal()">Close</button>';

  let models;
  try {
    const r = await fetch('/ollama/models');
    if (!r.ok) throw new Error(r.statusText);
    models = await r.json();
  } catch (e) {
    document.getElementById('modal-body').innerHTML =
      '<div class="fv-loading">Failed to load models. Is Ollama running?</div>';
    return;
  }
  _renderModelRegistry(models);
}

function _renderModelRegistry(models) {
  // Registered models first, then alphabetical
  models.sort((a, b) => {
    const ra = a.registered_as ? 0 : 1, rb = b.registered_as ? 0 : 1;
    if (ra !== rb) return ra - rb;
    return a.name.localeCompare(b.name);
  });

  let html = '<div class="mr-list">';
  for (const m of models) {
    const slug   = _mrSlug(m.name);
    const offline = m.offline ? ' <span class="mr-offline">offline</span>' : '';
    const size    = m.size_gb != null ? `<span class="mr-size">${m.size_gb}\u202fGB</span>` : '';
    html += `<div class="mr-row" id="mr-row-${slug}">`;
    html += `<div class="mr-info"><span class="mr-name">${escHtml(m.name)}</span>${size}${offline}`;
    if (m.registered_as) {
      const cls = m.builtin ? 'mr-badge-builtin' : 'mr-badge-custom';
      html += ` <span class="mr-badge ${cls}">${escHtml(m.registered_as)}</span>`;
    }
    html += '</div><div class="mr-actions">';
    if (m.registered_as && !m.builtin) {
      html += `<button class="btn btn-sm btn-danger" onclick="_mrRemove('${escHtml(m.registered_as)}',this)">Remove</button>`;
    } else if (!m.registered_as) {
      html += `<button class="btn btn-sm btn-ghost" id="mr-reg-${slug}" onclick="_mrShowForm(this,'${escHtml(m.name)}')">Register</button>`;
    }
    html += '</div></div>';
    html += `<div class="mr-form" id="mr-form-${slug}" style="display:none"></div>`;
  }
  if (!models.length) html += '<div class="fv-loading">No models found in Ollama.</div>';
  html += '</div>';
  document.getElementById('modal-body').innerHTML = html;
}

function _mrSlug(name) { return name.replace(/[^a-zA-Z0-9]/g, '_'); }

function _mrShowForm(btn, modelName) {
  // Close any open form first
  document.querySelectorAll('.mr-form').forEach(f => { f.style.display = 'none'; f.innerHTML = ''; });
  document.querySelectorAll('[id^="mr-reg-"]').forEach(b => { b.style.display = ''; });

  const slug    = _mrSlug(modelName);
  const formEl  = document.getElementById(`mr-form-${slug}`);
  if (!formEl) return;

  const suggested = modelName.split(':')[0].replace(/[^a-z0-9_-]/gi, '').toLowerCase();
  const labelDef  = suggested.charAt(0).toUpperCase() + suggested.slice(1);

  formEl.innerHTML = `
    <div class="mr-form-inner">
      <label>Agent key <span class="mr-hint">(short id used in chat, e.g. "llama3")</span></label>
      <input id="mr-inp-key" class="mr-input" type="text" value="${escHtml(suggested)}" placeholder="agent-key" maxlength="32">
      <label>Display name</label>
      <input id="mr-inp-label" class="mr-input" type="text" value="${escHtml(labelDef)}" placeholder="Display Name" maxlength="32">
      <div class="mr-form-btns">
        <button class="btn btn-sm btn-primary" onclick="_mrConfirm('${escHtml(modelName)}',this)">Add Agent</button>
        <button class="btn btn-sm btn-ghost" onclick="_mrCancel('${escHtml(slug)}')">Cancel</button>
      </div>
      <div class="mr-form-err" id="mr-err-${slug}"></div>
    </div>`;
  formEl.style.display = 'block';
  btn.style.display = 'none';
  document.getElementById('mr-inp-key').focus();
}

function _mrCancel(slug) {
  const f = document.getElementById(`mr-form-${slug}`);
  if (f) { f.style.display = 'none'; f.innerHTML = ''; }
  const b = document.getElementById(`mr-reg-${slug}`);
  if (b) b.style.display = '';
}

async function _mrConfirm(modelName, btn) {
  const key   = (document.getElementById('mr-inp-key')?.value   || '').trim().toLowerCase();
  const label = (document.getElementById('mr-inp-label')?.value || '').trim();
  const slug  = _mrSlug(modelName);
  const errEl = document.getElementById(`mr-err-${slug}`);
  if (!key) { if (errEl) errEl.textContent = 'Key is required'; return; }
  if (!/^[a-z0-9_-]+$/.test(key)) { if (errEl) errEl.textContent = 'Key must be a-z, 0-9, - or _ only'; return; }
  btn.disabled = true; btn.textContent = 'Adding\u2026';
  try {
    const r = await fetch('/ollama/models', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ key, model: modelName, label: label || key }),
    });
    const data = await r.json();
    if (!r.ok || data.error) {
      if (errEl) errEl.textContent = data.error || 'Request failed';
      btn.disabled = false; btn.textContent = 'Add Agent'; return;
    }
    const models = await (await fetch('/ollama/models')).json();
    _renderModelRegistry(models);
  } catch (e) {
    if (errEl) errEl.textContent = 'Network error';
    btn.disabled = false; btn.textContent = 'Add Agent';
  }
}

async function _mrRemove(agentKey, btn) {
  if (!confirm(`Remove agent "${agentKey}"? This cannot be undone.`)) return;
  btn.disabled = true; btn.textContent = 'Removing\u2026';
  try {
    const r = await fetch(`/ollama/models/${encodeURIComponent(agentKey)}`, { method: 'DELETE' });
    const data = await r.json();
    if (!r.ok || data.error) { alert(data.error || 'Failed to remove agent'); btn.disabled = false; btn.textContent = 'Remove'; return; }
    const models = await (await fetch('/ollama/models')).json();
    _renderModelRegistry(models);
  } catch (e) {
    alert('Network error'); btn.disabled = false; btn.textContent = 'Remove';
  }
}

// ── System stats ──────────────────────────────────────────────────────────────

function fmtSpeed(kbs) {
  if (kbs >= 1024) return (kbs / 1024).toFixed(1) + ' MB/s';
  return kbs.toFixed(1) + ' KB/s';
}

async function fetchStats() {
  try {
    const s = await fetch('/stats').then(r => r.json());

    const cpuChip = document.getElementById('stat-cpu');
    cpuChip.className = 'stat-chip' +
      (s.cpu >= 80 ? ' cpu-high' : s.cpu >= 50 ? ' cpu-medium' : '');
    document.getElementById('val-cpu').textContent = s.cpu + '%';

    document.getElementById('val-ram').textContent =
      s.ram_used + '/' + s.ram_total + ' GB';

    document.getElementById('val-swap').textContent =
      s.swap_total > 0
        ? s.swap_used + '/' + s.swap_total + ' GB'
        : 'none';

    document.getElementById('val-net').textContent =
      '\u2193' + fmtSpeed(s.rx_kbs) + ' \u2191' + fmtSpeed(s.tx_kbs);

    // Update Ollama agent status dots
    const ollamaState = s.ollama_online ? 'online' : 'offline';
    ['deepseek', 'qwen'].forEach(agent => {
      const dot = document.getElementById('dot-' + agent);
      if (dot) dot.className = 'dot ' + ollamaState;
    });

  } catch (_) { /* server unreachable */ }
}

fetchStats();
setInterval(fetchStats, 10000);

// ── Offline Detection ─────────────────────────────────────────────────────────

window.addEventListener('offline', () => {
  document.getElementById('offline-banner').classList.add('show');
});
window.addEventListener('online', () => {
  document.getElementById('offline-banner').classList.remove('show');
  if (!ws || ws.readyState !== WebSocket.OPEN) connect();
});

// ── File Viewer ───────────────────────────────────────────────────────────────

async function openFilesModal(projectId) {
  if (!projectId) return;
  _fvProjectId = projectId;

  // Reuse existing modal overlay — set wide mode
  const overlay = document.getElementById('modal-overlay');
  const modal   = document.getElementById('modal');
  modal.classList.add('modal--wide');
  document.getElementById('modal-avatar').textContent = '📁';
  document.getElementById('modal-avatar').style.background = '#3b3060';
  document.getElementById('modal-agent-name').textContent = 'Project Files';
  document.getElementById('modal-agent-type').textContent = 'Read-only viewer';
  document.getElementById('modal-body').innerHTML = '<div class="fv-loading">Loading file tree…</div>';
  document.getElementById('modal-footer').innerHTML =
    '<button class="btn" onclick="closeModal()">Close</button>';
  overlay.classList.add('open');
  document.addEventListener('keydown', onModalEsc);

  let files;
  try {
    const r = await fetch(`/projects/${projectId}/files`);
    files = await r.json();
  } catch (e) {
    document.getElementById('modal-body').innerHTML = '<div class="fv-loading">Failed to load files.</div>';
    return;
  }
  if (!Array.isArray(files) || files.length === 0) {
    document.getElementById('modal-body').innerHTML = '<div class="fv-loading">No files found.</div>';
    return;
  }

  _renderFileViewer(files);
}

function _fmtSize(bytes) {
  if (bytes < 1024) return bytes + ' B';
  if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + ' KB';
  return (bytes / 1024 / 1024).toFixed(1) + ' MB';
}

function _fileIcon(path) {
  const ext = path.split('.').pop().toLowerCase();
  const icons = { html: '🌐', css: '🎨', js: '⚡', json: '📋', md: '📝', py: '🐍',
                  png: '🖼', jpg: '🖼', jpeg: '🖼', gif: '🖼', svg: '🖼', webp: '🖼' };
  return icons[ext] || '📄';
}

function _renderFileViewer(files) {
  const body = document.getElementById('modal-body');
  body.innerHTML = `
    <div class="fv-wrapper">
      <div class="fv-tree" id="fv-tree"></div>
      <div class="fv-pane" id="fv-pane">
        <div class="fv-placeholder">Select a file to view its contents</div>
      </div>
    </div>`;

  const tree = document.getElementById('fv-tree');
  files.forEach(f => {
    const row = document.createElement('div');
    row.className = 'fv-file';
    row.title = f.path;
    row.innerHTML = `<span class="fv-icon">${_fileIcon(f.path)}</span>`
      + `<span class="fv-name">${escHtml(f.path)}</span>`
      + `<span class="fv-size">${_fmtSize(f.size)}</span>`;
    row.onclick = () => _loadFileContent(f.path, row);
    tree.appendChild(row);
  });
}

async function _loadFileContent(filePath, rowEl) {
  // Highlight selected row
  document.querySelectorAll('.fv-file').forEach(r => r.classList.remove('active'));
  if (rowEl) rowEl.classList.add('active');

  const pane = document.getElementById('fv-pane');
  pane.innerHTML = '<div class="fv-placeholder">Loading…</div>';

  let data;
  try {
    const r = await fetch(`/projects/${_fvProjectId}/files/${encodeURIComponent(filePath)}`);
    data = await r.json();
  } catch (e) {
    pane.innerHTML = '<div class="fv-placeholder">Failed to load file.</div>';
    return;
  }

  const ext = filePath.split('.').pop().toLowerCase();
  const imageExts = ['png', 'jpg', 'jpeg', 'gif', 'svg', 'webp'];
  if (imageExts.includes(ext)) {
    const url = `/play/${encodeURIComponent(filePath)}`;
    pane.innerHTML = `<div class="fv-img-wrap"><img src="${url}" alt="${escHtml(filePath)}" style="max-width:100%;max-height:60vh;border-radius:6px;"></div>`;
    return;
  }

  const escaped = escHtml(data.content || '');
  pane.innerHTML = `
    <div class="fv-file-header">
      <span>${_fileIcon(filePath)} ${escHtml(filePath)}</span>
      <button class="fv-copy-btn" onclick="_copyFileContent(this)">Copy</button>
    </div>
    <pre class="fv-code"><code>${escaped}</code></pre>`;
}

function _copyFileContent(btn) {
  const code = btn.closest('.fv-pane').querySelector('.fv-code');
  if (!code) return;
  navigator.clipboard.writeText(code.textContent).then(() => {
    btn.textContent = 'Copied!';
    setTimeout(() => btn.textContent = 'Copy', 1500);
  });
}

// ── Skills Panel ──────────────────────────────────────────────────────────────

let _skillsList = [];
let _activeSkillFile = null;

async function openSkillsModal() {
  const overlay = document.getElementById('modal-overlay');
  const modal   = document.getElementById('modal');
  modal.classList.add('modal--wide');
  document.getElementById('modal-avatar').textContent = '⚡';
  document.getElementById('modal-avatar').style.background = '#1e3a5f';
  document.getElementById('modal-agent-name').textContent = 'Skills';
  document.getElementById('modal-agent-type').textContent = 'Knowledge base for worker agents';
  document.getElementById('modal-body').innerHTML = '<div class="fv-loading">Loading skills…</div>';
  document.getElementById('modal-footer').innerHTML =
    '<button class="btn" onclick="closeModal()">Close</button>';
  overlay.classList.add('open');
  document.addEventListener('keydown', onModalEsc);

  try {
    const r = await fetch('/skills');
    _skillsList = await r.json();
  } catch (e) {
    document.getElementById('modal-body').innerHTML = '<div class="fv-loading">Failed to load skills.</div>';
    return;
  }
  if (!Array.isArray(_skillsList) || _skillsList.length === 0) {
    document.getElementById('modal-body').innerHTML = '<div class="fv-loading">No skills found.</div>';
    return;
  }
  _renderSkillsPanel();
}

function _renderSkillsPanel() {
  const body = document.getElementById('modal-body');
  body.innerHTML = `
    <div class="fv-wrapper">
      <div class="fv-tree" id="sk-tree"></div>
      <div class="fv-pane" id="sk-pane">
        <div class="fv-placeholder">Select a skill to view or edit</div>
      </div>
    </div>`;

  const tree = document.getElementById('sk-tree');
  _skillsList.forEach(sk => {
    const row = document.createElement('div');
    row.className = 'fv-file';
    row.dataset.file = sk.file;
    row.title = sk.description || sk.name;
    row.innerHTML =
      '<span class="fv-icon">⚡</span>' +
      '<span class="fv-name">' + escHtml(sk.name) + '</span>' +
      '<span class="fv-size">' + sk.keywords.length + ' kw</span>';
    row.onclick = () => _loadSkillContent(sk);
    tree.appendChild(row);
  });
}

async function _loadSkillContent(sk) {
  _activeSkillFile = sk.file;
  document.querySelectorAll('#sk-tree .fv-file').forEach(r => r.classList.remove('active'));
  const activeRow = document.querySelector(`#sk-tree .fv-file[data-file="${sk.file}"]`);
  if (activeRow) activeRow.classList.add('active');

  const pane = document.getElementById('sk-pane');
  pane.innerHTML = '<div class="fv-placeholder">Loading…</div>';

  let data;
  try {
    const r = await fetch('/skills/' + encodeURIComponent(sk.file));
    data = await r.json();
  } catch (e) {
    pane.innerHTML = '<div class="fv-placeholder">Failed to load skill.</div>';
    return;
  }

  pane.innerHTML = `
    <div class="fv-file-header">
      <span>⚡ ${escHtml(sk.name)}</span>
      <span class="sk-kw">${sk.keywords.map(k => '<code>' + escHtml(k) + '</code>').join(' ')}</span>
    </div>
    <div class="sk-desc">${escHtml(sk.description)}</div>
    <textarea class="sk-editor" id="sk-editor" spellcheck="false">${escHtml(data.content || '')}</textarea>
    <div class="sk-actions">
      <span class="sk-save-status" id="sk-save-status"></span>
      <button class="btn btn-ghost" onclick="_saveSkill()">Save</button>
    </div>`;
}

async function _saveSkill() {
  if (!_activeSkillFile) return;
  const editor = document.getElementById('sk-editor');
  const status = document.getElementById('sk-save-status');
  if (!editor) return;

  status.textContent = 'Saving…';
  status.className = 'sk-save-status';
  try {
    const r = await fetch('/skills/' + encodeURIComponent(_activeSkillFile), {
      method: 'PUT',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ content: editor.value }),
    });
    const res = await r.json();
    if (res.ok) {
      status.textContent = '✓ Saved';
      status.className = 'sk-save-status saved';
      // Refresh the skills list to pick up any name/keyword changes
      const r2 = await fetch('/skills');
      _skillsList = await r2.json();
    } else {
      status.textContent = '✗ ' + (res.error || 'Failed');
      status.className = 'sk-save-status error';
    }
  } catch (e) {
    status.textContent = '✗ Network error';
    status.className = 'sk-save-status error';
  }
}

// ── Boot ──────────────────────────────────────────────────────────────────────

// Fetch server config (dynamic host) then connect
fetch('/config').then(r => r.json()).then(cfg => {
  if (cfg.server_host) SERVER_HOST = cfg.server_host;
}).catch(() => {}).finally(() => connect());
