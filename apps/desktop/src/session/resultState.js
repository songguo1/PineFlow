export function normalizeResult(value) {
  const result = value && typeof value === "object" ? value : {};
  const stateTree = normalizeStateTree(result.state_tree || {});
  const fileState = normalizeFileState(result.file_state || {});
  return {
    session_id: String(result.session_id || ""),
    status: String(result.status || "idle"),
    final_message: String(result.final_message || ""),
    completion_summary: String(result.completion_summary || ""),
    completion_delivery: result.completion_delivery && typeof result.completion_delivery === "object" ? result.completion_delivery : {},
    state_tree: stateTree,
    outputs: outputsFromArtifacts(fileState.artifacts) || explicitOutputs(result.outputs),
    logs: Array.isArray(result.logs) ? result.logs : [],
    errors: Array.isArray(result.errors) ? result.errors : [],
    next_question: String(result.next_question || ""),
    issues: Array.isArray(result.issues) ? result.issues : [],
    risks: Array.isArray(result.risks) ? result.risks : [],
    pending_task: result.pending_task && typeof result.pending_task === "object" ? result.pending_task : {},
    repair: result.repair && typeof result.repair === "object" ? result.repair : {},
    quality_findings: Array.isArray(result.quality_findings) ? result.quality_findings : [],
    decision_rows: Array.isArray(result.decision_rows) ? result.decision_rows : [],
    report_audit: result.report_audit && typeof result.report_audit === "object" ? result.report_audit : {},
    transcript: normalizeTranscript(result.transcript || {}),
    display_transcript: result.display_transcript && typeof result.display_transcript === "object"
      ? normalizeTranscript(result.display_transcript)
      : {},
    // Legacy compatibility: retained for old session reads. New main path uses
    // visibleRunSnapshot instead of active_run_result for execution state.
    active_run_result: result.active_run_result && typeof result.active_run_result === "object" ? result.active_run_result : {},
    file_state: fileState,
    debug: {
      logs: Array.isArray(result.logs) ? result.logs : [],
    },
  };
}

function normalizeTranscript(value) {
  const transcript = value && typeof value === "object" ? value : {};
  return {
    version: Number(transcript.version || 0),
    timeline: Array.isArray(transcript.timeline) ? transcript.timeline.filter((item) => item && typeof item === "object") : [],
  };
}

function normalizeStateTree(value) {
  const tree = value && typeof value === "object" ? value : {};
  return {
    layers: Array.isArray(tree.layers) ? tree.layers : [],
    aliases: tree.aliases && typeof tree.aliases === "object" ? tree.aliases : {},
  };
}

function normalizeFileState(value) {
  const state = value && typeof value === "object" ? value : {};
  return {
    version: Number(state.version || 0),
    manifest_path: String(state.manifest_path || ""),
    event_log_path: String(state.event_log_path || ""),
    steps_path: String(state.steps_path || ""),
    state_tree_path: String(state.state_tree_path || ""),
    pending_path: String(state.pending_path || ""),
    artifact_index_path: String(state.artifact_index_path || ""),
    layers_dir: String(state.layers_dir || ""),
    artifacts: Array.isArray(state.artifacts) ? state.artifacts : [],
    event_count: Number(state.event_count || 0),
    updated_at: String(state.updated_at || ""),
  };
}

function outputsFromArtifacts(artifacts, { includeIntermediate = false } = {}) {
  const roles = includeIntermediate ? ["final", "report", "intermediate"] : ["final", "report"];
  const outputs = artifacts
    .filter((artifact) => roles.includes(String(artifact?.role || "")))
    .filter((artifact) => artifact?.path)
    .map((artifact) => ({
      artifact_id: artifact.artifact_id || "",
      role: artifact.role || "",
      layer_id: artifact.layer_id || "",
      name: artifact.name || "",
      path: artifact.path || "",
      kind: artifact.kind || "",
      algorithm_id: artifact.algorithm_id || "",
      display_summary: artifact.display_summary || "",
      crs: artifact.crs || "",
      geometry_type: artifact.geometry_type || "",
      feature_count: artifact.feature_count ?? null,
      row_count: artifact.row_count ?? null,
    }));
  return outputs.length ? outputs : null;
}

function explicitOutputs(value) {
  return Array.isArray(value) ? value.filter((item) => item && typeof item === "object") : [];
}

export function eventLabel(event) {
  return String(event?.event || event?.step || "event");
}
