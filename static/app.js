const state = {
  mode: 'sso',
  authFiles: [],
  jobId: null,
  timer: null,
  canceling: false,
};

const $ = (selector) => document.querySelector(selector);
const $$ = (selector) => [...document.querySelectorAll(selector)];

function setMode(mode) {
  state.mode = mode;
  $$('.source-tab').forEach((button) => button.classList.toggle('active', button.dataset.mode === mode));
  $('#ssoSource').classList.toggle('active', mode === 'sso');
  $('#authSource').classList.toggle('active', mode === 'from_auth');
  const grokInput = $('#targetGrok');
  grokInput.disabled = mode === 'from_auth';
  grokInput.closest('.output-option').style.opacity = mode === 'from_auth' ? '.5' : '1';
  if (mode === 'from_auth') grokInput.checked = false;
  else if (!$('#targetCliproxy').checked) grokInput.checked = true;
  setOptionState(grokInput);
}

function showMessage(message = '') {
  const box = $('#formMessage');
  box.textContent = message;
  box.classList.toggle('visible', Boolean(message));
}

function validLines() {
  return $('#ssoInput').value.split('\n').map((line) => line.trim()).filter((line) => line && !line.startsWith('#'));
}

function updateLineCount() {
  $('#lineCount').textContent = `${validLines().length} 个有效输入`;
}

function renderFiles() {
  const list = $('#fileList');
  if (!state.authFiles.length) {
    list.className = 'file-list empty-file-list';
    list.textContent = '尚未选择文件';
    return;
  }
  list.className = 'file-list';
  list.innerHTML = state.authFiles.map((file) => `<div><span>${escapeHtml(file.name)}</span><span>已读取</span></div>`).join('');
}

function escapeHtml(value) {
  return value.replace(/[&<>'"]/g, (char) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', "'": '&#39;', '"': '&quot;' }[char]));
}

function setOptionState(input) {
  input.closest('.output-option').classList.toggle('selected', input.checked);
}

function readFiles(fileList) {
  return Promise.all([...fileList].map((file) => new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve({ name: file.name, content: String(reader.result) });
    reader.onerror = () => reject(new Error(`无法读取 ${file.name}`));
    reader.readAsText(file);
  })));
}

function buildPayload() {
  return {
    mode: state.mode,
    sso_text: $('#ssoInput').value,
    auth_files: state.authFiles,
    email_override: $('#emailOverride').value.trim(),
    target_cliproxy: $('#targetCliproxy').checked,
    target_grok: $('#targetGrok').checked,
    delay: Number($('#delay').value),
    max_delay: Number($('#maxDelay').value),
    retries: Number($('#retries').value),
    account_retries: Number($('#accountRetries').value),
    concurrency: Number($('#concurrency').value),
  };
}

function validatePayload(payload) {
  if (!payload.target_cliproxy && !payload.target_grok) return '请至少选择一种输出格式';
  if (payload.mode === 'sso' && !validLines().length) return '请先粘贴至少一个 SSO Cookie';
  if (payload.mode === 'from_auth' && !payload.auth_files.length) return '请先选择至少一个 auth JSON 文件';
  if (payload.mode === 'from_auth' && !payload.target_cliproxy) return '已有 auth 文件转换只支持 cliproxyapi 输出';
  return '';
}

async function startJob() {
  showMessage('');
  const payload = buildPayload();
  const error = validatePayload(payload);
  if (error) { showMessage(error); return; }
  const button = $('#startButton');
  button.disabled = true;
  button.innerHTML = '<span class="button-spark">◌</span>正在创建任务<span class="button-arrow">…</span>';
  try {
    const response = await fetch('/api/jobs', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) });
    const data = await response.json();
    if (!response.ok) throw new Error(data.detail || '任务创建失败');
    state.jobId = data.id;
    showMonitor();
    pollJob();
  } catch (error) {
    showMessage(error.message || '网络错误，请确认后端仍在运行');
  } finally {
    button.disabled = false;
    button.innerHTML = '<span class="button-spark">✦</span>开始转换<span class="button-arrow">→</span>';
  }
}

