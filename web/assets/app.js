const state = {
  incidents: [],
  selected: null,
  lastResult: null,
  health: null,
  apiKey: window.sessionStorage.getItem("remediApiKey") || "",
};

const listEl = document.getElementById("incident-list");
const runBtn = document.getElementById("run-btn");
const applyBtn = document.getElementById("apply-btn");
const selftestBtn = document.getElementById("selftest-btn");
const statusEl = document.getElementById("status");
const outputEl = document.getElementById("output");
const artifactShellEl = document.getElementById("artifact-shell");
const previewEl = document.getElementById("artifact-preview");
const artifactNameEl = document.getElementById("artifact-name");

function apiFetch(resource, options = {}) {
  const headers = new Headers(options.headers || {});
  if (state.apiKey) headers.set("X-API-Key", state.apiKey);
  return fetch(resource, { ...options, headers });
}

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function setStatus(text, cls = "idle") {
  statusEl.textContent = text;
  statusEl.className = `status ${cls}`;
}

function selectedIncident() {
  return state.incidents.find((incident) => incident.id === state.selected);
}

function renderSelection() {
  const incident = selectedIncident();
  document.getElementById("selected-title").textContent =
    incident?.title || "No active incident";
  document.getElementById("selected-risk").textContent =
    incident?.risk_score != null ? `${incident.priority} · ${incident.risk_score}` : "—";
}

function renderIncidents() {
  listEl.innerHTML = "";
  document.getElementById("queue-count").textContent = String(state.incidents.length);

  if (!state.incidents.length) {
    const empty = document.createElement("li");
    empty.className = "queue-empty";
    empty.innerHTML = state.health?.mode === "live"
      ? "<strong>No failing DataHub assertions.</strong><span>Remedi only opens incidents from assertion runs whose latest result is FAILURE.</span>"
      : "<strong>No offline incidents.</strong><span>Restore the verification catalog and reload.</span>";
    listEl.appendChild(empty);
    runBtn.disabled = true;
    applyBtn.disabled = true;
  }

  for (const incident of state.incidents) {
    const li = document.createElement("li");
    const btn = document.createElement("button");
    const resolved = incident.status === "resolved";
    const priority = String(incident.priority || "P3").toLowerCase();
    btn.type = "button";
    btn.dataset.incidentId = incident.id;
    btn.className = state.selected === incident.id ? "active" : "";
    btn.setAttribute("aria-pressed", String(state.selected === incident.id));
    btn.innerHTML = `
      <span class="inc-title">${escapeHtml(incident.title)}</span>
      <span class="inc-meta">
        <span class="pill ${escapeHtml(incident.severity)}">${escapeHtml(incident.severity)}</span>
        ${
          incident.risk_score != null
            ? `<span class="pill priority-${escapeHtml(priority)}">${escapeHtml(incident.priority)} · ${escapeHtml(incident.risk_score)}</span>`
            : ""
        }
        <span class="pill">${escapeHtml(incident.type)}</span>
        <span class="pill ${resolved ? "ok" : ""}">${resolved ? "resolved" : "open"}</span>
      </span>
      ${
        incident.recommended_action
          ? `<span class="inc-action">${escapeHtml(incident.recommended_action)}</span>`
          : ""
      }
      ${
        incident.risk_reasons?.length
          ? `<span class="inc-evidence">${incident.risk_reasons.map(escapeHtml).join(" · ")}</span>`
          : ""
      }
    `;
    btn.addEventListener("click", () => {
      state.selected = incident.id;
      state.lastResult = null;
      applyBtn.disabled = true;
      applyBtn.innerHTML = '<span class="button-index">02</span>Apply sealed plan';
      artifactShellEl.hidden = true;
      renderIncidents();
      renderSelection();
      setStatus(
        incident.recommended_action
          ? `${incident.priority} · risk ${incident.risk_score} — ${incident.recommended_action}`
          : `Selected ${incident.id}. Propose a grounded fix to continue.`,
        "idle"
      );
    });
    li.appendChild(btn);
    listEl.appendChild(li);
  }

  renderSelection();
}

async function showArtifact(incidentId, relativePath) {
  const safePath = relativePath.split("/").map(encodeURIComponent).join("/");
  artifactShellEl.hidden = false;
  artifactNameEl.textContent = relativePath;
  previewEl.textContent = "Loading artifact…";
  const res = await apiFetch(`/api/artifacts/${encodeURIComponent(incidentId)}/${safePath}`);
  if (!res.ok) {
    previewEl.textContent = `Artifact unavailable (${res.status}).`;
    return;
  }
  previewEl.textContent = await res.text();
  artifactShellEl.scrollIntoView({ behavior: "smooth", block: "nearest" });
}

