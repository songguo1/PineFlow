import {
  compactDetailEntries,
  compactObjectText,
  diagnosisDisplayModel,
  selectedChoiceLabels,
  selectedChoiceText,
} from "../workflow/workflowFormatters.js";

function listFrom(value) {
  return Array.isArray(value) ? value : [];
}

function decisionTitle(kind, ui, { hasChoice = false } = {}) {
  if (kind === "clarification") return ui.decisions?.clarificationTitle || "Clarification";
  if (kind === "risk") {
    return hasChoice
      ? (ui.decisions?.userResolutionTitle || "User resolution")
      : (ui.decisions?.riskDecisionTitle || "Risk decision");
  }
  if (kind === "runtime_risk") return ui.decisions?.runtimeRiskTitle || "Runtime risk";
  if (kind === "repair") return ui.decisions?.repairTitle || "Repair decision";
  if (kind === "export") return ui.decisions?.exportTitle || "Export decision";
  if (kind === "empty_result") return ui.decisions?.emptyResultTitle || "Empty result diagnosis";
  if (kind === "quality") return ui.decisions?.qualityTitle || "Quality finding";
  return "Decision";
}

function decisionLabel(key, ui) {
  if (key === "goalSource") return ui.decisions?.goalSource || "Goal source";
  if (key === "assumptions") return ui.decisions?.assumptions || "Confirmed assumptions";
  if (key === "intent") return ui.decisions?.intent || "Intent";
  if (key === "choice") return ui.decisions?.choice || "Choice";
  if (key === "slotPatch") return ui.decisions?.slotPatch || "Slot patch";
  if (key === "decision") return ui.decisions?.decision || "Decision";
  if (key === "riskType") return ui.resume?.riskCategory || "Risk type";
  if (key === "blocking") return ui.decisions?.blocking || "Blocking";
  if (key === "affectsTrust") return ui.decisions?.affectsTrust || "Affects trust";
  if (key === "code") return ui.decisions?.code || "Code";
  if (key === "action") return ui.decisions?.action || ui.workflow?.action || "Action";
  if (key === "reason") return ui.decisions?.reason || "Reason";
  if (key === "layer") return ui.decisions?.layer || "Layer";
  if (key === "inputLayer") return ui.decisions?.inputLayer || "Input layer";
  if (key === "outputLayer") return ui.decisions?.outputLayer || "Output layer";
  if (key === "featureChange") return ui.decisions?.featureChange || "Feature count";
  if (key === "features") return ui.layers?.features || "Features";
  if (key === "path") return ui.outputs?.path || "Output path";
  if (key === "crs") return ui.layers?.crs || "CRS";
  if (key === "possibleCauses") return ui.decisions?.possibleCauses || ui.diagnosis?.possibleCauses || "Possible causes";
  if (key === "nextActions") return ui.diagnosis?.nextActions || "Suggested next steps";
  return key;
}

function normalizeDecisionRow(row, index) {
  const value = row && typeof row === "object" ? row : {};
  return {
    id: String(value.id || index),
    kind: String(value.kind || "decision"),
    status: String(value.status || value.kind || "decision"),
    severity: String(value.severity || "info"),
    title: String(value.title || value.kind || "Decision"),
    summary: String(value.summary || ""),
    details: Array.isArray(value.details)
      ? value.details
          .filter((item) => item && typeof item === "object")
          .map((item) => ({ label: String(item.label || ""), value: String(item.value || "") }))
          .filter((item) => item.label && item.value)
      : [],
  };
}

function dedupeDecisionRows(rows) {
  const seen = new Set();
  const result = [];
  for (const row of rows.map(normalizeDecisionRow)) {
    const key = `${row.kind}|${row.title}|${row.summary}|${row.status}`;
    if (seen.has(key)) continue;
    seen.add(key);
    result.push(row);
  }
  return result;
}

