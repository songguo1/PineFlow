import { useEffect, useMemo, useRef, useState } from "react";

import { buildCommandItems } from "../shared/commandItems.js";
import { getLocalizedUi } from "../uiText.js";
import { AppTopbar } from "../layout/AppTopbar.jsx";
import { ConversationWorkspace } from "../conversation/ConversationWorkspace.jsx";
import { SettingsDrawer } from "../settings/SettingsDrawer.jsx";
import { ContextSidebar, DataSourcesSidebar } from "../workspace/WorkspaceSidebars.jsx";
import { WorkspaceLayout } from "../workspace/WorkspaceLayout.jsx";
import { buildExecutionRequest, useAgentActions } from "../session/useAgentActions.js";
import { useAppSessionState } from "./useAppSessionState.js";
import { useAppSettings } from "./useAppSettings.js";
import { useEnvironmentStatus } from "../workspace/useEnvironmentStatus.js";
import { useResumeInteraction } from "../session/useResumeInteraction.js";
import { useResizableLayout } from "../layout/useResizableLayout.js";
import { useSessionMemory } from "../workspace/useSessionMemory.js";
import { useSessionStore } from "../session/useSessionStore.js";
import { useRunExecution } from "../session/useRunExecution.js";

export default function App() {
  const { settings, updateSetting, apiKey, updateApiKey } = useAppSettings();
  const [settingsOpen, setSettingsOpen] = useState(false);
  const {
    layoutRef,
    layoutStyle,
    isLayoutDragging,
    resizingPanel,
    navCollapsed,
    leftCollapsed,
    contextCollapsed,
    setNavCollapsed,
    setLeftCollapsed,
    setContextCollapsed,
    startLayoutResize,
    toggleSplitterPanel,
  } = useResizableLayout();
  const {
    sources,
    setSources,
    message,
    setMessage,
    events,
    setEvents,
    result,
    setResult,
    visibleRunSnapshot,
    setVisibleRunSnapshot,
    sessionId,
    setSessionId,
    runId,
    setRunId,
    adoptActiveRun,
    status,
    setStatus,
    error,
    setError,
    sessionView,
    runState,
    resetSessionState,
    applySessionSnapshot,
    beginVisibleRun,
  } = useAppSessionState();
  const activeSessionIdRef = useRef("");
  const activeRunIdRef = useRef("");
  useEffect(() => {
    activeSessionIdRef.current = sessionId;
  }, [sessionId]);
  useEffect(() => {
    activeRunIdRef.current = runId;
  }, [runId]);
  const resumeRunPollingRef = useRef(null);
  const ui = useMemo(() => getLocalizedUi(settings.locale), [settings.locale]);
  const commandItems = useMemo(() => buildCommandItems(ui), [ui]);
  const pendingTask = runState.pendingTask;
  const missingSlots = runState.missingSlots;
  const allowedActions = runState.allowedActions;
  const activeIssue = runState.activeIssue;
  const activeRisk = runState.activeRisk;
  const repair = runState.repair;
  const isAwaitingUser = runState.isAwaitingUser;
  const isAwaitingConfirmation = runState.isAwaitingConfirmation;
  const hasPendingInteraction = runState.hasPendingInteraction;

  const qgis = useMemo(
    () => ({
      launcher: settings.qgisLauncher,
      prefix_path: settings.qgisPrefixPath,
    }),
    [settings.qgisLauncher, settings.qgisPrefixPath]
  );
  const { health, recentOutputs } = useEnvironmentStatus({
    apiBaseUrl: settings.apiBaseUrl,
    qgis,
    ui,
    sessionId,
    status,
  });
  const transcript = sessionView.transcript;
  const visibleResult = sessionView.rawResult;
  const toolStateView = sessionView.toolState;
  const artifactView = sessionView.artifacts;
  const { resumePatch, resumeMode, setResumeMode, updateResumePatch, resumeFields } = useResumeInteraction({
    pendingTask,
    missingSlots,
    status,
    isAwaitingUser,
    allowedActions,
  });

  const sessionStore = useSessionStore({
    apiBaseUrl: settings.apiBaseUrl,
    ui,
    currentSession: { sessionId, runId, status, result, visibleRunSnapshot, events, sources },
    onApplySession: applySessionSnapshotAndRefs,
    onResetSession: resetLocalSession,
    onError: setError,
    onResumeRunPolling: (payload) => resumeRunPollingRef.current?.(payload),
  });
  const { sessions } = sessionStore;
  const { performRunRequest, performRunAction, resumeRunPolling } = useRunExecution({
    apiBaseUrl: settings.apiBaseUrl,
    ui,
    setMessage,
    setError,
    setEvents,
    setResult,
    setVisibleRunSnapshot,
    setStatus,
    setSessionId,
    setRunId,
    adoptActiveRun: adoptActiveRunAndRefs,
    beginVisibleRun,
    getActiveSessionId: () => activeSessionIdRef.current,
    getActiveRunId: () => activeRunIdRef.current,
    updateSessionSummary: sessionStore.updateSessionSummary,
  });
  resumeRunPollingRef.current = resumeRunPolling;
  const {
    sessionMemory,
    sessionMemoryDraft,
    memoryEditing,
    setSessionMemoryDraft,
    setMemoryEditing,
    saveMemory,
    resetMemory,
  } = useSessionMemory({
    apiBaseUrl: settings.apiBaseUrl,
    sessionId,
    runId,
    ui,
    onError: setError,
  });
  const {
    send,
    submitPatch,
    submitSourceRequest,
    resetSessionFromUi,
    submitCommand,
    confirmRepair,
    rejectRepair,
    cancelPendingTask,
    handlePause,
    handleCancelRun,
  } = useAgentActions({
    settings,
    apiKey,
    qgis,
    sources,
    sessionId,
    runId,
    message,
    resumeMode,
    allowedActions,
    isAwaitingUser,
    isAwaitingConfirmation,
    hasPendingInteraction,
    runState,
    ui,
    performRunRequest,
    performRunAction,
    resetLocalSession,
    setError,
    setStatus,
    setSettingsOpen,
  });

  function validateActionSettings() {
    if (!String(apiKey || "").trim()) {
      setError(ui.errors.apiKeyRequired);
      setSettingsOpen(true);
      return false;
    }
    if (!settings.baseUrl.trim() || !settings.model.trim()) {
      setError(ui.errors.baseUrlAndModelRequired);
      setSettingsOpen(true);
      return false;
    }
    return true;
  }

  function resetLocalSession() {
    activeSessionIdRef.current = "";
    activeRunIdRef.current = "";
    resetSessionState();
    resetMemory();
  }

  function applySessionSnapshotAndRefs(snapshot) {
    activeSessionIdRef.current = String(snapshot?.sessionId || "");
    activeRunIdRef.current = String(snapshot?.runId || snapshot?.latest_run?.run_id || "");
    applySessionSnapshot(snapshot);
  }

  function adoptActiveRunAndRefs({ sessionId: nextSessionId = "", runId: nextRunId = "" } = {}) {
    const normalizedSessionId = String(nextSessionId || "").trim();
    const normalizedRunId = String(nextRunId || "").trim();
    if (normalizedSessionId) activeSessionIdRef.current = normalizedSessionId;
    if (normalizedRunId) activeRunIdRef.current = normalizedRunId;
    adoptActiveRun({ sessionId: normalizedSessionId, runId: normalizedRunId });
  }

  async function resolvePendingSourceRequest(sourceRequest, selectedSources, nextSources) {
    setSources(nextSources);
    await submitSourceRequest(sourceRequest, selectedSources, nextSources);
  }

  return (
    <div className="app">
      <AppTopbar
        ui={ui}
        health={health}
        runState={runState}
        onOpenSettings={() => setSettingsOpen(true)}
        onResetSession={resetSessionFromUi}
      />
      {error ? <div className="error-banner">{error}</div> : null}

      <WorkspaceLayout
        ui={ui}
        layoutRef={layoutRef}
        layoutStyle={layoutStyle}
        isLayoutDragging={isLayoutDragging}
        resizingPanel={resizingPanel}
        navCollapsed={navCollapsed}
        leftCollapsed={leftCollapsed}
        contextCollapsed={contextCollapsed}
        sessions={sessions}
        activeSessionId={sessionId}
        onToggleNav={() => setNavCollapsed((v) => !v)}
        onNewSession={sessionStore.handleNewSession}
        onSwitchSession={sessionStore.switchSession}
        onArchiveSession={sessionStore.archiveSession}
        onDeleteSession={sessionStore.deleteSession}
        onStartResize={startLayoutResize}
        onToggleSplitterPanel={toggleSplitterPanel}
        left={(
          <DataSourcesSidebar
            ui={ui}
            collapsed={leftCollapsed}
            sources={sources}
            recentOutputs={recentOutputs}
            pendingTask={pendingTask}
            onToggle={() => setLeftCollapsed((v) => !v)}
            onSourcesChange={setSources}
            onResolveSourceRequest={resolvePendingSourceRequest}
            onError={setError}
          />
        )}
        center={(
          <ConversationWorkspace
            ui={ui}
            llmSettings={{ provider: settings.provider, model: settings.model }}
            sessionId={sessionId}
            transcript={transcript}
            runState={runState}
            pendingTask={pendingTask}
            activeIssue={activeIssue}
            activeRisk={activeRisk}
            repair={repair}
            allowedActions={allowedActions}
            hasPendingInteraction={hasPendingInteraction}
            resumeMode={resumeMode}
            setResumeMode={setResumeMode}
            resumePatch={resumePatch}
            resumeFields={resumeFields}
            missingSlots={missingSlots}
            message={message}
            commandItems={commandItems}
            onMessageChange={setMessage}
            onPatchChange={updateResumePatch}
            onSubmitCommand={submitCommand}
            onSubmitPatch={submitPatch}
            onSendMessage={send}
            onPause={handlePause}
            onCancelRun={handleCancelRun}
            onCancelPending={cancelPendingTask}
            onConfirmRepair={confirmRepair}
            onRejectRepair={rejectRepair}
            onSelectChoice={submitPatch}
            onError={setError}
          />
        )}
        right={(
          <ContextSidebar
            ui={ui}
            apiBaseUrl={settings.apiBaseUrl}
            collapsed={contextCollapsed}
            normalized={visibleResult}
            toolStateView={toolStateView}
            artifactView={artifactView}
            sessionMemory={sessionMemory}
            sessionMemoryDraft={sessionMemoryDraft}
            memoryEditing={memoryEditing}
            onToggle={() => setContextCollapsed((v) => !v)}
            onMemoryEdit={setMemoryEditing}
            onMemoryChange={setSessionMemoryDraft}
            onMemorySave={saveMemory}
          />
        )}
      />

      <SettingsDrawer
        ui={ui}
        open={settingsOpen}
        settings={settings}
        apiKey={apiKey}
        onChange={updateSetting}
        onApiKeyChange={updateApiKey}
        onError={setError}
        onClose={() => setSettingsOpen(false)}
      />
    </div>
  );
}