function relativeArtifactPath(fullPath, incidentId) {
  const normalized = String(fullPath || "");
  const marker = `${incidentId}/`;
  const idx = normalized.indexOf(marker);
  if (idx >= 0) return normalized.slice(idx + marker.length);
  return normalized.split("/").slice(-2).join("/");
}

function renderLineageGraph(result) {
  const svg = document.getElementById("lineage-graph");
  const sourceName = result.incident.entity.name.split(".").pop();
  const nodes = [{ id: "src", name: sourceName, type: "source", x: 65, y: 112 }];
  const downstream = [
    ...new Map(
      (result.blast_radius.downstream || []).map((item) => [item.entity.urn, item])
    ).values(),
  ];
  const byHop = {};

  for (const item of downstream) {
    (byHop[item.hop] ||= []).push(item);
  }

  const hops = Object.keys(byHop).map(Number).sort((a, b) => a - b);
  hops.forEach((hop, hopIndex) => {
    const group = byHop[hop].slice(0, 3);
    const x = 205 + hopIndex * (380 / Math.max(hops.length - 1, 1));
    group.forEach((item, itemIndex) => {
      const spacing = group.length > 1 ? 150 / (group.length - 1) : 0;
      nodes.push({
        id: item.entity.urn,
        name: item.entity.name.split(".").pop(),
        type: item.entity.type,
        x: Math.min(x, 590),
        y: group.length > 1 ? 38 + itemIndex * spacing : 112,
      });
    });
  });

  let edges = (result.blast_radius.edges || []).map((edge) => ({
    from: edge.from === result.blast_radius.source_urn ? "src" : edge.from,
    to: edge.to,
  }));
  if (!edges.length) {
    edges = downstream.map((item) => ({ from: "src", to: item.entity.urn }));
  }

  const nodeMap = Object.fromEntries(nodes.map((node) => [node.id, node]));
  const visibleEdges = edges.filter((edge) => nodeMap[edge.from] && nodeMap[edge.to]);
  const displayedTargets = new Set(visibleEdges.map((edge) => edge.to));
  for (const node of nodes.slice(1)) {
    if (!displayedTargets.has(node.id)) visibleEdges.push({ from: "src", to: node.id });
  }
  const lines = visibleEdges
    .map((edge) => {
      const from = nodeMap[edge.from];
      const to = nodeMap[edge.to];
      return `<line x1="${from.x + 18}" y1="${from.y}" x2="${to.x - 18}" y2="${to.y}" class="edge" />`;
    })
    .join("");

  const circles = nodes
    .map((node) => {
      const className =
        node.type === "mlModel"
          ? "ml"
          : node.type === "dashboard"
            ? "dash"
            : node.type === "source"
              ? "src"
              : "ds";
      return `
        <g transform="translate(${node.x},${node.y})">
          <circle r="15" class="node ${className}" />
          <text text-anchor="middle" dy="34" class="node-label">${escapeHtml(node.name.slice(0, 19))}</text>
        </g>
      `;
    })
    .join("");

  svg.innerHTML = lines + circles;
}

function renderTimeline(steps) {
  document.getElementById("timeline").innerHTML = (steps || [])
    .map(
      (step) => `
        <li>
          <strong>${escapeHtml(step.name)}</strong>
          <span class="muted">${escapeHtml(step.detail)}</span>
          <span class="ms">${escapeHtml(step.duration_ms ?? 0)}ms</span>
        </li>
      `
    )
    .join("");
}