function showMonitor() {
  if (state.timer) window.clearTimeout(state.timer);
  state.timer = null;
  $('#emptyMonitor').classList.add('hidden');
  $('#activeMonitor').classList.remove('hidden');
  $('#downloadArea').classList.add('hidden');
  $('#logLines').innerHTML = '';
  $('#cancelButton').classList.add('hidden');
  $('#cancelButton').disabled = false;
  state.canceling = false;
  $('#jobStatus').textContent = '排队中';
  $('#jobStatus').className = 'status-badge running';
  window.location.hash = 'activity';
}

function updateMonitor(job) {
  const total = job.total || 0;
  const done = (job.success || 0) + (job.failed || 0);
  const percent = total ? Math.min(100, Math.round(done / total * 100)) : 0;
  $('#progressPercent').textContent = `${percent}%`;
  $('#progressCount').textContent = `${done} / ${total}`;
  $('#progressBar').style.width = `${percent}%`;
  $('#progressTitle').textContent = job.status === 'running' ? '正在转换' : job.status === 'cancelling' ? '正在停止' : job.status === 'completed' ? '转换完成' : job.status === 'cancelled' ? '任务已停止' : job.status === 'failed' ? '任务失败' : '正在准备';
  $('#progressSubtitle').textContent = job.error || (job.status === 'running' ? 'Device Flow 与文件生成正在本地执行' : job.status === 'cancelling' ? '正在取消未开始的账号任务' : '');
  $('#currentLabel').textContent = job.current_label || '等待后台任务启动';
  $('#currentState').textContent = job.status === 'running' ? '处理中' : job.status === 'cancelling' ? '停止中' : job.status === 'completed' ? '已完成' : job.status === 'cancelled' ? '已停止' : job.status === 'failed' ? '失败' : '排队中';
  $('#successCount').textContent = job.success || 0;
  $('#failedCount').textContent = job.failed || 0;
  $('#fileCount').textContent = (job.files || []).length;
  $('#logLines').innerHTML = (job.logs || []).map((line) => `<div>${escapeHtml(line)}</div>`).join('');
  $('#logLines').scrollTop = $('#logLines').scrollHeight;
  const status = $('#jobStatus');
  if (job.status === 'completed') { status.textContent = job.failed ? '部分完成' : '已完成'; status.className = 'status-badge done'; }
  if (job.status === 'failed') { status.textContent = '失败'; status.className = 'status-badge failed'; }
  if (job.status === 'running') { status.textContent = '执行中'; status.className = 'status-badge running'; }
  if (job.status === 'cancelling') { status.textContent = '停止中'; status.className = 'status-badge running'; }
  if (job.status === 'cancelled') { status.textContent = '已停止'; status.className = 'status-badge cancelled'; }
  const canCancel = ['queued', 'running', 'cancelling'].includes(job.status);
  $('#cancelButton').classList.toggle('hidden', !canCancel);
  $('#cancelButton').disabled = state.canceling || job.status === 'cancelling';
  $('#cancelButton').textContent = job.status === 'cancelling' ? '停止中…' : '停止任务';
  if (['completed', 'failed', 'cancelled'].includes(job.status)) {
    $('#logState').textContent = 'DONE';
    if (job.files?.length) {
      $('#downloadArea').classList.remove('hidden');
      $('#downloadAll').href = `/api/jobs/${job.id}/download/all.zip`;
      $('#fileLinks').innerHTML = job.files.map((file) => `<a href="${file.download_url}" title="${escapeHtml(file.name)}">${escapeHtml(file.name)}</a>`).join('');
    }
    loadHistory();
  }
}

