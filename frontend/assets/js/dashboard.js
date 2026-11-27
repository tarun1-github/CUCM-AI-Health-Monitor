let allServices = [];
let gaugeCharts = {};
let trendsChart = null;
let activeTaskId = null;
let pollTimer = null;

function showBanner(msg, isError = false) {
  const el = document.getElementById('statusBanner');
  el.textContent = msg;
  el.style.borderColor = isError ? 'rgba(239,68,68,0.6)' : 'rgba(59,130,246,0.5)';
  el.style.background = isError ? 'rgba(127,29,29,0.45)' : 'rgba(30,58,138,0.35)';
  el.classList.remove('hidden');
}

function setLoading(btnId, loading) {
  const btn = document.getElementById(btnId);
  if (!btn) return;
  btn.disabled = loading;
  btn.classList.toggle('loading', loading);
}

function dotClass(status) {
  const s = (status || 'UNKNOWN').toLowerCase();
  if (s === 'healthy') return 'health-dot dot-healthy';
  if (s === 'warning') return 'health-dot dot-warning';
  if (s === 'critical') return 'health-dot dot-critical';
  return 'health-dot dot-unknown';
}

function badgeClass(status) {
  const s = (status || 'UNKNOWN').toLowerCase();
  if (s === 'healthy') return 'badge badge-healthy';
  if (s === 'warning') return 'badge badge-warning';
  if (s === 'critical') return 'badge badge-critical';
  return 'badge badge-unknown';
}

function formatSectionMeta(sections, key) {
  const sec = sections && sections[key];
  if (!sec || sec.status === 'NOT COLLECTED') return 'Status: NOT COLLECTED';
  const ts = sec.last_successful_at ? new Date(sec.last_successful_at).toLocaleString() : 'Never';
  const err = sec.error ? ` • Error: ${sec.error}` : '';
  return `Status: ${sec.status} (${sec.data_freshness || 'Snapshot'}) • Last OK: ${ts}${err}`;
}

function renderGauge(canvasId, value, status) {
  const ctx = document.getElementById(canvasId);
  if (!ctx) return;
  const numeric = value !== null && value !== undefined ? Math.min(100, Math.max(0, value)) : 0;
  const colorMap = { HEALTHY: '#10b981', WARNING: '#f59e0b', CRITICAL: '#ef4444', UNKNOWN: '#64748b' };
  const color = colorMap[status] || '#64748b';
  if (gaugeCharts[canvasId]) gaugeCharts[canvasId].destroy();
  gaugeCharts[canvasId] = new Chart(ctx, {
    type: 'doughnut',
    data: { datasets: [{ data: [numeric, 100 - numeric], backgroundColor: [color, 'rgba(148,163,184,0.14)'], borderWidth: 0 }] },
    options: { rotation: -90, circumference: 180, cutout: '75%', responsive: true, maintainAspectRatio: false, plugins: { legend: { display: false }, tooltip: { enabled: false } } }
  });
}