function renderResult(result) {
  outputEl.hidden = false;
  outputEl.classList.remove("selftest-output");

  const artifacts = result.plan.artifacts || [];
  const tools = result.tools_used || [];
  const digest = result.proposal_digest || "";
  const applied = !result.pending_write_back;
  document.getElementById("metric-impact").textContent = String(
    result.blast_radius.total_impacted
  );
  document.getElementById("metric-artifacts").textContent = String(artifacts.length);
  document.getElementById("metric-tools").textContent = String(tools.length);
  document.getElementById("receipt-state").textContent = applied
    ? "Applied · integrity verified"
    : result.proposal_integrity === "sealed"
      ? "Sealed · awaiting approval"
      : "Legacy proposal";
  document.getElementById("receipt-digest").textContent = digest || "No digest available";

  document.getElementById("meta").innerHTML = `
    <span>run ${escapeHtml(result.run_id)}</span>
    <span>mode ${escapeHtml(result.mode)}</span>
    <span>codegen ${escapeHtml(result.plan.codegen_mode)}</span>
    <span>detected via ${escapeHtml(result.incident.detection_source)}</span>
    <span class="pill ${result.groundedness?.ok ? "ok" : "error"}">${escapeHtml(result.groundedness?.badge || "unverified")}</span>
  `;

  renderTimeline(result.timeline);

  document.getElementById("tools").innerHTML =
    tools
      .map(
        (tool) => `
          <li>
            <span class="pill ${escapeHtml(tool.status)}">${escapeHtml(tool.status)}</span>
            <code>${escapeHtml(tool.tool)}</code>
            <span>${escapeHtml(tool.detail || "")}</span>
          </li>
        `
      )
      .join("") || "<li>No context calls recorded.</li>";

  renderLineageGraph(result);

  document.getElementById("summary").textContent = result.plan.summary;
  document.getElementById("steps").innerHTML = (result.plan.steps || [])
    .map((step) => `<li>${escapeHtml(step)}</li>`)
    .join("");

  const blastItems = (result.blast_radius.downstream || []).map(
    (item) =>
      `<li>Hop ${escapeHtml(item.hop)} · <strong>${escapeHtml(item.entity.name)}</strong> (${escapeHtml(item.entity.type)}) — ${escapeHtml(item.impact_reason)}</li>`
  );
  const upstream = result.blast_radius.upstream || [];
  if (upstream.length) {
    blastItems.push("<li><strong>Upstream evidence</strong></li>");
    for (const item of upstream) {
      blastItems.push(
        `<li>↑ Hop ${escapeHtml(item.hop)} · <strong>${escapeHtml(item.entity.name)}</strong> (${escapeHtml(item.entity.type)}) — ${escapeHtml(item.impact_reason)}</li>`
      );
    }
  }
  const columns = result.blast_radius.column_impacts || [];
  if (columns.length) {
    blastItems.push("<li><strong>Column-level impact</strong></li>");
    for (const column of columns.slice(0, 8)) {
      blastItems.push(
        `<li><code>${escapeHtml(column.column)}</code> → ${escapeHtml(column.consumer)}</li>`
      );
    }
  }
  document.getElementById("blast").innerHTML =
    blastItems.join("") || "<li>No downstream consumers found.</li>";

  const artifactList = document.getElementById("artifacts");
  artifactList.innerHTML = "";
  for (const artifact of artifacts) {
    const item = document.createElement("li");
    const relativePath = relativeArtifactPath(artifact.path, result.incident.id);
    const button = document.createElement("button");
    button.type = "button";
    button.className = "linkish";
    button.textContent = relativePath;
    button.title = `${artifact.description} · grounded in ${(artifact.grounded_in || []).join(", ")}`;
    button.addEventListener("click", () => showArtifact(result.incident.id, relativePath));
    item.appendChild(button);
    artifactList.appendChild(item);
  }

  const before = result.plan.write_back.before || {};
  const after = result.plan.write_back.after || {};
  document.getElementById("diff").innerHTML = `
    <div class="diff-col">
      <h4>Before</h4>
      <pre>${escapeHtml(JSON.stringify({ tags: before.tags, owners: before.owners }, null, 2))}</pre>
    </div>
    <div class="diff-col">
      <h4>After</h4>
      <pre>${escapeHtml(JSON.stringify({ tags: after.tags, owners: after.owners }, null, 2))}</pre>
    </div>
  `;

  document.getElementById("writeback").innerHTML =
    (result.plan.write_back.results || [])
      .map(
        (action) => `
          <li>
            <span class="pill ${escapeHtml(action.status)}">${escapeHtml(action.status)}</span>
            ${escapeHtml(action.action)}
            ${action.detail ? `<span> — ${escapeHtml(action.detail)}</span>` : ""}
          </li>
        `
      )
      .join("") || "<li>No catalog changes proposed.</li>";

  document.getElementById("actions").innerHTML =
    (result.actions_taken || [])
      .map(
        (action) => `
          <li>
            <span class="pill ${action.status === "error" ? "error" : "ok"}">${escapeHtml(action.status)}</span>
            <code>${escapeHtml(action.kind)}</code>
            <span>${escapeHtml(action.destination)} · ${escapeHtml(action.summary)}</span>
          </li>
        `
      )
      .join("") || "<li>No operational action proposed.</li>";

  applyBtn.disabled = !result.pending_write_back;
  applyBtn.innerHTML = applied
    ? '<span class="button-index">✓</span>Sealed plan applied'
    : '<span class="button-index">02</span>Apply sealed plan';
}