async function loadHistory() {
  try {
    const response = await fetch('/api/jobs');
    if (!response.ok) return;
    const jobs = await response.json();
    const list = $('#historyList');
    if (!jobs.length) { list.innerHTML = '<div class="history-empty">还没有已完成的任务</div>'; return; }
    list.innerHTML = jobs.map((job) => {
      const stateLabel = job.status === 'completed' ? '已完成' : job.status === 'failed' ? '失败' : job.status === 'cancelled' ? '已停止' : '执行中';
      const result = `${job.success || 0} 成功 · ${job.failed || 0} 失败`;
      return `<div class="history-row"><div><strong>${escapeHtml(job.current_label || `任务 ${job.id}`)}</strong><span>${escapeHtml(job.id)} · ${stateLabel}</span></div><div class="history-result">${result}</div><a class="history-link" href="#activity" data-history-id="${job.id}">查看</a></div>`;
    }).join('');
    $$('.history-link').forEach((link) => link.addEventListener('click', () => { state.jobId = link.dataset.historyId; showMonitor(); pollJob(); }));
  } catch (_) { /* Monitor remains usable if history is unavailable. */ }
}

async function setupElectronUpdates() {
  const button = $('#updateButton');
  const api = window.electronAPI;
  if (!button || !api) return;

  let action = 'check';
  const setButton = (label, nextAction = 'check', disabled = false) => {
    button.textContent = label;
    button.disabled = disabled;
    action = nextAction;
  };

  const version = await api.getVersion();
  $('#appVersion').textContent = `v${version}`;
  setButton(`v${version} · 检查更新`);
  button.addEventListener('click', async () => {
    if (action === 'download') {
      setButton('正在下载…', 'check', true);
      await api.downloadUpdate();
      return;
    }
    if (action === 'install') {
      setButton('正在重启…', 'check', true);
      await api.installUpdate();
      return;
    }
    setButton('检查中…', 'check', true);
    await api.checkForUpdates();
  });

  api.onUpdateStatus((payload) => {
    if (payload.status === 'checking') setButton('检查中…', 'check', true);
    if (payload.status === 'available') setButton(`下载更新 v${payload.version}`, 'download');
    if (payload.status === 'downloading') setButton(`下载中 ${Math.round(payload.percent || 0)}%`, 'check', true);
    if (payload.status === 'downloaded') setButton(`重启安装 v${payload.version}`, 'install');
    if (payload.status === 'not-available') setButton(`v${version} · 已是最新`);
    if (payload.status === 'error') setButton('更新失败 · 重试');
  });
}

async function pollJob() {
  if (!state.jobId) return;
  const jobId = state.jobId;
  try {
    const response = await fetch(`/api/jobs/${jobId}`);
    if (!response.ok) throw new Error('无法读取任务状态');
    const job = await response.json();
    if (state.jobId !== jobId) return;
    updateMonitor(job);
    if (['completed', 'failed', 'cancelled'].includes(job.status)) { state.timer = null; return; }
  } catch (error) {
    $('#logLines').innerHTML += `<div>轮询失败：${escapeHtml(error.message)}</div>`;
  }
  state.timer = window.setTimeout(pollJob, 1200);
}

$$('.source-tab').forEach((button) => button.addEventListener('click', () => setMode(button.dataset.mode)));
$('#ssoInput').addEventListener('input', updateLineCount);
$('#startButton').addEventListener('click', startJob);
$('#refreshHistory').addEventListener('click', loadHistory);
$('#cancelButton').addEventListener('click', async () => {
  if (!state.jobId || state.canceling) return;
  const jobId = state.jobId;
  state.canceling = true;
  $('#cancelButton').disabled = true;
  try {
    const response = await fetch(`/api/jobs/${jobId}/cancel`, { method: 'POST' });
    const data = await response.json();
    if (!response.ok) throw new Error(data.detail || '停止任务失败');
    if (state.jobId === jobId) updateMonitor(data);
  } catch (error) {
    state.canceling = false;
    $('#logLines').innerHTML += `<div>停止失败：${escapeHtml(error.message)}</div>`;
  }
});
$('#authFiles').addEventListener('change', async (event) => {
  try { state.authFiles = await readFiles(event.target.files); renderFiles(); showMessage(''); }
  catch (error) { showMessage(error.message); }
});
$$('.output-option input').forEach((input) => input.addEventListener('change', () => setOptionState(input)));
updateLineCount();
loadHistory();
setupElectronUpdates();
