import { useRef } from "react";

import { appendFrontendLog } from "../shared/frontendLog.js";
import { canApplyActiveRunEvent, canApplyActiveRunSnapshot } from "./activeRunView.js";
import { createRunProtocolClient, nextRunIdFromControlResponse, sessionIdFromControlResponse } from "./runProtocolClient.js";
import { createSessionProtocolClient } from "./sessionProtocolClient.js";
import { applyEventToSnapshot, eventFromRecord } from "./sessionEventProjection.js";
import { eventNeedsSnapshotRefresh } from "./runEventProjection.js";
import { mergeRunSnapshotIntoSessionSnapshot } from "./sessionSnapshotProjection.js";
import { stripTranscriptPayload } from "./transcriptProjection.js";

const TERMINAL_RUN_STATUSES = new Set([
  "completed",
  "failed",
  "cancelled",
  "paused",
  "awaiting_user",
  "awaiting_confirmation",
]);

export function useRunExecution({
  apiBaseUrl,
  ui,
  setMessage,
  setError,
  setEvents,
  setResult,
  setVisibleRunSnapshot,
  setStatus,
  setSessionId,
  setRunId,
  adoptActiveRun,
  beginVisibleRun,
  getActiveSessionId,
  getActiveRunId,
  updateSessionSummary,
}) {
  const activePolls = useRef(new Map());
  const snapshotRefreshes = useRef(new Map());
  const sessionRefreshes = useRef(new Map());
  const requestInFlight = useRef(false);
  const actionInFlight = useRef(false);
  const protocol = createRunProtocolClient(apiBaseUrl);
  const sessionProtocol = createSessionProtocolClient(apiBaseUrl);

  async function performRunRequest(request, userMessage, { clearInput = true, preserveEvents = false, showUserMessage = true } = {}) {
    if (requestInFlight.current) return null;
    requestInFlight.current = true;
    let runSessionId = String(request.session_id || "").trim();
    const previousRunId = String(getActiveRunId?.() || "").trim();
    const launchSessionId = String(getActiveSessionId?.() || "").trim();
    const startedInBlankSession = !runSessionId && !String(getActiveSessionId?.() || "").trim();
    if (clearInput) setMessage("");
    beginVisibleRun({ preserveEvents, userMessage, showUserMessage });
    appendFrontendLog({
      level: "info",
      source: "run.start",
      message: request.resume ? `resume:${request.resume.action}` : "start",
      session_id: request.session_id || "",
      preserveEvents,
    });

    try {
      const run = request.resume && previousRunId
        ? await protocol.resumeExecutionRun(previousRunId, request)
        : await protocol.createExecutionRun(request);
      const runId = String(run.run_id || "").trim();
      runSessionId = String(run.session_id || runSessionId || "").trim();
      if (!runId) throw new Error("Run creation did not return run_id.");
      if (shouldAdoptStartedRun({ runId, runSessionId, launchSessionId, startedInBlankSession })) {
        adoptVisibleRun(runSessionId, runId);
        await refreshSessionResult(runSessionId);
        await refreshVisibleRunSnapshot(runId, runSessionId);
      }
      await pollRunEvents({
        runId,
        sessionId: runSessionId,
        startedInBlankSession,
      });
    } catch (err) {
      const message = err.message || ui.errors.requestFailed;
      appendFrontendLog({ level: "error", source: "run.catch", message, stack: err.stack || "" });
      if (!shouldUpdateLaunchSurface({ launchSessionId, startedInBlankSession })) return;
      setStatus("failed");
      setError(message);
    } finally {
      requestInFlight.current = false;
    }
  }

  async function performRunAction(
    action,
    userMessage,
    { clearInput = true, preserveEvents = true, showUserMessage = true } = {}
  ) {
    if (actionInFlight.current) return null;
    const previousRunId = String(getActiveRunId?.() || "").trim();
    const launchSessionId = String(getActiveSessionId?.() || "").trim();
    if (!previousRunId) throw new Error("No active run to control.");
    actionInFlight.current = true;
    if (clearInput) setMessage("");
    beginVisibleRun({ preserveEvents, userMessage, showUserMessage });
    applyOptimisticRunControlStatus(action?.action_type, setStatus);
    appendFrontendLog({
      level: "info",
      source: "run.action",
      message: action?.action_type || "action",
      session_id: launchSessionId,
      run_id: previousRunId,
      preserveEvents,
    });

    try {
      const response = await protocol.sendRunControlAction(previousRunId, action);
      const nextRunId = nextRunIdFromControlResponse(response, previousRunId);
      const nextSessionId = sessionIdFromControlResponse(response, launchSessionId);
      if (nextRunId) {
        adoptVisibleRun(nextSessionId, nextRunId);
        await refreshSessionResult(nextSessionId);
        await refreshVisibleRunSnapshot(nextRunId, nextSessionId);
        await pollRunEvents({
          runId: nextRunId,
          sessionId: nextSessionId,
          startedInBlankSession: false,
        });
      }
      return response;
    } catch (err) {
      const message = err.message || ui.errors.requestFailed;
      appendFrontendLog({ level: "error", source: "run.action_catch", message, stack: err.stack || "" });
      setStatus("failed");
      setError(message);
      throw err;
    } finally {
      actionInFlight.current = false;
    }
  }

  function resumeRunPolling({ runId, sessionId, afterSeq = 0 } = {}) {
    const normalizedRunId = String(runId || "").trim();
    if (!normalizedRunId) return;
    refreshVisibleRunSnapshot(normalizedRunId, sessionId).catch((err) => {
      appendFrontendLog({
        level: "warn",
        source: "run.snapshot_resume",
        message: err.message || ui.errors.requestFailed,
      });
    });
    pollRunEvents({
      runId: normalizedRunId,
      sessionId,
      startedInBlankSession: false,
      initialAfterSeq: afterSeq,
    }).catch((err) => {
      appendFrontendLog({
        level: "error",
        source: "run.poll_resume",
        message: err.message || ui.errors.requestFailed,
        stack: err.stack || "",
      });
    });
  }

  async function pollRunEvents({ runId, sessionId, startedInBlankSession, initialAfterSeq = 0 }) {
    const previous = activePolls.current.get(runId);
    if (previous) previous.cancelled = true;
    const token = { cancelled: false };
    activePolls.current.set(runId, token);
    let afterSeq = Math.max(0, Number(initialAfterSeq) || 0);
    let idlePolls = 0;
    while (!token.cancelled) {
      const response = await protocol.pollRunEvents(runId, { afterSeq });
      const records = Array.isArray(response.events) ? response.events : [];
      let needsVisibleSnapshotRefresh = false;
      for (const record of records) {
        const event = eventFromRecord(record);
        if (!event) continue;
        afterSeq = Math.max(afterSeq, Number(event.seq || record.seq || 0));
        needsVisibleSnapshotRefresh = applyRunEvent(event, {
          runId,
          sessionId,
          startedInBlankSession,
        }) || needsVisibleSnapshotRefresh;
      }
      if (needsVisibleSnapshotRefresh && isRunVisible(runId, sessionId)) {
        await refreshVisibleRunSnapshot(runId, sessionId);
      }
      idlePolls = records.length ? 0 : idlePolls + 1;
      const run = await protocol.loadRun(runId);
      const runStatus = String(run.status || "");
      if (TERMINAL_RUN_STATUSES.has(runStatus) && idlePolls > 0) {
        if (isRunVisible(runId, sessionId)) {
          await refreshVisibleRunSnapshot(runId, sessionId);
        }
        if (runStatus === "paused" && String(getActiveRunId?.() || "") === runId) setStatus("paused");
        activePolls.current.delete(runId);
        return;
      }
      await delay(records.length ? 120 : 450);
    }
  }

  function applyRunEvent(event, { runId, sessionId, startedInBlankSession }) {
    const eventSessionId = String(event.session_id || sessionId || "").trim();
    const eventRunId = String(event.run_id || runId || "").trim();
    const activeSessionId = String(getActiveSessionId?.() || "").trim();
    const activeRunId = String(getActiveRunId?.() || "").trim();
    const isVisible = canApplyActiveRunEvent({
      event,
      activeSessionId,
      activeRunId,
      expectedSessionId: sessionId,
      expectedRunId: runId,
      startedInBlankSession,
    });

    if (!isVisible) {
      appendFrontendLog({
        level: "debug",
        source: "run.event_background",
        message: event.event || "event",
        session_id: eventSessionId,
        run_id: eventRunId,
        active_session_id: activeSessionId,
        active_run_id: activeRunId,
      });
      updateSessionSummary?.(eventSessionId, (snapshot) => applyEventToSnapshot(snapshot, event, ui));
      return false;
    }

    setEvents((current) => [...current, event]);
    adoptVisibleRun(eventSessionId, eventRunId);
    if (eventSessionId) {
      updateSessionSummary?.(eventSessionId, (snapshot) => applyEventToSnapshot(snapshot, event, ui));
    }
    applyVisibleRunEvent(event);
    if (eventSessionId && (event.transcript_item || event.result)) {
      refreshSessionResult(eventSessionId).catch((err) => {
        appendFrontendLog({
          level: "warn",
          source: "session.transcript_refresh",
          message: err.message || ui.errors.requestFailed,
        });
      });
    }
    return eventNeedsSnapshotRefresh(event);
  }

  function applyVisibleRunEvent(event) {
    if (event.event === "failed") {
      appendFrontendLog({
        level: "error",
        source: "run.failed_event",
        message: event.message || ui.messages.taskFailed,
        resultStatus: event.result?.status || "",
      });
      setError(event.message || ui?.messages?.taskFailed || "Task failed");
    }
    setVisibleRunSnapshot((current) => {
      const snapshot = current && typeof current === "object" ? current : {};
      const baseSnapshot = Object.keys(snapshot).length
        ? snapshot
        : {
            run_id: String(event?.run_id || ""),
            session_id: String(event?.session_id || ""),
            status: String(event?.result?.status || "running"),
            result: {
              status: String(event?.result?.status || "running"),
            },
          };
      const result = baseSnapshot.result && typeof baseSnapshot.result === "object"
        ? stripTranscriptPayload(baseSnapshot.result)
        : { status: baseSnapshot.status || event?.result?.status || "running" };
      const eventResult = event?.result && typeof event.result === "object"
        ? stripTranscriptPayload(event.result)
        : {};
      const status = String(eventResult.status || baseSnapshot.status || result.status || "running");
      return {
        ...stripTranscriptPayload(baseSnapshot),
        run_id: String(event?.run_id || baseSnapshot.run_id || ""),
        session_id: String(event?.session_id || baseSnapshot.session_id || ""),
        status,
        result: {
          ...result,
          ...eventResult,
          status,
        },
        pending_task: event?.pending_task && typeof event.pending_task === "object"
          ? event.pending_task
          : baseSnapshot.pending_task,
      };
    });
  }

  async function refreshVisibleRunSnapshot(runId, sessionId = "") {
    const normalizedRunId = String(runId || "").trim();
    if (!normalizedRunId) return;
    const inFlight = snapshotRefreshes.current.get(normalizedRunId);
    if (inFlight) {
      await inFlight;
      return;
    }
    const refreshPromise = (async () => {
      const snapshot = await protocol.loadRunSnapshot(normalizedRunId);
      const normalizedSessionId = String(snapshot.session_id || sessionId || "").trim();
      if (!canApplyVisibleSnapshot(snapshot)) {
        if (normalizedSessionId) {
          updateSessionSummary?.(
            normalizedSessionId,
            (currentSnapshot) => mergeRunSnapshotIntoSessionSnapshot(currentSnapshot, snapshot)
          );
        }
        return;
      }
      setVisibleRunSnapshot(snapshot && typeof snapshot === "object" ? snapshot : {});
      adoptVisibleRun(normalizedSessionId, snapshot?.run_id);
      if (snapshot?.status) setStatus(String(snapshot.status || "running"));
      await refreshSessionResult(normalizedSessionId);
      if (normalizedSessionId) {
        updateSessionSummary?.(
          normalizedSessionId,
          (currentSnapshot) => mergeRunSnapshotIntoSessionSnapshot(currentSnapshot, snapshot)
        );
      }
    })();
    snapshotRefreshes.current.set(normalizedRunId, refreshPromise);
    try {
      await refreshPromise;
    } finally {
      snapshotRefreshes.current.delete(normalizedRunId);
    }
  }

  return { performRunRequest, performRunAction, resumeRunPolling };

  async function refreshSessionResult(sessionId) {
    const normalizedSessionId = String(sessionId || "").trim();
    if (!normalizedSessionId || typeof setResult !== "function") return;
    const inFlight = sessionRefreshes.current.get(normalizedSessionId);
    if (inFlight) {
      await inFlight;
      return;
    }
    const refreshPromise = (async () => {
      const session = await sessionProtocol.loadSession(normalizedSessionId);
      const activeSessionId = String(getActiveSessionId?.() || "").trim();
      if (activeSessionId && activeSessionId !== normalizedSessionId) return;
      setResult(session);
    })();
    sessionRefreshes.current.set(normalizedSessionId, refreshPromise);
    try {
      await refreshPromise;
    } finally {
      sessionRefreshes.current.delete(normalizedSessionId);
    }
  }

  function shouldAdoptStartedRun({ runId, runSessionId, launchSessionId, startedInBlankSession }) {
    if (!runId) return false;
    const activeSessionId = String(getActiveSessionId?.() || "").trim();
    const activeRunId = String(getActiveRunId?.() || "").trim();
    if (runSessionId && activeSessionId === runSessionId) return true;
    if (startedInBlankSession && !activeSessionId && !activeRunId) return true;
    if (activeRunId && activeRunId !== runId) return false;
    return Boolean(launchSessionId && activeSessionId === launchSessionId);
  }

  function shouldUpdateLaunchSurface({ launchSessionId, startedInBlankSession }) {
    const activeSessionId = String(getActiveSessionId?.() || "").trim();
    const activeRunId = String(getActiveRunId?.() || "").trim();
    if (startedInBlankSession) return !activeSessionId && !activeRunId;
    return Boolean(launchSessionId && activeSessionId === launchSessionId);
  }

  function isRunVisible(runId, sessionId = "") {
    const activeRunId = String(getActiveRunId?.() || "").trim();
    const activeSessionId = String(getActiveSessionId?.() || "").trim();
    const normalizedRunId = String(runId || "").trim();
    const normalizedSessionId = String(sessionId || "").trim();
    if (activeRunId && normalizedRunId) return activeRunId === normalizedRunId;
    return Boolean(normalizedSessionId && activeSessionId === normalizedSessionId);
  }

  function adoptVisibleRun(sessionId, runId) {
    const normalizedSessionId = String(sessionId || "").trim();
    const normalizedRunId = String(runId || "").trim();
    if (typeof adoptActiveRun === "function") {
      adoptActiveRun({ sessionId: normalizedSessionId, runId: normalizedRunId });
      return;
    }
    if (normalizedSessionId) setSessionId(normalizedSessionId);
    if (normalizedRunId) setRunId?.(normalizedRunId);
  }

  function canApplyVisibleSnapshot(snapshot) {
    const activeRunId = String(getActiveRunId?.() || "").trim();
    const activeSessionId = String(getActiveSessionId?.() || "").trim();
    return canApplyActiveRunSnapshot({ snapshot, activeSessionId, activeRunId });
  }
}

function applyOptimisticRunControlStatus(actionType, setStatus) {
  if (actionType === "run.pause") setStatus?.("pause_requested");
  if (actionType === "run.cancel") setStatus?.("cancel_requested");
}

function delay(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}