async function loadHealth() {
  const contextMode = document.getElementById("context-mode");
  try {
    const response = await fetch("/api/health");
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    state.health = await response.json();
    document.getElementById("product-version").textContent = `Remedi v${state.health.version}`;
    const fixture = state.health.mode === "fixture";
    document.getElementById("report-link").hidden = !fixture;
    const blockers = state.health.gates?.live_blockers || [];
    contextMode.textContent = fixture
      ? "Offline verification · DataHub not connected"
      : state.health.gates?.live_gms_connected
        ? "Live DataHub GMS · connected"
        : `Live mode blocked · ${blockers.join(", ") || "provider unavailable"}`;
  } catch (error) {
    contextMode.textContent = `Health unavailable · ${error.message}`;
  }
}

async function loadIncidents() {
  const triageResponse = await apiFetch("/api/triage");
  let triage = { items: [] };
  let incidents = [];
  if (triageResponse.ok) {
    triage = await triageResponse.json();
    incidents = (triage.items || []).map((item) => item.incident);
  } else {
    const incidentResponse = await apiFetch("/api/incidents");
    if (!incidentResponse.ok) {
      throw new Error(
        `Triage API returned ${triageResponse.status}; incident API returned ${incidentResponse.status}`
      );
    }
    incidents = await incidentResponse.json();
  }
  const ranked = new Map(
    (triage.items || []).map((item) => [
      item.incident.id,
      {
        risk_score: item.risk_score,
        priority: item.priority,
        risk_reasons: item.reasons,
        recommended_action: item.recommended_action,
      },
    ])
  );

  state.incidents = incidents
    .map((incident) => ({ ...incident, ...(ranked.get(incident.id) || {}) }))
    .sort((a, b) => (b.risk_score ?? -1) - (a.risk_score ?? -1));

  if (state.selected && !state.incidents.some((incident) => incident.id === state.selected)) {
    state.selected = null;
  }
  if (state.incidents.length && !state.selected) {
    state.selected = triage.top_incident_id || state.incidents[0].id;
  }
  renderIncidents();

  const incident = selectedIncident();
  if (incident && !state.lastResult) {
    setStatus(
      incident.recommended_action
        ? `${incident.priority} · risk ${incident.risk_score} — ${incident.recommended_action}`
        : `Selected ${incident.id}. Propose a grounded fix to continue.`
    );
  }
}

async function runRemediation(applyStoredProposal) {
  if (!state.selected) return;
  runBtn.disabled = true;
  applyBtn.disabled = true;
  selftestBtn.disabled = true;
  document.body.classList.add("agent-running");
  artifactShellEl.hidden = true;
  setStatus(
    applyStoredProposal
      ? "Verifying proposal digest and applying the exact sealed plan…"
      : state.health?.mode === "fixture"
        ? "Reading schema, queries, ownership, and lineage from the replay catalog…"
        : "Reading schema, queries, ownership, and lineage from live DataHub…",
    "running"
  );

  try {
    const runId = state.lastResult?.proposal_id || state.lastResult?.run_id;
    const response = await apiFetch(applyStoredProposal ? "/api/apply" : "/api/run", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(
        applyStoredProposal
          ? { run_id: runId || undefined, incident_id: runId ? undefined : state.selected }
          : { incident_id: state.selected, dry_run: true }
      ),
    });
    if (!response.ok) {
      const error = await response.json().catch(() => ({}));
      throw new Error(error.detail || `Request failed (${response.status})`);
    }

    const result = await response.json();
    state.lastResult = result;
    renderResult(result);
    setStatus(result.message, result.success ? "done" : "error");
    if (applyStoredProposal) await loadIncidents();
    outputEl.scrollIntoView({ behavior: "smooth", block: "start" });
  } catch (error) {
    setStatus(`Action stopped — ${error.message}`, "error");
  } finally {
    document.body.classList.remove("agent-running");
    runBtn.disabled = !state.selected;
    selftestBtn.disabled = false;
    if (state.lastResult?.pending_write_back) applyBtn.disabled = false;
  }
}

