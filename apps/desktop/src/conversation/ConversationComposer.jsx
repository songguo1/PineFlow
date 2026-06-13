import { useState } from "react";
import { ArrowUp, Ban, FolderOpen, Loader2, Pause, Plus } from "lucide-react";

import { isLikelyTauri, pickDirectory } from "../shared/tauriBridge.js";

export function ConversationComposer({
  ui,
  message,
  onMessageChange,
  commandItems,
  runState,
  resumeMode,
  missingSlots,
  pendingTask,
  resumeFields = [],
  allowedActions,
  resumePatch,
  onPatchChange,
  onSubmitCommand,
  onSubmitPatch,
  onSendMessage,
  onPause,
  onCancelRun,
  onCancelPending,
  llmSettings,
  onError,
}) {
  const [commandMenuOpen, setCommandMenuOpen] = useState(false);
  const isAwaitingUser = runState.isAwaitingUser;
  const isAwaitingConfirmation = runState.isAwaitingConfirmation;
  const isPauseRequested = runState.isPauseRequested;
  const isCancelRequested = runState.isCancelRequested;
  const hasPendingInteraction = runState.hasPendingInteraction;
  const slotPatchSchema = pendingTask?.slot_patch_schema && typeof pendingTask.slot_patch_schema === "object" ? pendingTask.slot_patch_schema : {};
  const editableMissingSlots = missingSlots.filter((slot) => !slotPatchSchema?.[slot]?.source_required);
  const structuredFields = (resumeFields.length ? resumeFields : editableMissingSlots.map((slot) => ({ slot, schema: slotPatchSchema[slot] || {} })))
    .filter((field) => !field.sourceRequired);
  const commandQuery = message.trimStart().startsWith("/") ? message.trimStart().slice(1).toLowerCase() : "";
  const commandSuggestions = commandItems.filter((item) =>
    `${item.command} ${item.label} ${item.description}`.toLowerCase().includes(commandQuery)
  );
  const showSlashCommands = !hasPendingInteraction && !runState.isRunning && message.trimStart().startsWith("/");
  const showCommandSuggestions = showSlashCommands || (commandMenuOpen && !hasPendingInteraction && !runState.isRunning);
  const showPatchForm = isAwaitingUser && resumeMode === "patch" && allowedActions.includes("patch") && structuredFields.length;
  const providerLabel = formatProviderLabel(llmSettings?.provider);
  const modelLabel = formatModelLabel(llmSettings?.model);
  const statusLabel = ui.statuses[runState.status] || runState.status;

  function handlePrimaryAction() {
    if (showPatchForm) {
      const slotPatch = buildSlotPatch(structuredFields, resumePatch);
      if (!hasRequiredPatchValues(structuredFields, slotPatch)) {
        onError?.(ui.errors.fillPendingParams);
        return;
      }
      onSubmitPatch(slotPatch, summarizeSlotPatch(slotPatch, ui));
      return;
    }
    onSendMessage();
  }

  function handleCommand(command) {
    setCommandMenuOpen(false);
    onSubmitCommand(command);
  }

  return (
    <section className="composer">
      <div className="composer-surface">
        <div className={`composer-stage ${showPatchForm ? "patch" : ""}`}>
          <div className={`composer-layer ${showPatchForm ? "hidden" : "visible"}`}>
            <div className="composer-input-wrap">
              <textarea
                value={message}
                onChange={(event) => onMessageChange(event.target.value)}
                placeholder={ui.composer.placeholder}
                aria-label={ui.composer.placeholder}
                disabled={runState.isRunning}
              />
              {showCommandSuggestions ? (
                <CommandMenu
                  commands={showSlashCommands ? commandSuggestions : commandItems}
                  fallbackCommands={commandItems}
                  onCommand={handleCommand}
                  ui={ui}
                />
              ) : null}
            </div>
          </div>
          <div className={`composer-layer ${showPatchForm ? "visible" : "hidden"}`}>
            <ResumePatchForm
              ui={ui}
              fields={structuredFields}
              values={resumePatch}
              onChange={onPatchChange}
              onError={onError}
            />
          </div>
        </div>
        <div className="composer-toolbar">
          <div className="composer-toolbar-left">
            <button
              className="composer-icon-button"
              type="button"
              disabled={hasPendingInteraction || runState.isRunning}
              onClick={() => setCommandMenuOpen((value) => !value)}
              title={ui.composer.quickActions}
            >
              <Plus size={20} />
            </button>
          </div>
          <div className="composer-actions">
            {runState.isRunning ? (
              <>
                {!isPauseRequested && !isCancelRequested ? (
                  <button className="composer-compact-action" type="button" onClick={onPause} title={ui.actions.pause || "Pause"}>
                    <Pause size={15} />
                  </button>
                ) : null}
                {!isCancelRequested ? (
                  <button className="composer-compact-action danger" type="button" onClick={onCancelRun} title={ui.actions.cancelRun || ui.actions.cancel}>
                    <Ban size={15} />
                  </button>
                ) : null}
              </>
            ) : null}
            {(isAwaitingUser || isAwaitingConfirmation) && allowedActions.includes("cancel") ? (
              <button className="composer-compact-action danger" type="button" onClick={onCancelPending} title={ui.actions.cancel}>
                <Ban size={15} />
              </button>
            ) : null}
            <span className="composer-model-chip" title={providerLabel}>{providerLabel}</span>
            <span className={`composer-status-dot ${runState.isRunning ? "running" : ""}`} title={statusLabel} aria-label={statusLabel} />
            <span className="composer-model-detail" title={modelLabel}>{modelLabel}</span>
            <button
              className="composer-send-button"
              type="button"
              onClick={handlePrimaryAction}
              disabled={runState.isRunning || isAwaitingConfirmation}
              title={composerLabel(runState.status, resumeMode, editableMissingSlots.length, allowedActions, ui)}
            >
              {runState.isRunning ? <Loader2 className="spin" size={18} /> : <ArrowUp size={22} />}
            </button>
          </div>
        </div>
      </div>
    </section>
  );
}

