import deepseekIcon from "../assets/deepseek.svg";
import glmIcon from "../assets/glm.svg";
import openaiIcon from "../assets/openai.svg";
import qwenIcon from "../assets/qwen.svg";

export const PROVIDER_OPTIONS = [
  { value: "deepseek", labelKey: "deepseek", iconSrc: deepseekIcon },
  { value: "qwen", labelKey: "qwen", iconSrc: qwenIcon },
  { value: "glm", labelKey: "glm", iconSrc: glmIcon },
  { value: "openai-compatible", labelKey: "openaiCompatible", iconSrc: openaiIcon },
];

const PROVIDER_BASE_URLS = {
  deepseek: "https://api.deepseek.com",
  qwen: "https://dashscope.aliyuncs.com/compatible-mode/v1",
  glm: "https://open.bigmodel.cn/api/paas/v4",
  "openai-compatible": "https://api.openai.com/v1",
};

export function normalizeProviderValue(value) {
  const normalized = String(value || "").trim().toLowerCase();
  if (normalized === "openai" || normalized === "openrouter") return "openai-compatible";
  if (PROVIDER_OPTIONS.some((option) => option.value === normalized)) return normalized;
  return PROVIDER_OPTIONS[0].value;
}

export function providerOptionFor(value) {
  const normalized = normalizeProviderValue(value);
  return PROVIDER_OPTIONS.find((option) => option.value === normalized) || PROVIDER_OPTIONS[0];
}

export function defaultBaseUrlForProvider(value) {
  const normalized = normalizeProviderValue(value);
  return PROVIDER_BASE_URLS[normalized] || PROVIDER_BASE_URLS.deepseek;
}
