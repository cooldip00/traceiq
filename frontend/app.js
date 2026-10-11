/* ─────────────────────────────────────────────────────────────────────────
   TraceIQ — Frontend Application Logic
   ─────────────────────────────────────────────────────────────────────────*/

const DOC_TYPE_COLORS = {
  LVC: { bg: '#1e1042', color: '#a78bfa', border: '#6d28d9' },
  TIA: { bg: '#0c1a2e', color: '#38bdf8', border: '#0369a1' },
  BRD: { bg: '#1a0a2e', color: '#c084fc', border: '#7c3aed' },
  CDD: { bg: '#0f2027', color: '#34d399', border: '#059669' },
  PRD: { bg: '#1a1500', color: '#fbbf24', border: '#d97706' },
  SDD: { bg: '#1a0d00', color: '#fb923c', border: '#c2410c' },
  ADD: { bg: '#0f1a1a', color: '#2dd4bf', border: '#0f766e' },
  EPIC: { bg: '#1a0a0a', color: '#f87171', border: '#dc2626' },
  USER_STORY: { bg: '#0a1a0e', color: '#4ade80', border: '#16a34a' },
};

// ── STATE ───────────────────────────────────────────────────────────────────
let selectedFiles = [];
let currentJobId = null;
let eventSource = null;
let activeTab = 'conflicts';

// ── DOM REFS ────────────────────────────────────────────────────────────────
const dropZone     = document.getElementById('drop-zone');
const fileInput    = document.getElementById('file-input');
const fileList     = document.getElementById('file-list');
const fileItems    = document.getElementById('file-items');
const fileCount    = document.getElementById('file-count-label');
const runBtn       = document.getElementById('run-btn');
const clearBtn     = document.getElementById('clear-files');
const pipelineIdle = document.getElementById('pipeline-idle');
const reportSection= document.getElementById('report-section');

// ── FILE HANDLING ───────────────────────────────────────────────────────────
dropZone.addEventListener('dragover', (e) => {
  e.preventDefault();
  dropZone.classList.add('drag-over');
});

dropZone.addEventListener('dragleave', () => {
  dropZone.classList.remove('drag-over');
});

dropZone.addEventListener('drop', (e) => {
  e.preventDefault();
  dropZone.classList.remove('drag-over');
  handleFiles([...e.dataTransfer.files]);
});

dropZone.addEventListener('click', (e) => {
  // Don't trigger if clicking the browse label (it handles itself)
  if (e.target.classList.contains('browse-link')) return;
  fileInput.click();
});

// Browse link: stop propagation so dropZone click doesn't also fire
document.querySelector('.browse-link').addEventListener('click', (e) => {
  e.stopPropagation();
  fileInput.click();
});

fileInput.addEventListener('change', () => {
  handleFiles([...fileInput.files]);
});

clearBtn.addEventListener('click', () => {
  selectedFiles = [];
  fileInput.value = '';
  renderFileList();
});

function handleFiles(newFiles) {
  const mdFiles = newFiles.filter(f => f.name.endsWith('.md'));
  // Deduplicate by name
  const existing = new Set(selectedFiles.map(f => f.name));
  for (const f of mdFiles) {
    if (!existing.has(f.name)) {
      selectedFiles.push(f);
      existing.add(f.name);
    }
  }
  renderFileList();
}

function inferDocType(filename) {
  const name = filename.toUpperCase();
  for (const type of Object.keys(DOC_TYPE_COLORS)) {
    if (name.includes(type.replace('_', ''))) return type;
  }
  return 'MD';
}

