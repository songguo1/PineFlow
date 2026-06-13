"""Public API contracts for the PineFlow QGIS agent service."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from pineflow_api.config import (
    DEFAULT_QGIS_LAUNCHER,
    DEFAULT_QGIS_PREFIX_PATH,
    default_llm_base_url,
    default_llm_model,
    default_llm_provider,
)

SourceType = Literal["vector", "raster", "csv"]
ResumeAction = Literal["confirm", "reject", "patch", "cancel", "replan"]
AgentEventName = Literal[
    "session",
    "step_start",
    "command",
    "stdout",
    "stderr",
    "observe",
    "thought",
    "action",
    "tool",
    "observation",
    "warning",
    "step_complete",
    "review",
    "retry",
    "resume",
    "repair",
    "question",
    "confirmation",
    "paused",
    "cancelled",
    "completed",
    "summary",
    "failed",
]


class DataSource(BaseModel):
    alias: str = Field(..., min_length=1)
    path: str = Field(..., min_length=1)
    type: SourceType = "vector"
    crs: str = ""


class LLMConfig(BaseModel):
    provider: str = Field(default_factory=default_llm_provider)
    base_url: str = Field(default_factory=default_llm_base_url)
    model: str = Field(default_factory=default_llm_model)
    api_key: str = ""
    llm_params: dict[str, Any] = Field(default_factory=dict)


class QGISConfig(BaseModel):
    launcher: str = DEFAULT_QGIS_LAUNCHER
    prefix_path: str = DEFAULT_QGIS_PREFIX_PATH


class OutputConfig(BaseModel):
    directory: str = "data"
    format: str = "geojson"


class AgentOptions(BaseModel):
    auto_repair: bool = True
    locale: Literal["zh-CN", "en-US"] = "zh-CN"
    tool_profile: Literal["minimal", "vector_basic", "vector_analysis", "vector_raster_basic", "debug"] = "vector_raster_basic"
    tool_allow: list[str] = Field(default_factory=list)
    tool_deny: list[str] = Field(default_factory=list)
    reset_session: bool = False
    goal_contract: dict[str, Any] = Field(default_factory=dict)
    run_context: dict[str, Any] = Field(default_factory=dict)


class ResumePayload(BaseModel):
    action: ResumeAction
    slot_patch: dict[str, Any] = Field(default_factory=dict)
    message: str = ""


class QGISAgentRequest(BaseModel):
    message: str = Field(..., min_length=1)
    session_id: str = ""
    sources: list[DataSource] = Field(default_factory=list)
    llm: LLMConfig = Field(default_factory=LLMConfig)
    qgis: QGISConfig = Field(default_factory=QGISConfig)
    output: OutputConfig = Field(default_factory=OutputConfig)
    options: AgentOptions = Field(default_factory=AgentOptions)
    resume: ResumePayload | None = None


class QGISAgentEvent(BaseModel):
    event: AgentEventName
    message: str = ""
    session_id: str = ""
    run_id: str = ""
    seq: int | None = None
    step_index: int | None = None
    step_total: int | None = None
    command: str = ""
    stream: str = ""
    payload: dict[str, Any] = Field(default_factory=dict)


class QGISAgentResult(BaseModel):
    session_id: str
    status: str
    final_message: str = ""
    react_trace: list[dict[str, Any]] = Field(default_factory=list)
    state_tree: dict[str, Any] = Field(default_factory=dict)
    outputs: list[dict[str, Any]] = Field(default_factory=list)
    logs: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)
    next_question: str = ""
    issues: list[dict[str, Any]] = Field(default_factory=list)
    risks: list[dict[str, Any]] = Field(default_factory=list)
    pending_task: dict[str, Any] = Field(default_factory=dict)
    repair: dict[str, Any] = Field(default_factory=dict)
    goal_contract: dict[str, Any] = Field(default_factory=dict)
    quality_findings: list[dict[str, Any]] = Field(default_factory=list)
    file_state: dict[str, Any] = Field(default_factory=dict)
    transcript: dict[str, Any] = Field(default_factory=dict)


