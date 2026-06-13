import { aliasFromPath } from "../shared/tauriBridge.js";
import { runScopedEvents } from "./activeRunView.js";
import { normalizeResult } from "./resultState.js";
import { maxEventSeq, normalizeEventRecords } from "./sessionEventProjection.js";
import { displayStatusFromSession } from "./sessionStatus.js";
import { stripTranscriptPayload } from "./transcriptProjection.js";

const ACTIVE_RUN_STATUSES = new Set(["created", "initializing", "running", "pause_requested", "cancel_requested"]);

export function currentSessionSnapshot(currentSession) {
  const session = currentSession && typeof currentSession === "object" ? currentSession : {};
  const events = normalizeEventRecords(session.events);
  return {
    sessionId: String(session.sessionId || ""),
    runId: String(session.runId || session.result?.latest_run?.run_id || ""),
    status: String(session.status || "idle"),
    result: session.result || null,
    visibleRunSnapshot: session.visibleRunSnapshot || {},
    events,
    sessionEvents: events,
    lastSeq: maxEventSeq(events),
    sources: normalizeSources(session.sources || []),
  };
}

export function sessionSnapshotFromResponse(sessionId, data) {
  const payload = data && typeof data === "object" ? data : {};
  const runId = String(payload.latest_run?.run_id || "");
  const sessionEvents = normalizeEventRecords(payload.events);
  return {
    sessionId: String(sessionId || payload.session_id || ""),
    runId,
    status: displayStatusFromSession(payload),
    result: normalizeResult(payload),
    visibleRunSnapshot: {},
    events: runScopedEvents(sessionEvents, runId),
    sessionEvents,
    lastSeq: maxEventSeq(sessionEvents),
    latest_run: payload.latest_run || {},
    updated_at: String(payload.updated_at || ""),
    sources: normalizeSources(payload.request?.sources || []),
  };
}

export function emptySessionSnapshot(sessionId) {
  return {
    sessionId: String(sessionId || ""),
    runId: "",
    status: "idle",
    result: null,
    visibleRunSnapshot: {},
    events: [],
    sessionEvents: [],
    lastSeq: 0,
    sources: [],
  };
}

export function mergeRunSnapshotIntoSessionSnapshot(sessionSnapshot, runSnapshot) {
  const session = sessionSnapshot && typeof sessionSnapshot === "object" ? sessionSnapshot : emptySessionSnapshot("");
  const run = runSnapshot && typeof runSnapshot === "object" ? runSnapshot : {};
  const runId = String(run.run_id || session.runId || session.latest_run?.run_id || "").trim();
  const status = String(run.status || session.status || "idle");
  const sessionEvents = normalizeEventRecords(
    Array.isArray(session.sessionEvents) ? session.sessionEvents : session.events
  );
  const visibleRunSnapshot = normalizeVisibleRunSnapshot(run);
  return {
    ...session,
    runId,
    status,
    visibleRunSnapshot,
    events: runScopedEvents(sessionEvents, runId),
    sessionEvents,
    latest_run: {
      ...(session.latest_run && typeof session.latest_run === "object" ? session.latest_run : {}),
      run_id: runId,
      status,
      result_status: status,
    },
    sources: normalizeSources(session.sources?.length ? session.sources : run.request?.sources || []),
    lastSeq: Math.max(
      Number(session.lastSeq || 0),
      Number(visibleRunSnapshot?.result?.file_state?.event_count || visibleRunSnapshot?.tool_state?.file_state?.event_count || 0)
    ),
    updated_at: String(run.updated_at || session.updated_at || ""),
  };
}

export function activeRunResumePayload(snapshot) {
  const runId = String(snapshot?.visibleRunSnapshot?.run_id || snapshot?.runId || snapshot?.latest_run?.run_id || "").trim();
  if (!runId) return null;
  const status = String(
    snapshot?.visibleRunSnapshot?.status || snapshot?.latest_run?.status || snapshot?.status || ""
  ).trim();
  if (!ACTIVE_RUN_STATUSES.has(status)) return null;
  return {
    runId,
    sessionId: String(
      snapshot?.visibleRunSnapshot?.session_id || snapshot?.sessionId || snapshot?.latest_run?.session_id || ""
    ).trim(),
    afterSeq: snapshot?.lastSeq || maxEventSeq(snapshot?.events || []),
  };
}

function normalizeSources(value) {
  return listFrom(value)
    .filter((item) => item && typeof item === "object" && item.path)
    .map((item, index) => ({
      alias: String(item.alias || aliasFromPath(item.path) || `source_${index + 1}`),
      path: String(item.path || ""),
      type: item.type === "raster" || item.type === "csv" ? item.type : "vector",
      crs: String(item.crs || ""),
    }));
}

function normalizeVisibleRunSnapshot(run) {
  if (!run || typeof run !== "object" || !Object.keys(run).length) return {};
  const result = normalizeResult(run.result);
  return {
    ...run,
    run_id: String(run.run_id || ""),
    session_id: String(run.session_id || ""),
    status: String(run.status || result.status || ""),
    request: run.request && typeof run.request === "object" ? run.request : {},
    pending_task: run.pending_task && typeof run.pending_task === "object" ? run.pending_task : {},
    transcript: {},
    workflow: run.workflow && typeof run.workflow === "object" ? run.workflow : {},
    tool_state: run.tool_state && typeof run.tool_state === "object" ? run.tool_state : {},
    quality_findings: Array.isArray(run.quality_findings) ? run.quality_findings : [],
    report_audit: run.report_audit && typeof run.report_audit === "object" ? run.report_audit : {},
    decision_rows: Array.isArray(run.decision_rows) ? run.decision_rows : [],
    result,
    updated_at: String(run.updated_at || ""),
  };
}

function listFrom(value) {
  return Array.isArray(value) ? value : [];
}