function renderFileList() {
  if (selectedFiles.length === 0) {
    fileList.style.display = 'none';
    runBtn.disabled = true;
    return;
  }

  fileList.style.display = 'block';
  runBtn.disabled = false;
  fileCount.textContent = `${selectedFiles.length} file${selectedFiles.length !== 1 ? 's' : ''} selected`;

  fileItems.innerHTML = selectedFiles.map((f, i) => {
    const docType = inferDocType(f.name);
    const style = DOC_TYPE_COLORS[docType] || {};
    const tagStyle = style.color
      ? `background:${style.bg};color:${style.color};border:1px solid ${style.border};`
      : '';
    return `
      <li>
        <span class="file-type-tag" style="${tagStyle}">${docType}</span>
        ${escHtml(f.name)}
        <span style="margin-left:auto;color:var(--text-muted);font-size:10px;">${(f.size/1024).toFixed(1)}kb</span>
      </li>
    `;
  }).join('');
}

// ── RUN ANALYSIS ────────────────────────────────────────────────────────────
runBtn.addEventListener('click', async () => {
  if (selectedFiles.length === 0) return;

  // Close any existing SSE
  if (eventSource) { eventSource.close(); eventSource = null; }

  resetPipeline();
  runBtn.disabled = true;
  runBtn.textContent = 'Analyzing…';
  reportSection.style.display = 'none';

  // Upload files
  const formData = new FormData();
  for (const f of selectedFiles) {
    formData.append('files', f);
  }

  let jobId;
  try {
    const resp = await fetch('/analyze', { method: 'POST', body: formData });
    if (!resp.ok) {
      const body = await resp.json().catch(() => ({}));
      throw new Error(body.detail || `Upload failed: ${resp.status}`);
    }
    const data = await resp.json();
    jobId = data.job_id;
    currentJobId = jobId;
  } catch (err) {
    alert('Failed to start analysis: ' + err.message);
    resetRunButton();
    return;
  }

  // Open SSE stream
  pipelineIdle.style.display = 'none';
  eventSource = new EventSource(`/stream/${jobId}`);

  eventSource.onmessage = async (event) => {
    const data = JSON.parse(event.data);

    if (data.type === 'done') {
      eventSource.close();
      if (data.report_ready) {
        await loadReport(jobId);
      } else {
        showFailure('The analysis failed before a report was produced. See the pipeline step marked with ✕ for details.');
      }
      resetRunButton();
      return;
    }

    updateStep(data);
  };

  eventSource.onerror = () => {
    eventSource.close();
    showFailure('Lost connection to the server. Is the backend still running?');
    resetRunButton();
  };
});

function resetRunButton() {
  runBtn.disabled = selectedFiles.length === 0;
  runBtn.innerHTML = `
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
      <polygon points="5 3 19 12 5 21 5 3"/>
    </svg>
    Run Traceability Analysis`;
}

// ── PIPELINE UI ──────────────────────────────────────────────────────────────
function resetPipeline() {
  for (let i = 1; i <= 5; i++) {
    const stepEl = document.getElementById(`step-${i}`);
    const icon = stepEl.querySelector('.step-icon');
    const detail = document.getElementById(`step-${i}-detail`);

    stepEl.className = 'step';
    icon.className = 'step-icon idle';
    icon.innerHTML = `<span class="step-number">${i}</span>`;
    detail.textContent = '';
    detail.classList.remove('visible');
  }
  pipelineIdle.style.display = 'none';
}