function renderSnapshot(snap) {
  if (!snap) return;
  const cls = snap.classification || {};
  const score = cls.score || {};
  const sys = snap.system || {};
  const net = snap.network || {};
  const conn = snap.connectivity || {};
  const cluster = snap.cluster || {};
  const ai = snap.ai_summary || {};
  const sections = snap.sections || {};

  document.getElementById('deviceIp').textContent = snap.device_host || '10.197.206.141';
  document.getElementById('headerVersion').textContent = sys.cucm_version || 'UNKNOWN';
  document.getElementById('lastTimestamp').textContent = new Date(snap.collected_at).toLocaleString();

  // KPI Cards
  document.getElementById('kpiScoreVal').textContent = score.score_label || 'INSUFFICIENT DATA';
  document.getElementById('kpiOverallDot').className = dotClass(cls.overall_health);
  document.getElementById('kpiOverallSub').textContent = `${cls.overall_health || 'UNKNOWN'} • Coverage ${score.data_coverage_pct ?? 0}%`;

  const compPct = score.completion_pct ?? 0;
  const doneSec = Object.values(sections).filter(s => s.status === 'COLLECTED' || s.status === 'PARTIAL').length;
  document.getElementById('kpiCompletionVal').textContent = `${compPct}%`;
  document.getElementById('kpiCompletionDot').className = dotClass(compPct >= 100 ? 'HEALTHY' : compPct > 0 ? 'WARNING' : 'UNKNOWN');
  document.getElementById('kpiCompletionSub').textContent = `${doneSec} / ${Object.keys(sections).length || 9} sections`;

  document.getElementById('kpiCpuVal').textContent = sys.cpu_utilization_pct != null ? `${sys.cpu_utilization_pct.toFixed(1)}%` : '--%';
  document.getElementById('kpiCpuDot').className = dotClass(cls.cpu_health);
  document.getElementById('kpiCpuSub').textContent = cls.cpu_health || 'UNKNOWN';

  document.getElementById('kpiMemVal').textContent = sys.memory_utilization_pct != null ? `${sys.memory_utilization_pct.toFixed(1)}%` : '--%';
  document.getElementById('kpiMemDot').className = dotClass(cls.memory_health);
  document.getElementById('kpiMemSub').textContent = cls.memory_health || 'UNKNOWN';

  document.getElementById('kpiDiskVal').textContent = sys.disk_utilization_pct != null ? `${sys.disk_utilization_pct.toFixed(1)}%` : '--%';
  document.getElementById('kpiDiskDot').className = dotClass(cls.disk_health);
  document.getElementById('kpiDiskSub').textContent = cls.disk_health || 'UNKNOWN';

  allServices = snap.services || [];
  const started = allServices.filter(s => s.status === 'STARTED').length;
  document.getElementById('kpiSvcVal').textContent = allServices.length ? `${started} / ${allServices.length}` : '-- / --';
  document.getElementById('kpiSvcDot').className = dotClass(cls.services_health);
  document.getElementById('kpiSvcSub').textContent = cls.services_health || 'UNKNOWN';

  document.getElementById('kpiNetVal').textContent = cls.network_health || 'NOT COLLECTED';
  document.getElementById('kpiNetDot').className = dotClass(cls.network_health);
  document.getElementById('kpiNetSub').textContent = conn.ssh_connected ? `SSH (${conn.ssh_transport})` : 'Not Connected';

  const nodeCount = (cluster.nodes || []).length;
  document.getElementById('kpiClusterVal').textContent = conn.axl_connected ? `${nodeCount} Node(s)` : 'NOT COLLECTED';
  document.getElementById('kpiClusterDot').className = dotClass(conn.axl_connected ? 'HEALTHY' : 'UNKNOWN');
  document.getElementById('kpiClusterSub').textContent = conn.axl_connected ? `AXL ${conn.axl_version || ''}` : (conn.axl_error || 'AXL unavailable');

  // System Health
  document.getElementById('sysSectionMeta').textContent = formatSectionMeta(sections, 'system');
  renderGauge('cpuGauge', sys.cpu_utilization_pct, cls.cpu_health);
  renderGauge('memGauge', sys.memory_utilization_pct, cls.memory_health);
  renderGauge('diskGauge', sys.disk_utilization_pct, cls.disk_health);
  document.getElementById('cucmVersionPill').textContent = `Version: ${sys.cucm_version || 'UNKNOWN'}`;
  document.getElementById('sysHostname').textContent = sys.hostname || 'UNKNOWN';
  document.getElementById('sysUptime').textContent = sys.uptime || 'UNKNOWN';
  document.getElementById('sysHardware').textContent = sys.hardware_model || 'UNKNOWN';
  document.getElementById('sysLoad').textContent = sys.load_average_1m != null
    ? `${sys.load_average_1m} / ${sys.load_average_5m ?? '-'} / ${sys.load_average_15m ?? '-'}`
    : 'UNKNOWN';

  // Network Health
  document.getElementById('netSectionMeta').textContent = formatSectionMeta(sections, 'network');
  document.getElementById('netHealthBadge').textContent = cls.network_health || 'UNKNOWN';
  document.getElementById('netHealthBadge').className = badgeClass(cls.network_health);
  document.getElementById('netIface').textContent = net.interface_name || 'eth0';
  document.getElementById('netLink').textContent = net.link_status || 'UNKNOWN';
  document.getElementById('netIp').textContent = net.ip_address || 'UNKNOWN';
  document.getElementById('netMask').textContent = net.subnet_mask || 'UNKNOWN';
  document.getElementById('netGw').textContent = net.gateway || 'UNKNOWN';
  document.getElementById('netDns').textContent = net.dns_primary || 'UNKNOWN';
  document.getElementById('netDns2').textContent = net.dns_secondary || 'Not Configured';
  document.getElementById('netConn').textContent =
    `SSH: ${conn.ssh_connected ? conn.ssh_transport : 'OFF'} | AXL: ${conn.axl_connected ? 'OK' : 'OFF'}`;

  const diags = net.diagnostics || [];
  const passed = diags.filter(d => d.result.toLowerCase().includes('pass'));
  const skipped = diags.filter(d => d.result.toLowerCase().includes('off hours') || d.result.toLowerCase().includes('skip'));
  const failed = diags.filter(d => d.result.toLowerCase().includes('fail') || d.result.toLowerCase().includes('error'));
  document.getElementById('netDiagCounts').textContent = `${passed.length} passed, ${skipped.length} skipped, ${failed.length} failed`;
  const diagEl = document.getElementById('netDiagList');
  if (diags.length) {
    diagEl.innerHTML = diags.map(d => {
      const rl = d.result.toLowerCase();
      const clsChip = rl.includes('fail') || rl.includes('error') ? 'badge-critical' : rl.includes('off hours') ? 'badge-unknown' : 'badge-healthy';
      return `<div class="diag-chip"><strong>${d.test}:</strong> <span class="badge ${clsChip}">${d.result}</span></div>`;
    }).join('');
  } else {
    diagEl.innerHTML = '<span class="muted">NOT COLLECTED</span>';
  }

  // Cluster Health
  document.getElementById('clusterSectionMeta').textContent = formatSectionMeta(sections, 'cluster');
  document.getElementById('clusterPubPill').textContent = `Publisher: ${cluster.publisher || 'UNKNOWN'}`;
  const cBody = document.getElementById('clusterTableBody');
  if (cluster.nodes && cluster.nodes.length) {
    cBody.innerHTML = cluster.nodes.map(n => `<tr><td><strong>${n.name}</strong></td><td>${n.role}</td><td><span class="badge badge-healthy">${n.status}</span></td></tr>`).join('');
  } else {
    cBody.innerHTML = '<tr><td colspan="3" class="muted">NOT COLLECTED</td></tr>';
  }

  // AI Summary & Score Reasons
  document.getElementById('aiSectionMeta').textContent = formatSectionMeta(sections, 'ai_summary');
  document.getElementById('aiProviderPill').textContent = `Provider: ${ai.provider || 'deterministic-fallback'}`;
  document.getElementById('aiOverall').textContent = ai.overall_assessment || 'N/A';
  document.getElementById('aiExec').textContent = ai.executive_summary || '';
  document.getElementById('scoreReasons').innerHTML = (score.reasons && score.reasons.length)
    ? score.reasons.map(r => `<li>${r}</li>`).join('')
    : '';
  document.getElementById('aiFindings').innerHTML = (ai.critical_findings && ai.critical_findings.length)
    ? ai.critical_findings.map(f => `<li>${f}</li>`).join('')
    : '<li class="muted">No critical findings.</li>';
  document.getElementById('aiActions').innerHTML = (ai.recommended_actions && ai.recommended_actions.length)
    ? ai.recommended_actions.map(a => `<li>${a}</li>`).join('')
    : '<li class="muted">No actions required.</li>';

  // Services
  document.getElementById('svcSectionMeta').textContent = formatSectionMeta(sections, 'services');
  renderServicesTable(allServices);
  renderCommandsAccordion(snap.commands || []);
}