function rowsFromReportAudit(result, ui) {
  const payload = result && typeof result === "object" ? result : {};
  const audit = payload.report_audit && typeof payload.report_audit === "object" ? payload.report_audit : {};
  const rows = [];

  for (const item of listFrom(audit.clarification_decisions)) {
    if (!item || typeof item !== "object") continue;
    rows.push({
      id: `audit-clarification-${rows.length}`,
      kind: "clarification",
      status: item.decision || "answered",
      severity: "info",
      title: decisionTitle("clarification", ui),
      summary: item.question || item.message || "",
      details: compactDetailEntries([
        [decisionLabel("intent", ui), item.active_intent],
        [decisionLabel("choice", ui), selectedChoiceText(item)],
        [decisionLabel("slotPatch", ui), compactObjectText(item.slot_patch)],
      ]),
    });
  }

  for (const item of listFrom(audit.user_confirmations)) {
    if (!item || typeof item !== "object") continue;
    rows.push({
      id: `audit-decision-${rows.length}`,
      kind: "risk",
      status: item.decision || "resolved",
      severity: "warning",
      title: decisionTitle("risk", ui, { hasChoice: selectedChoiceLabels(item).length > 0 }),
      summary: item.message || item.risk_code || "",
      details: compactDetailEntries([
        [decisionLabel("riskType", ui), item.risk_category || item.risk_code],
        [decisionLabel("decision", ui), item.decision],
        [decisionLabel("choice", ui), selectedChoiceText(item)],
        [decisionLabel("slotPatch", ui), compactObjectText(item.slot_patch)],
      ]),
    });
  }

  for (const item of listFrom(audit.repairs)) {
    if (!item || typeof item !== "object") continue;
    const repairAudit = item.repair_audit && typeof item.repair_audit === "object" ? item.repair_audit : item;
    const inputLayer = repairAudit.input && typeof repairAudit.input === "object" ? repairAudit.input : {};
    const outputLayer = repairAudit.output && typeof repairAudit.output === "object" ? repairAudit.output : {};
    const before = repairAudit.feature_count_before;
    const after = repairAudit.feature_count_after;
    rows.push({
      id: `repair-${rows.length}`,
      kind: "repair",
      status: item.event_type === "repair.failed" ? "failed" : "completed",
      severity: item.event_type === "repair.failed" ? "error" : "info",
      title: decisionTitle("repair", ui),
      summary: item.message || repairAudit.reason || "",
      details: compactDetailEntries([
        [decisionLabel("action", ui), repairAudit.action || item.action],
        [decisionLabel("decision", ui), repairAudit.decision],
        [decisionLabel("reason", ui), repairAudit.reason],
        [decisionLabel("inputLayer", ui), inputLayer.name || inputLayer.layer_id || inputLayer.ref],
        [decisionLabel("outputLayer", ui), outputLayer.name || outputLayer.layer_id || outputLayer.ref],
        [decisionLabel("featureChange", ui), before != null || after != null ? `${before ?? "?"} -> ${after ?? "?"}` : ""],
      ]),
    });
  }

  for (const item of listFrom(audit.exports)) {
    if (!item || typeof item !== "object") continue;
    rows.push({
      id: `export-${rows.length}`,
      kind: "export",
      status: "before_export",
      severity: "info",
      title: decisionTitle("export", ui),
      summary: item.output_path || item.output_name || "",
      details: compactDetailEntries([
        [decisionLabel("layer", ui), item.layer_name || item.layer_ref || item.layer_id],
        [decisionLabel("features", ui), item.feature_count],
        [decisionLabel("path", ui), item.output_path],
        [decisionLabel("crs", ui), item.crs],
      ]),
    });
  }

  for (const item of listFrom(audit.empty_results)) {
    if (!item || typeof item !== "object") continue;
    const diagnosis = item.risk?.diagnosis && typeof item.risk.diagnosis === "object"
      ? item.risk.diagnosis
      : (item.diagnosis && typeof item.diagnosis === "object" ? item.diagnosis : {});
    const display = diagnosisDisplayModel(diagnosis, ui);
    const causes = display.causes.join("; ");
    const actions = display.actions.map((entry) => entry.label).join("; ");
    rows.push({
      id: `empty-${rows.length}`,
      kind: "empty_result",
      status: item.risk?.severity || item.severity || "warning",
      severity: item.risk?.severity || item.severity || "warning",
      title: decisionTitle("empty_result", ui),
      summary: item.message || item.code || "",
      details: compactDetailEntries([
        [decisionLabel("action", ui), item.action],
        [decisionLabel("possibleCauses", ui), causes],
        [decisionLabel("nextActions", ui), actions],
      ]),
    });
  }

  return rows;
}

function minimalFallbackRows(result, ui) {
  const payload = result && typeof result === "object" ? result : {};
  const rows = [];

  for (const risk of listFrom(payload.risks)) {
    if (!risk || typeof risk !== "object") continue;
    rows.push({
      id: `risk-${risk.code || rows.length}`,
      kind: "risk",
      status: risk.severity || "warning",
      severity: risk.severity || "warning",
      title: decisionTitle("runtime_risk", ui),
      summary: risk.message || risk.code || "",
      details: compactDetailEntries([
        [decisionLabel("riskType", ui), risk.category || risk.code],
        [decisionLabel("blocking", ui), risk.blocking === true ? "yes" : ""],
        [decisionLabel("affectsTrust", ui), risk.affects_result_trust === true ? "yes" : ""],
      ]),
    });
  }

  for (const finding of listFrom(payload.quality_findings)) {
    if (!finding || typeof finding !== "object") continue;
    rows.push({
      id: `quality-${finding.code || rows.length}`,
      kind: "quality",
      status: finding.severity || "quality",
      severity: finding.severity || "info",
      title: decisionTitle("quality", ui),
      summary: finding.message || finding.code || "",
      details: compactDetailEntries([
        [decisionLabel("code", ui), finding.code],
        [decisionLabel("blocking", ui), finding.blocking === true ? "yes" : ""],
      ]),
    });
  }

  return rows;
}

export function projectDecisionRows(result, ui) {
  const payload = result && typeof result === "object" ? result : {};
  if (Array.isArray(payload.decision_rows) && payload.decision_rows.length) {
    return dedupeDecisionRows(payload.decision_rows);
  }
  const auditRows = rowsFromReportAudit(payload, ui);
  if (auditRows.length) {
    return dedupeDecisionRows([...auditRows, ...minimalFallbackRows(payload, ui)]);
  }
  return dedupeDecisionRows(minimalFallbackRows(payload, ui));
}