function updateStep(event) {
  const { step, status, message, data } = event;
  const stepEl = document.getElementById(`step-${step}`);
  if (!stepEl) return;

  const icon = stepEl.querySelector('.step-icon');
  const detail = document.getElementById(`step-${step}-detail`);

  stepEl.className = `step ${status}`;

  if (status === 'running') {
    icon.className = 'step-icon running';
    icon.innerHTML = `<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5">
      <line x1="12" y1="2" x2="12" y2="6"/><line x1="12" y1="18" x2="12" y2="22"/>
      <line x1="4.93" y1="4.93" x2="7.76" y2="7.76"/><line x1="16.24" y1="16.24" x2="19.07" y2="19.07"/>
      <line x1="2" y1="12" x2="6" y2="12"/><line x1="18" y1="12" x2="22" y2="12"/>
      <line x1="4.93" y1="19.07" x2="7.76" y2="16.24"/><line x1="16.24" y1="7.76" x2="19.07" y2="4.93"/>
    </svg>`;
    // Animate spinner
    icon.style.animation = 'spin 1s linear infinite';

  } else if (status === 'complete') {
    icon.className = 'step-icon complete';
    icon.style.animation = '';
    icon.innerHTML = `<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5">
      <polyline points="20 6 9 17 4 12"/>
    </svg>`;
  } else if (status === 'error') {
    icon.className = 'step-icon error';
    icon.style.animation = '';
    icon.innerHTML = `<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5">
      <line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/>
    </svg>`;
  }

  if (message || (data && Object.keys(data).length > 0)) {
    detail.classList.add('visible');
    let text = message || '';
    if (data && Object.keys(data).length > 0) {
      const dataLines = Object.entries(data)
        .filter(([k]) => k !== 'pair' || status === 'running')
        .map(([k, v]) => `${k}: ${Array.isArray(v) ? v.join(', ') : v}`)
        .join('\n');
      if (dataLines) text += (text ? '\n' : '') + dataLines;
    }
    detail.textContent = text;
  }
}

// ── REPORT ───────────────────────────────────────────────────────────────────
async function loadReport(jobId) {
  try {
    const resp = await fetch(`/report/${jobId}`);
    if (!resp.ok) throw new Error('Report not ready');
    const report = await resp.json();
    renderReport(report);
  } catch (err) {
    console.error('Failed to load report:', err);
    showFailure('The report could not be loaded: ' + err.message);
  }
}

function showFailure(message) {
  reportSection.style.display = 'block';
  document.getElementById('report-stats').innerHTML = '';
  document.getElementById('report-summary').textContent = message;
  document.getElementById('ai-notice').style.display = 'none';
  for (const id of ['report-conflicts', 'report-warnings', 'report-impact']) {
    document.getElementById(id).innerHTML = '';
  }
}

const AI_NOTICES = {
  unavailable: (r) => `⚠️ AI semantic analysis did not run. ${r.pairs_total} traced requirement pair(s) were not checked for contradictions. Start Ollama and run again.`,
  partial: (r) => `⚠️ AI analysis failed for ${r.pairs_failed} of ${r.pairs_total} pair(s). Those pairs were not checked.`,
};

function renderReport(report) {
  reportSection.style.display = 'block';
  reportSection.scrollIntoView({ behavior: 'smooth', block: 'start' });

  // Stats pills
  document.getElementById('report-stats').innerHTML = `
    <div class="stat-pill conflict">🔴 ${report.conflicts.length} Conflicts</div>
    <div class="stat-pill warning">🟡 ${report.warnings.length} Warnings</div>
    <div class="stat-pill ok" title="AI-checked requirement pairs judged consistent">🟢 ${report.ok_count}/${report.pairs_total} Pairs consistent</div>
  `;

  document.getElementById('report-summary').textContent = report.summary;

  const notice = document.getElementById('ai-notice');
  const noticeText = AI_NOTICES[report.ai_status];
  notice.style.display = noticeText ? 'block' : 'none';
  notice.textContent = noticeText ? noticeText(report) : '';

  // Update tab labels
  document.getElementById('tab-conflicts').textContent = `🔴 Conflicts (${report.conflicts.length})`;
  document.getElementById('tab-warnings').textContent  = `🟡 Warnings (${report.warnings.length})`;

  // Render issues
  renderIssues('report-conflicts', report.conflicts, 'conflict');
  renderIssues('report-warnings',  report.warnings,  'warning');
  renderImpact(report.impact_map || {});

  showTab(activeTab);
}