function renderServicesTable(list) {
  const tbody = document.getElementById('servicesTableBody');
  if (!list.length) {
    tbody.innerHTML = '<tr><td colspan="5" class="muted">NOT COLLECTED</td></tr>';
    return;
  }
  tbody.innerHTML = list.map(s => `
    <tr>
      <td><strong>${s.name}</strong></td>
      <td><span class="badge badge-unknown">${s.criticality || (s.is_critical ? 'CRITICAL' : 'UNKNOWN')}</span></td>
      <td>${s.status}</td>
      <td><span class="${badgeClass(s.health)}">${s.health}</span></td>
      <td class="muted">${s.description || ''}</td>
    </tr>
  `).join('');
}

function filterServices() {
  const q = document.getElementById('svcSearch').value.toLowerCase();
  renderServicesTable(allServices.filter(s => s.name.toLowerCase().includes(q) || s.status.toLowerCase().includes(q)));
}

function renderCommandsAccordion(commands) {
  const container = document.getElementById('commandsAccordion');
  if (!commands.length) {
    container.innerHTML = '<div class="empty-state">No diagnostic commands executed yet.</div>';
    return;
  }
  container.innerHTML = commands.map((c, idx) => {
    const label = c.skipped ? 'SKIPPED' : c.timed_out ? 'TIMEOUT' : c.success ? 'OK' : 'FAIL';
    const bCls = c.skipped ? 'badge badge-unknown' : c.success ? 'badge badge-healthy' : 'badge badge-critical';
    return `
      <div class="acc-item" id="acc-${idx}">
        <button class="acc-head" onclick="document.getElementById('acc-${idx}').classList.toggle('open')">
          <span><code>${c.command}</code></span>
          <span>${c.execution_time_ms} ms • <span class="${bCls}">${label}</span></span>
        </button>
        <div class="acc-body">
          ${c.error ? `<div class="muted">Note: ${c.error}</div>` : ''}
          <pre class="acc-pre">${(c.raw_output || '(no output)').replace(/</g, '&lt;')}</pre>
        </div>
      </div>
    `;
  }).join('');
}