function CommandMenu({ commands, fallbackCommands, onCommand, ui }) {
  const visible = commands.length ? commands : fallbackCommands;
  return (
    <div className="command-menu">
      <div className="command-menu-title">{ui.composer.commandHint}</div>
      {visible.map((item) => {
        const Icon = item.Icon;
        return (
          <button className={item.danger ? "danger" : ""} key={item.command} onMouseDown={(event) => { event.preventDefault(); onCommand(item.command); }} title={item.description}>
            <Icon size={14} />
            <span><strong>{item.label}</strong><em>{item.description}</em></span>
            <code>{item.command}</code>
          </button>
        );
      })}
    </div>
  );
}

function ResumePatchForm({ ui, fields, values, onChange, onError }) {
  return (
    <div className="resume-form">
      <div className="resume-hint">{ui.resume.patchHint}</div>
      <div className="resume-grid">
        {fields.map((field) => (
          <ResumeField key={field.slot} field={field} value={values[field.slot] || ""} ui={ui} onChange={onChange} onError={onError} />
        ))}
      </div>
    </div>
  );
}

function ResumeField({ field, value, ui, onChange, onError }) {
  const slot = String(field.slot || "").trim();
  const schema = field.schema && typeof field.schema === "object" ? field.schema : {};
  const description = String(schema.description || "").trim();
  const type = String(schema.type || "").trim();
  const enumValues = Array.isArray(schema.enum) ? schema.enum : [];
  const isDirectory = String(schema.format || "").trim() === "directory";
  const label = humanizeSlot(slot, ui);

  async function chooseDirectory() {
    if (!isLikelyTauri()) {
      onError?.(ui.errors.filePickerDesktopOnly);
      return;
    }
    try {
      const directory = await pickDirectory();
      if (directory) onChange(slot, directory);
    } catch (err) {
      onError?.(err.message || ui.errors.directoryPickerFailed);
    }
  }

  return (
    <label>
      <span>{label}</span>
      {description ? <em className="resume-field-hint">{description}</em> : null}
      {enumValues.length ? (
        <select value={value} onChange={(event) => onChange(slot, event.target.value)}>
          <option value="">{ui.common.auto}</option>
          {enumValues.map((item) => (
            <option key={String(item)} value={String(item)}>
              {String(item)}
            </option>
          ))}
        </select>
      ) : (
        <div className="resume-field-row">
          <input
            value={value}
            onChange={(event) => onChange(slot, event.target.value)}
            placeholder={placeholderForSlot(slot, ui, type, isDirectory)}
          />
          {isDirectory ? (
            <button type="button" className="resume-dir-button" onClick={chooseDirectory} title={ui.settings?.chooseDirectory || "Choose directory"}>
              <FolderOpen size={14} />
            </button>
          ) : null}
        </div>
      )}
    </label>
  );
}

function buildSlotPatch(fields, values) {
  const patch = {};
  for (const field of fields) {
    const slot = field.slot;
    const raw = String(values[slot] || "").trim();
    if (!raw) continue;
    patch[slot] = parseSlotValue(slot, raw);
  }
  return patch;
}

function hasRequiredPatchValues(fields, patch) {
  return fields.every((field) => {
    if (!field.required) return true;
    const slot = field.slot;
    const value = patch[slot];
    if (Array.isArray(value)) return value.length > 0;
    return value != null && String(value).trim() !== "";
  });
}

function parseSlotValue(slot, raw) {
  if (slot === "output_format") return normalizeOutputFormat(raw);
  if (slot === "output_dir") return raw.trim();
  if (slot === "input_refs") return raw.split(",").map((item) => item.trim()).filter(Boolean);
  return raw;
}

function summarizeSlotPatch(slotPatch, ui) {
  const parts = Object.entries(slotPatch).map(([key, value]) => `${key}=${Array.isArray(value) ? value.join(", ") : value}`);
  return ui.resume.patchSummary.replace("{patch}", parts.join("; "));
}

function composerLabel(status, resumeMode, missingCount, allowedActions, ui) {
  if (status === "pause_requested" || status === "cancel_requested") return ui.actions.waiting;
  if (status === "awaiting_confirmation") return ui.actions.waiting;
  if (status === "awaiting_user" && resumeMode === "patch" && allowedActions.includes("patch") && missingCount) return ui.actions.submitPatch;
  if (status === "awaiting_user" && allowedActions.includes("replan")) return ui.actions.replan;
  return ui.actions.send;
}

function humanizeSlot(slot, ui) {
  return ui.slots?.[slot] || slot;
}

function placeholderForSlot(slot, ui, type, isDirectory) {
  if (isDirectory) return ui.settings.chooseDirectory || "Choose directory";
  if (slot === "output_format") return ".gpkg";
  if (type === "string") return ui.common.auto;
  return ui.common.auto;
}

function normalizeOutputFormat(value) {
  const text = String(value || "").trim().toLowerCase();
  if (!text) return "";
  return text.startsWith(".") ? text : `.${text}`;
}

function formatProviderLabel(provider) {
  const text = String(provider || "").trim();
  if (!text) return "GIS";
  return text.replace(/-/g, " ").replace(/\b\w/g, (char) => char.toUpperCase());
}

function formatModelLabel(model) {
  const text = String(model || "").trim();
  if (!text) return "auto";
  return text.replace(/^deepseek[-_]?/i, "").replace(/^gpt[-_]?/i, "");
}