function renderImpact(impactMap) {
  const container = document.getElementById('report-impact');
  const rows = Object.entries(impactMap)
    .sort((a, b) => b[1].total_impacted - a[1].total_impacted || a[0].localeCompare(b[0]));

  if (rows.length === 0) {
    container.innerHTML = '<div class="empty-state">No documents to analyze.</div>';
    return;
  }

  container.innerHTML = `
    <table class="impact-table">
      <thead>
        <tr><th>If this changes…</th><th>Docs to review</th><th>Downstream documents</th><th>Depends on</th></tr>
      </thead>
      <tbody>
        ${rows.map(([docId, imp]) => `
          <tr>
            <td>${escHtml(docId)}</td>
            <td class="impact-count">${imp.total_impacted}</td>
            <td class="impact-docs">${escHtml(imp.downstream_docs.join(', ')) || '—'}</td>
            <td class="impact-docs">${escHtml(imp.upstream_docs.join(', ')) || '—'}</td>
          </tr>`).join('')}
      </tbody>
    </table>`;
}

function renderIssues(containerId, issues, type) {
  const container = document.getElementById(containerId);

  if (issues.length === 0) {
    container.innerHTML = `
      <div class="empty-state">
        ${type === 'conflict'
          ? '✅ No conflicts detected across all document pairs.'
          : '✅ No warnings detected.'}
      </div>`;
    return;
  }

  container.innerHTML = issues.map(issue => {
    const confBar = issue.confidence != null ? `
      <div class="confidence-bar-wrap">
        <span class="confidence-label">AI confidence</span>
        <div class="confidence-bar">
          <div class="confidence-fill" style="width:${Math.round(issue.confidence * 100)}%"></div>
        </div>
        <span class="confidence-label">${Math.round(issue.confidence * 100)}%</span>
      </div>` : '';

    const aiReason = issue.ai_reason ? `
      <div class="issue-ai-reason">${escHtml(issue.ai_reason)}</div>` : '';

    const meta = [
      issue.source_doc && `<span class="issue-meta-tag">📄 ${escHtml(issue.source_doc)}</span>`,
      issue.source_req && `<span class="issue-meta-tag">${escHtml(issue.source_req)}</span>`,
      issue.target_doc && `<span class="issue-meta-tag">→ ${escHtml(issue.target_doc)}</span>`,
      issue.target_req && `<span class="issue-meta-tag">${escHtml(issue.target_req)}</span>`,
      `<span class="issue-meta-tag">${escHtml(issue.severity)}</span>`,
    ].filter(Boolean).join('');

    return `
      <div class="issue-card ${type}">
        <div class="issue-header">
          <div class="issue-title">${escHtml(issue.title)}</div>
          <span class="issue-type-badge ${escHtml(issue.issue_type)}">${escHtml(formatIssueType(issue.issue_type))}</span>
        </div>
        <div class="issue-description">${escHtml(issue.description)}</div>
        ${aiReason}
        ${confBar}
        <div class="issue-meta">${meta}</div>
      </div>
    `;
  }).join('');
}

window.showTab = function(tab) {
  activeTab = tab;
  for (const name of ['conflicts', 'warnings', 'impact']) {
    document.getElementById(`tab-${name}`).classList.toggle('active', tab === name);
    document.getElementById(`report-${name}`).style.display = tab === name ? 'flex' : 'none';
  }
};

// ── HELPERS ──────────────────────────────────────────────────────────────────
function escHtml(value) {
  if (value == null) return '';
  return String(value)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
}

function formatIssueType(type) {
  const map = {
    SEMANTIC_CONFLICT: 'Semantic Conflict',
    PARTIAL_MATCH: 'Partial Match',
    CIRCULAR_TRACE: 'Circular Trace',
    PARSE_ERROR: 'Parse Error',
    ORPHANED_REQUIREMENT: 'Orphaned Req',
    MISSING_ACCEPTANCE_CRITERIA: 'No AC',
    BROKEN_REFERENCE: 'Broken Ref',
    VERSION_MISMATCH: 'Version Mismatch',
    COVERAGE_GAP: 'Coverage Gap',
  };
  return map[type] || type;
}

// Add spin keyframe dynamically
const style = document.createElement('style');
style.textContent = `@keyframes spin { from { transform: rotate(0deg); } to { transform: rotate(360deg); } }`;
document.head.appendChild(style);