async function loadTrends() {
  const res = await fetch('/api/health/history');
  const history = await res.json();
  const emptyEl = document.getElementById('trendsEmptyState');
  const wrapEl = document.getElementById('trendsChartWrap');
  if (!history || history.length < 1) {
    emptyEl.classList.remove('hidden');
    wrapEl.classList.add('hidden');
    return;
  }
  emptyEl.classList.add('hidden');
  wrapEl.classList.remove('hidden');
  const labels = history.map(h => new Date(h.collected_at).toLocaleTimeString());
  const ctx = document.getElementById('trendsChart');
  if (trendsChart) trendsChart.destroy();
  trendsChart = new Chart(ctx, {
    type: 'line',
    data: {
      labels,
      datasets: [
        { label: 'CPU %', data: history.map(h => h.cpu_pct), borderColor: '#3b82f6', tension: 0.3 },
        { label: 'Memory %', data: history.map(h => h.memory_pct), borderColor: '#8b5cf6', tension: 0.3 },
        { label: 'Disk %', data: history.map(h => h.disk_pct), borderColor: '#f59e0b', tension: 0.3 },
      ]
    },
    options: {
      responsive: true, maintainAspectRatio: false,
      scales: { y: { min: 0, max: 100, ticks: { color: '#94a3b8' } }, x: { ticks: { color: '#94a3b8' } } },
      plugins: { legend: { labels: { color: '#e2e8f0' } } }
    }
  });
}

async function refreshDashboard() {
  setLoading('btnRefresh', true);
  try {
    const cfgRes = await fetch('/api/health/config-summary');
    const cfg = await cfgRes.json();
    document.getElementById('envBadge').textContent = `ENV: ${(cfg.app_env || 'development').toUpperCase()}`;
    document.getElementById('deviceIp').textContent = cfg.cucm_host || '10.197.206.141';

    const latestRes = await fetch('/api/health/latest');
    const snap = await latestRes.json();
    if (snap) renderSnapshot(snap);
    await loadTrends();
    await loadAssistantStatusOnly();
  } catch (err) {
    showBanner(`Failed to refresh dashboard: ${err}`, true);
  } finally {
    setLoading('btnRefresh', false);
  }
}

