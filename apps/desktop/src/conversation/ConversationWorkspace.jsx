import { ClipboardList } from "lucide-react";

import { PanelTitle } from "../layout/LayoutPrimitives.jsx";
import { ChatTranscript } from "./ChatTranscript.jsx";
import { ConversationComposer } from "./ConversationComposer.jsx";
import { ResumePanel } from "./ResumePanel.jsx";

export function ConversationWorkspace({
  ui,
  llmSettings,
  sessionId,
  transcript,
  runState,
  pendingTask,
  activeIssue,
  activeRisk,
  repair,
  allowedActions,
  hasPendingInteraction,
  resumeMode,
  setResumeMode,
  resumePatch,
  resumeFields,
  missingSlots,
  message,
  commandItems,
  onMessageChange,
  onPatchChange,
  onSubmitCommand,
  onSubmitPatch,
  onSendMessage,
  onPause,
  onCancelRun,
  onCancelPending,
  onConfirmRepair,
  onRejectRepair,
  onSelectChoice,
  onError,
}) {
  return (
    <main className="center">
      <section className="chat-head">
        <PanelTitle icon={ClipboardList} text={ui.sections.conversation || "Conversation"} />
        <div className="session">
          {sessionId ? sessionId.slice(0, 8) : ui.session.noSession} / {ui.statuses[runState.status] || runState.status}
        </div>
      </section>
      <ChatTranscript
        transcript={transcript}
        runState={runState}
        ui={ui}
      />
      {hasPendingInteraction ? (
        <section className="interaction-dock">
          <ResumePanel
            ui={ui}
            status={runState.status}
            pendingTask={pendingTask}
            issue={activeIssue}
            risk={activeRisk}
            repair={repair}
            allowedActions={allowedActions}
            resumeMode={resumeMode}
            onModeChange={setResumeMode}
            onConfirm={onConfirmRepair}
            onReject={onRejectRepair}
            onCancel={onCancelPending}
            onChoiceSelect={onSelectChoice}
          />
        </section>
      ) : null}
      <ConversationComposer
        ui={ui}
        llmSettings={llmSettings}
        message={message}
        onMessageChange={onMessageChange}
        commandItems={commandItems}
        runState={runState}
        resumeMode={resumeMode}
        missingSlots={missingSlots}
        pendingTask={pendingTask}
        allowedActions={allowedActions}
        resumePatch={resumePatch}
        resumeFields={resumeFields}
        onPatchChange={onPatchChange}
        onSubmitCommand={onSubmitCommand}
        onSubmitPatch={onSubmitPatch}
        onSendMessage={onSendMessage}
        onPause={onPause}
        onCancelRun={onCancelRun}
        onCancelPending={onCancelPending}
        onError={onError}
      />
    </main>
  );
}
