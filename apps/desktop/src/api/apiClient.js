export const DEFAULT_API_BASE_URL = "http://127.0.0.1:8765";
export const DEFAULT_QGIS_LAUNCHER = "D:\\software\\QGIS\\bin\\python-qgis-ltr.bat";
export const DEFAULT_QGIS_PREFIX_PATH = "D:\\software\\QGIS\\apps\\qgis-ltr";

export async function createRun(baseUrl, request) {
  const response = await fetch(`${trimBaseUrl(baseUrl)}/qgis/runs`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(request),
  });
  await ensureOk(response, "Run creation failed");
  return response.json();
}

export async function resumeRun(baseUrl, runId, request) {
  const response = await fetch(`${trimBaseUrl(baseUrl)}/qgis/runs/${encodeURIComponent(runId)}/resume`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(request),
  });
  await ensureOk(response, "Run resume failed");
  return response.json();
}

export async function sendRunAction(baseUrl, runId, action) {
  const response = await fetch(`${trimBaseUrl(baseUrl)}/qgis/runs/${encodeURIComponent(runId)}/actions`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ ...(action || {}), run_id: runId }),
  });
  await ensureOk(response, "Run action failed");
  return response.json();
}

export async function toolboxSearch(baseUrl, query, qgis) {
  const params = new URLSearchParams({
    q: query || "",
    launcher: qgis.launcher || "",
    prefix_path: qgis.prefix_path || "",
  });
  const response = await fetch(`${trimBaseUrl(baseUrl)}/qgis/toolbox/search?${params}`);
  await ensureOk(response, "Toolbox search failed");
  return response.json();
}

export async function algorithmHelp(baseUrl, algorithmId, qgis) {
  const params = new URLSearchParams({
    launcher: qgis.launcher || "",
    prefix_path: qgis.prefix_path || "",
  });
  const response = await fetch(
    `${trimBaseUrl(baseUrl)}/qgis/toolbox/help/${encodeURIComponent(algorithmId)}?${params}`
  );
  await ensureOk(response, "Algorithm help failed");
  return response.json();
}

export async function listSessions(baseUrl) {
  const response = await fetch(`${trimBaseUrl(baseUrl)}/qgis/sessions`);
  await ensureOk(response, "Session list failed");
  const data = await response.json();
  return data.sessions || [];
}

export async function getSession(baseUrl, sessionId) {
  const response = await fetch(`${trimBaseUrl(baseUrl)}/qgis/sessions/${encodeURIComponent(sessionId)}`);
  await ensureOk(response, "Session fetch failed");
  return response.json();
}

export async function archiveSession(baseUrl, sessionId) {
  const response = await fetch(`${trimBaseUrl(baseUrl)}/qgis/sessions/${encodeURIComponent(sessionId)}/archive`, {
    method: "POST",
  });
  await ensureOk(response, "Session archive failed");
  return response.json().catch(() => ({}));
}

export async function deleteSession(baseUrl, sessionId) {
  const response = await fetch(`${trimBaseUrl(baseUrl)}/qgis/sessions/${encodeURIComponent(sessionId)}`, {
    method: "DELETE",
  });
  await ensureOk(response, "Session delete failed");
  return response.json().catch(() => ({}));
}

export async function listSessionRuns(baseUrl, sessionId) {
  const response = await fetch(`${trimBaseUrl(baseUrl)}/qgis/sessions/${encodeURIComponent(sessionId)}/runs`);
  await ensureOk(response, "Session runs fetch failed");
  const data = await response.json();
  return data.runs || [];
}

export async function listSessionEvents(baseUrl, sessionId, { afterSeq = 0, limit = 500 } = {}) {
  const params = new URLSearchParams({
    after_seq: String(Math.max(0, Number(afterSeq) || 0)),
    limit: String(Math.min(Math.max(1, Number(limit) || 500), 2000)),
  });
  const response = await fetch(
    `${trimBaseUrl(baseUrl)}/qgis/sessions/${encodeURIComponent(sessionId)}/events?${params}`
  );
  await ensureOk(response, "Session events fetch failed");
  return response.json();
}