async function testConnectivity() {
  setLoading('btnConnTest', true);
  showBanner('Testing SSH (Netmiko/Paramiko) and AXL HTTPS connectivity...');
  try {
    const res = await fetch('/api/devices/test-connectivity', { method: 'POST' });
    const data = await res.json();
    const sshTxt = data.ssh.connected ? `SSH OK (${data.ssh.transport})` : `SSH FAIL: ${data.ssh.error}`;
    const axlTxt = data.axl.connected ? `AXL OK (ver=${data.axl.version})` : `AXL FAIL: ${data.axl.error}`;
    showBanner(`Connectivity Test [${data.host}] — ${sshTxt} | ${axlTxt}`, !data.ssh.connected && !data.axl.connected);
  } catch (err) {
    showBanner(`Connectivity test error: ${err}`, true);
  } finally {
    setLoading('btnConnTest', false);
  }
}

async function runPartialHealthCheck(btnId, commandIds, label) {
  setLoading(btnId, true);
  showBanner(`Collecting ${label} (preserving other sections)...`);
  try {
    const res = await fetch('/api/health/run', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ command_ids: commandIds })
    });
    const snap = await res.json();
    renderSnapshot(snap);
    await loadTrends();
    showBanner(`${label} updated (${snap.classification.score.score_label}).`);
  } catch (err) {
    showBanner(`${label} failed: ${err}`, true);
  } finally {
    setLoading(btnId, false);
  }
}

async function startFullHealthCheck() {
  setLoading('btnRunFull', true);
  const panel = document.getElementById('progressPanel');
  panel.classList.remove('hidden');
  document.getElementById('progressFill').style.width = '5%';
  document.getElementById('progressPct').textContent = '5%';
  document.getElementById('progressActivity').textContent = 'Queueing full CUCM health check...';

  try {
    const res = await fetch('/api/health/full', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}' });
    const task = await res.json();
    activeTaskId = task.task_id;
    if (pollTimer) clearInterval(pollTimer);
    pollTimer = setInterval(() => pollTaskStatus(activeTaskId), 1200);
  } catch (err) {
    showBanner(`Failed to start full health check: ${err}`, true);
    setLoading('btnRunFull', false);
  }
}

async function pollTaskStatus(taskId) {
  try {
    const res = await fetch(`/api/health/tasks/${taskId}`);
    if (!res.ok) return;
    const t = await res.json();
    document.getElementById('progressFill').style.width = `${t.progress_percent}%`;
    document.getElementById('progressPct').textContent = `${t.progress_percent}%`;
    document.getElementById('progressActivity').textContent = `${t.current_step}: ${t.message}`;
    document.getElementById('progressSteps').textContent = `Step ${t.completed_steps} of ${t.total_steps} (${t.status})`;

    if (['completed', 'partial', 'failed', 'cancelled'].includes(t.status)) {
      clearInterval(pollTimer);
      pollTimer = null;
      setLoading('btnRunFull', false);
      await refreshDashboard();
      showBanner(`Full health check ${t.status}: ${t.message}`, t.status === 'failed');
    }
  } catch (err) {
    console.error('Poll error:', err);
  }
}

async function cancelActiveTask() {
  if (!activeTaskId) return;
  await fetch(`/api/health/tasks/${activeTaskId}/cancel`, { method: 'POST' });
  showBanner('Cancellation requested for active health check...');
}

async function regenerateAiSummary() {
  setLoading('btnAiSummary', true);
  try {
    const res = await fetch('/api/health/summarize', { method: 'POST' });
    if (!res.ok) throw new Error('Run a health check first.');
    const snap = await res.json();
    renderSnapshot(snap);
    showBanner(`Summary regenerated using ${snap.ai_summary.provider}.`);
  } catch (err) {
    showBanner(err.message, true);
  } finally {
    setLoading('btnAiSummary', false);
  }
}