function renderSelftest(report) {
  outputEl.hidden = false;
  outputEl.classList.add("selftest-output");
  const total = report.passed + report.failed;
  document.getElementById("metric-impact").textContent = String(report.passed);
  document.getElementById("metric-artifacts").textContent = String(total);
  document.getElementById("metric-tools").textContent = `${report.elapsed_ms}ms`;
  document.getElementById("receipt-state").textContent = report.ok
    ? "Offline verification passed"
    : "Offline verification failed";
  document.getElementById("receipt-digest").textContent = report.report_path || "Selftest report";
  document.getElementById("meta").innerHTML = `
    <span>selftest</span>
    <span>version ${escapeHtml(report.version)}</span>
    <span class="pill ${report.ok ? "ok" : "error"}">${report.ok ? "pass" : "fail"}</span>
    <span>${escapeHtml(report.passed)}/${escapeHtml(total)} checks</span>
  `;
  renderTimeline(
    (report.checks || []).map((check) => ({
      name: check.name,
      detail: check.ok ? check.detail || "verified" : `FAILED · ${check.detail}`,
      duration_ms: 0,
    }))
  );
  document.getElementById("summary").textContent = report.summary || "";
  document.getElementById("steps").innerHTML = (report.checks || [])
    .map(
      (check) =>
        `<li><strong>${escapeHtml(check.name)}</strong> — ${check.ok ? "verified" : escapeHtml(check.detail)}</li>`
    )
    .join("");
  document.getElementById("tools").innerHTML =
    "<li>Selftest runs against an isolated copy of the committed fixture catalog.</li>";
  document.getElementById("blast").innerHTML =
    "<li>Choose an incident and propose a fix to render its lineage graph.</li>";
  document.getElementById("lineage-graph").innerHTML = "";
  document.getElementById("artifacts").innerHTML = "";
  document.getElementById("writeback").innerHTML = "";
  document.getElementById("actions").innerHTML = "";
  document.getElementById("diff").innerHTML = "";
  applyBtn.disabled = true;
}

runBtn.addEventListener("click", () => runRemediation(false));
applyBtn.addEventListener("click", () => {
  const runId = state.lastResult?.proposal_id || state.lastResult?.run_id;
  if (
    !window.confirm(
      `Apply sealed proposal ${runId || ""}? This writes tags, ownership, glossary terms, and a resolution document to the active catalog.`
    )
  ) {
    return;
  }
  runRemediation(true);
});

selftestBtn.addEventListener("click", async () => {
  selftestBtn.disabled = true;
  runBtn.disabled = true;
  applyBtn.disabled = true;
  document.body.classList.add("agent-running");
  setStatus("Running isolated offline verification…", "running");
  try {
    const response = await apiFetch("/api/selftest");
    if (!response.ok) throw new Error(`Selftest returned ${response.status}`);
    const report = await response.json();
    renderSelftest(report);
    setStatus(
      report.ok
        ? `Offline verification passed — ${report.passed}/${report.passed + report.failed} checks`
        : `Offline verification failed — ${report.failed} check(s) need attention`,
      report.ok ? "done" : "error"
    );
    outputEl.scrollIntoView({ behavior: "smooth", block: "start" });
  } catch (error) {
    setStatus(`Selftest stopped — ${error.message}`, "error");
  } finally {
    document.body.classList.remove("agent-running");
    selftestBtn.disabled = false;
    runBtn.disabled = !state.selected;
  }
});

document.getElementById("close-artifact").addEventListener("click", () => {
  artifactShellEl.hidden = true;
});

async function start() {
  await loadHealth();
  if (state.health?.gates?.api_auth_required && !state.apiKey) {
    const key = window.prompt(
      "Enter the Remedi API key for this protected session. It is kept only in this browser tab."
    );
    if (!key) throw new Error("A Remedi API key is required for this configuration.");
    state.apiKey = key;
    window.sessionStorage.setItem("remediApiKey", key);
  }
  await loadIncidents();
}

start().catch((error) => {
  if (String(error.message).includes("401")) {
    state.apiKey = "";
    window.sessionStorage.removeItem("remediApiKey");
  }
  setStatus(`Startup stopped — ${error.message}`, "error");
});