export async function getRun(baseUrl, runId) {
  const response = await fetch(`${trimBaseUrl(baseUrl)}/qgis/runs/${encodeURIComponent(runId)}`);
  await ensureOk(response, "Run fetch failed");
  return response.json();
}

export async function getRunSnapshot(baseUrl, runId) {
  const response = await fetch(`${trimBaseUrl(baseUrl)}/qgis/runs/${encodeURIComponent(runId)}/snapshot`);
  await ensureOk(response, "Run snapshot fetch failed");
  return response.json();
}

export async function listRunEvents(baseUrl, runId, { afterSeq = 0, limit = 500 } = {}) {
  const params = new URLSearchParams({
    after_seq: String(Math.max(0, Number(afterSeq) || 0)),
    limit: String(Math.min(Math.max(1, Number(limit) || 500), 2000)),
  });
  const response = await fetch(
    `${trimBaseUrl(baseUrl)}/qgis/runs/${encodeURIComponent(runId)}/events?${params}`
  );
  await ensureOk(response, "Run events fetch failed");
  return response.json();
}

export async function getSessionMemory(baseUrl, sessionId) {
  const response = await fetch(
    `${trimBaseUrl(baseUrl)}/qgis/sessions/${encodeURIComponent(sessionId)}/memory`
  );
  await ensureOk(response, "Session memory fetch failed");
  const data = await response.json();
  return data.content || "";
}

export async function saveSessionMemory(baseUrl, sessionId, content) {
  const response = await fetch(
    `${trimBaseUrl(baseUrl)}/qgis/sessions/${encodeURIComponent(sessionId)}/memory`,
    {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ content }),
    }
  );
  await ensureOk(response, "Session memory save failed");
}

export async function getSessionReport(baseUrl, sessionId, artifactId) {
  const response = await fetch(
    `${trimBaseUrl(baseUrl)}/qgis/sessions/${encodeURIComponent(sessionId)}/reports/${encodeURIComponent(artifactId)}`
  );
  await ensureOk(response, "Session report fetch failed");
  const data = await response.json();
  return data.report || {};
}

export async function listRecentOutputs(baseUrl) {
  const response = await fetch(`${trimBaseUrl(baseUrl)}/qgis/outputs`);
  await ensureOk(response, "Output list failed");
  const data = await response.json();
  return data.outputs || [];
}

export async function pauseRun(baseUrl, runId) {
  const response = await fetch(
    `${trimBaseUrl(baseUrl)}/qgis/runs/${encodeURIComponent(runId)}/pause`,
    { method: "POST" }
  );
  await ensureOk(response, "Run pause request failed");
}

export async function cancelRun(baseUrl, runId) {
  const response = await fetch(
    `${trimBaseUrl(baseUrl)}/qgis/runs/${encodeURIComponent(runId)}/cancel`,
    { method: "POST" }
  );
  await ensureOk(response, "Run cancel request failed");
}

export async function healthCheck(baseUrl, qgis, deep = true) {
  const params = new URLSearchParams({
    deep: deep ? "true" : "false",
    launcher: qgis.launcher || "",
    prefix_path: qgis.prefix_path || "",
  });
  const response = await fetch(`${trimBaseUrl(baseUrl)}/qgis/health?${params}`);
  await ensureOk(response, "Health check failed");
  return response.json();
}

async function ensureOk(response, prefix) {
  if (response.ok) return;
  const detail = await response.text().catch(() => "");
  throw new Error(`${prefix}: HTTP ${response.status}${detail ? ` - ${detail}` : ""}`);
}

function trimBaseUrl(value) {
  return String(value || DEFAULT_API_BASE_URL).trim().replace(/\/+$/, "");
}