async function exportReport(fmt) {
  try {
    const res = await fetch(`/api/reports/export/${fmt}`, { method: 'POST' });
    if (!res.ok) {
      const err = await res.json();
      throw new Error(err.detail || 'Export failed');
    }
    const data = await res.json();
    window.open(`/api/reports/download/${data.filename}`, '_blank');
    showBanner(`Exported ${fmt.toUpperCase()} report: ${data.filename}`);
  } catch (err) {
    showBanner(`Export failed: ${err.message}`, true);
  }
}

// FLOATING CUCM AI ASSISTANT + SETTINGS
let currentSessionId = 'default';
let currentAssistantMode = 'auto';

function setAssistantMode(mode) {
  currentAssistantMode = mode;
  document.getElementById('modeBtnAuto').classList.toggle('active', mode === 'auto');
  document.getElementById('modeBtnGeneral').classList.toggle('active', mode === 'general');
  document.getElementById('modeBtnCucm').classList.toggle('active', mode === 'cucm');
}

function openChatWidget() {
  document.getElementById('chatWidget').classList.remove('hidden');
  document.getElementById('chatFab').classList.add('hidden');
  document.getElementById('chatUnreadDot').classList.add('hidden');
  loadAssistantStatusOnly();
}

function minimizeChatWidget() {
  document.getElementById('chatWidget').classList.add('hidden');
  document.getElementById('chatFab').classList.remove('hidden');
}

function closeChatWidget() {
  document.getElementById('chatWidget').classList.add('hidden');
  document.getElementById('chatFab').classList.remove('hidden');
}

function toggleMaximizeChat() {
  const w = document.getElementById('chatWidget');
  w.classList.toggle('maximized');
  document.getElementById('btnMaxRestore').textContent = w.classList.contains('maximized') ? '⤡' : '⤢';
}

function toggleAiSettings() {
  document.getElementById('chatSettingsPanel').classList.toggle('hidden');
}

function renderAssistantStatus(st) {
  const badge = document.getElementById('chatStateBadge');
  const detail = document.getElementById('chatStateDetail');
  const p = st.configured_provider || 'none';
  const m = st.configured_model || 'N/A';

  if (p === 'none') {
    badge.className = 'badge badge-unknown';
    badge.textContent = 'AI DISABLED';
    detail.textContent = 'LLM_PROVIDER=none • Deterministic Python routing active';
  } else if (st.provider_available) {
    badge.className = 'badge badge-healthy';
    badge.textContent = 'LLM ACTIVE';
    detail.textContent = `Provider: ${p} • Model: ${m}`;
  } else if (st.last_error) {
    badge.className = 'badge badge-warning';
    badge.textContent = 'LLM UNAVAILABLE';
    detail.textContent = `${p} (${st.last_error_category || 'Error'}) • Fallback active`;
  } else {
    badge.className = 'badge badge-warning';
    badge.textContent = 'FALLBACK MODE';
    detail.textContent = `Provider: ${p} (not verified) • Python fallback active`;
  }

  document.getElementById('setProvider').textContent = p === 'gateway' ? 'gateway (Gemini → OpenRouter → Ollama)' : p;
  document.getElementById('setModel').textContent = st.last_used_model ? `${st.last_used_provider}: ${st.last_used_model}` : m;
  if (st.primary) document.getElementById('setPrimary').textContent = `${st.primary.model} (${st.primary.configured ? 'Key OK' : 'No Key'})`;
  if (st.secondary) document.getElementById('setSecondary').textContent = `${(st.secondary.models || []).length} models (${st.secondary.configured ? 'Key OK' : 'No Key'})`;
  if (st.final_fallback) document.getElementById('setOllamaEndpoint').textContent = `${st.final_fallback.model} @ ${st.final_fallback.endpoint}`;
  document.getElementById('setAuth').textContent = st.auth_status || 'N/A';
  document.getElementById('setConnStatus').textContent = st.last_connection_test ? st.last_connection_test.status : 'Not tested';
  document.getElementById('setInfStatus').textContent = st.last_inference_test
    ? (st.last_inference_test.success ? 'Success' : `Failed (${st.last_inference_test.error_category || 'Error'})`)
    : 'Not tested';
  document.getElementById('setLatency').textContent = st.last_latency_ms != null ? `${st.last_latency_ms} ms` : '-- ms';
  document.getElementById('setFallback').textContent = st.fallback_enabled ? 'Enabled' : 'Disabled';
  document.getElementById('cfgProviderSelect').value = p;

  const errBox = document.getElementById('setLastError');
  if (st.last_error) {
    errBox.textContent = st.last_error;
    errBox.classList.remove('hidden');
  } else {
    errBox.classList.add('hidden');
  }
}

async function loadAssistantStatusOnly() {
  try {
    const r = await fetch('/api/assistant/status');
    if (r.ok) renderAssistantStatus(await r.json());
  } catch (e) { /* ignore */ }
}

async function refreshAssistantStatus() {
  const r = await fetch('/api/assistant/refresh', { method: 'POST' });
  if (r.ok) renderAssistantStatus(await r.json());
}

async function runProviderConnectionTest() {
  const r = await fetch('/api/assistant/test-connection', { method: 'POST' });
  const res = await r.json();
  await loadAssistantStatusOnly();
  showBanner(`Provider Connection Test (${res.provider}): ${res.status}`, !res.connected);
}

async function runProviderInferenceTest() {
  const r = await fetch('/api/assistant/test-inference', { method: 'POST' });
  const res = await r.json();
  await loadAssistantStatusOnly();
  showBanner(
    res.success ? `Inference Test (${res.provider}/${res.model}) OK in ${res.latency_ms} ms` : `Inference Test Failed: ${res.error}`,
    !res.success
  );
}

async function testSingleProvider(provider) {
  showBanner(`Testing ${provider} connectivity and inference...`);
  const r = await fetch(`/api/assistant/test-provider?provider=${encodeURIComponent(provider)}`, { method: 'POST' });
  const data = await r.json();
  const connOk = data.connection && data.connection.connected;
  const infOk = data.inference && data.inference.success;
  const msg = `${provider.toUpperCase()} — Conn: ${connOk ? 'OK' : data.connection?.status} | Inference: ${infOk ? `OK (${data.inference.model}, ${data.inference.latency_ms} ms)` : data.inference?.error}`;
  showBanner(msg, !infOk);
  await loadAssistantStatusOnly();
}

async function applyProviderConfig() {
  const provider = document.getElementById('cfgProviderSelect').value;
  const model = document.getElementById('cfgModelInput').value.trim();
  if (!confirm(`Confirm changing active LLM provider to '${provider}'${model ? ` (model: ${model})` : ''}?`)) return;
  const r = await fetch('/api/assistant/configure', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ provider, model: model || null, confirmed: true })
  });
  const data = await r.json();
  if (data.status) renderAssistantStatus(data.status);
}

function modeBadge(mode) {
  if (mode === 'GENERAL_AI') return '<span class="badge badge-healthy">[GENERAL AI]</span>';
  if (mode === 'HYBRID') return '<span class="badge badge-healthy">[HYBRID]</span>';
  if (mode === 'LLM_GENERATED') return '<span class="badge badge-healthy">[CUCM OPERATIONS]</span>';
  if (mode === 'CLARIFICATION_REQUIRED') return '<span class="badge badge-warning">[CLARIFICATION]</span>';
  if (mode === 'TOOL_ONLY') return '<span class="badge badge-unknown">[TOOL ONLY]</span>';
  if (mode === 'LLM_FAILED') return '<span class="badge badge-critical">[LLM FAILED]</span>';
  return '<span class="badge badge-warning">[PYTHON FALLBACK]</span>';
}

function appendChatBubble(msg) {
  const box = document.getElementById('chatMessages');
  const div = document.createElement('div');
  div.className = `chat-bubble ${msg.role === 'user' ? 'chat-user' : 'chat-assistant'}`;
  const ts = msg.timestamp ? new Date(msg.timestamp).toLocaleTimeString() : new Date().toLocaleTimeString();

  let tableHtml = '';
  if (msg.table && msg.table.rows && msg.table.rows.length) {
    const cols = msg.table.columns;
    const thead = cols.map(c => `<th>${c}</th>`).join('');
    const tbody = msg.table.rows.map(r => `<tr>${cols.map(c => `<td>${r[c] ?? ''}</td>`).join('')}</tr>`).join('');
    tableHtml = `<div class="chat-table-wrap"><table class="glass-table"><thead><tr>${thead}</tr></thead><tbody>${tbody}</tbody></table></div>`;
  }

  let provHtml = '';
  if (msg.role === 'assistant') {
    const llm = msg.llm || {};
    const tool = msg.tool || {};
    const timing = msg.timing || {};
    const noticeHtml = msg.execution_notice ? `<div class="chat-notice">${msg.execution_notice}</div>` : '';
    provHtml = `
      ${noticeHtml}
      <div class="chat-provenance">
        Mode: ${msg.execution_mode || 'DETERMINISTIC_FALLBACK'} | Provider: ${llm.provider || 'none'} (${llm.model || 'N/A'}) |
        Tool: ${tool.name || 'none'} (${tool.source || 'N/A'}) | Total: ${timing.total_latency_ms ?? 0} ms
      </div>
    `;
  }

  const copyBtn = msg.role === 'assistant'
    ? `<button class="chip-btn" onclick="navigator.clipboard.writeText(this.closest('.chat-bubble').innerText)">Copy</button>` : '';
  const badgeHtml = msg.role === 'assistant' ? modeBadge(msg.execution_mode) : '';
  div.innerHTML = `<div class="chat-meta"><span>${msg.role === 'user' ? 'You' : 'CUCM AI Assistant'} • ${ts} ${badgeHtml}</span>${copyBtn}</div><div>${(msg.content || '').replace(/</g, '&lt;')}</div>${tableHtml}${provHtml}`;
  box.appendChild(div);
  box.scrollTop = box.scrollHeight;
}

function askQuickTool(text) {
  sendChat(text, 'tool_only');
}

async function sendChat(overrideText = null, executionPreference = 'auto') {
  const input = document.getElementById('chatInput');
  const q = (overrideText !== null ? overrideText : input.value).trim();
  if (!q) return;
  if (overrideText === null) input.value = '';
  appendChatBubble({ role: 'user', content: q });

  const stageEl = document.getElementById('chatStageBar');
  stageEl.textContent = executionPreference === 'tool_only'
    ? 'Executing registered CUCM toolkit operation...'
    : 'Routing query → Executing CUCM toolkit → Formatting response...';
  stageEl.classList.remove('hidden');
  setLoading('btnSendChat', true);

  try {
    const res = await fetch('/api/assistant/chat', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        message: q,
        mode: currentAssistantMode,
        conversation_id: currentSessionId,
        execution_preference: executionPreference
      })
    });
    const reply = await res.json();
    appendChatBubble(reply);
    await loadAssistantStatusOnly();
  } catch (err) {
    appendChatBubble({ role: 'assistant', execution_mode: 'LLM_FAILED', content: `Request failed: ${err}` });
  } finally {
    stageEl.classList.add('hidden');
    setLoading('btnSendChat', false);
  }
}

async function startNewChat() {
  const res = await fetch('/api/assistant/new-chat', { method: 'POST' });
  const data = await res.json();
  currentSessionId = data.session_id || 'default';
  document.getElementById('chatSessionLabel').textContent = `Session: ${currentSessionId}`;
  document.getElementById('chatMessages').innerHTML =
    '<div class="chat-bubble chat-assistant"><div class="chat-meta"><span>CUCM AI Assistant • New Chat</span></div><div>Started a new conversation session. Ask a CUCM question.</div></div>';
}

document.addEventListener('DOMContentLoaded', refreshDashboard);
