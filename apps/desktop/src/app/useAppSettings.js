import { useEffect, useState } from "react";

import {
  DEFAULT_API_BASE_URL,
  DEFAULT_QGIS_LAUNCHER,
  DEFAULT_QGIS_PREFIX_PATH,
} from "../api/apiClient.js";
import {
  clearApiKeySecret,
  getApiKeySecret,
  isLikelyTauri,
  setApiKeySecret,
} from "../shared/tauriBridge.js";
import { defaultBaseUrlForProvider, normalizeProviderValue } from "../providers/providerCatalog.js";

const SETTINGS_KEY = "qgis.agent.desktop.settings.v1";
const API_KEY_STORAGE_KEY = "qgis.agent.desktop.secret.apiKey.v1";
const DEFAULT_LLM_PROVIDER = "deepseek";
const DEFAULT_LLM_BASE_URL = "https://api.deepseek.com";
const DEFAULT_LLM_MODEL = "deepseek-v4-pro";
const SETTINGS_PRESET_VERSION = 5;

const DEFAULT_SETTINGS = {
  settingsPresetVersion: SETTINGS_PRESET_VERSION,
  apiBaseUrl: DEFAULT_API_BASE_URL,
  provider: DEFAULT_LLM_PROVIDER,
  baseUrl: DEFAULT_LLM_BASE_URL,
  model: DEFAULT_LLM_MODEL,
  qgisLauncher: DEFAULT_QGIS_LAUNCHER,
  qgisPrefixPath: DEFAULT_QGIS_PREFIX_PATH,
  outputDirectory: "data",
  outputFormat: "geojson",
  locale: "zh-CN",
  llmMaxTokens: 4096,
  llmTopP: 1,
  llmJsonMode: false,
};

export function useAppSettings() {
  const [settings, setSettings] = useState(loadSettings);
  const [apiKey, setApiKey] = useState(loadApiKey);
  const [secretReady, setSecretReady] = useState(!isLikelyTauri());

  useEffect(() => {
    localStorage.setItem(SETTINGS_KEY, JSON.stringify(settings));
  }, [settings]);

  useEffect(() => {
    let cancelled = false;
    async function hydrateApiKey() {
      if (!isLikelyTauri()) {
        setSecretReady(true);
        return;
      }
      const fallback = loadApiKey();
      try {
        const nativeValue = await getApiKeySecret();
        if (cancelled) return;
        if (nativeValue) {
          setApiKey(nativeValue);
          clearLegacyApiKeyCache();
        } else if (fallback) {
          await setApiKeySecret(fallback);
          if (cancelled) return;
          setApiKey(fallback);
          clearLegacyApiKeyCache();
        } else {
          setApiKey("");
        }
      } catch {
        setApiKey(fallback);
      } finally {
        if (!cancelled) setSecretReady(true);
      }
    }
    hydrateApiKey();
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    if (!secretReady) return;
    async function persistApiKey() {
      if (!isLikelyTauri()) {
        persistBrowserApiKey(apiKey);
        return;
      }
      try {
        if (apiKey) await setApiKeySecret(apiKey);
        else await clearApiKeySecret();
        clearLegacyApiKeyCache();
      } catch {
        persistBrowserApiKey(apiKey);
      }
    }
    persistApiKey();
  }, [apiKey, secretReady]);

function updateSetting(key, value) {
    setSettings((current) => {
      if (key !== "provider") {
        return { ...current, [key]: value };
      }
      const provider = normalizeProviderValue(value);
      if (provider === current.provider) {
        return current;
      }
      return {
        ...current,
        provider,
        baseUrl: defaultBaseUrlForProvider(provider),
        model: "",
      };
    });
  }

  function updateApiKey(value) {
    setApiKey(String(value || ""));
  }

  return { settings, updateSetting, apiKey, updateApiKey };
}

function loadSettings() {
  try {
    const stored = loadStoredSettings();
    const settings = { ...DEFAULT_SETTINGS, ...stored };
    settings.provider = normalizeProviderValue(settings.provider);
    if (stored.settingsPresetVersion !== SETTINGS_PRESET_VERSION) {
      if (!stored.provider || stored.provider === "rule") settings.provider = DEFAULT_LLM_PROVIDER;
      if (!stored.baseUrl) settings.baseUrl = DEFAULT_LLM_BASE_URL;
      if (!stored.model) settings.model = DEFAULT_LLM_MODEL;
      if (!stored.llmMaxTokens || Number(stored.llmMaxTokens) === 800) settings.llmMaxTokens = DEFAULT_SETTINGS.llmMaxTokens;
      if (stored.llmJsonMode == null || stored.llmJsonMode === true) settings.llmJsonMode = DEFAULT_SETTINGS.llmJsonMode;
      settings.qgisLauncher = DEFAULT_SETTINGS.qgisLauncher;
      settings.qgisPrefixPath = DEFAULT_SETTINGS.qgisPrefixPath;
      settings.settingsPresetVersion = SETTINGS_PRESET_VERSION;
    }
    return settings;
  } catch {
    return DEFAULT_SETTINGS;
  }
}

function loadApiKey() {
  try {
    const stored = String(localStorage.getItem(API_KEY_STORAGE_KEY) || "").trim();
    if (stored) return stored;
    return String(loadLegacyApiKey() || "");
  } catch {
    return "";
  }
}

function loadStoredSettings() {
  const stored = JSON.parse(localStorage.getItem(SETTINGS_KEY) || "{}");
  if (!stored || typeof stored !== "object" || Array.isArray(stored)) return {};
  const { apiKey: _legacyApiKey, ...settings } = stored;
  return settings;
}

function loadLegacyApiKey() {
  const stored = JSON.parse(localStorage.getItem(SETTINGS_KEY) || "{}");
  if (!stored || typeof stored !== "object" || Array.isArray(stored)) return "";
  return typeof stored.apiKey === "string" ? stored.apiKey : "";
}

function persistBrowserApiKey(value) {
  const secret = String(value || "");
  if (secret) {
    localStorage.setItem(API_KEY_STORAGE_KEY, secret);
    return;
  }
  localStorage.removeItem(API_KEY_STORAGE_KEY);
}

function clearLegacyApiKeyCache() {
  localStorage.removeItem(API_KEY_STORAGE_KEY);
  try {
    const stored = JSON.parse(localStorage.getItem(SETTINGS_KEY) || "{}");
    if (!stored || typeof stored !== "object" || Array.isArray(stored) || !("apiKey" in stored)) return;
    const { apiKey: _legacyApiKey, ...settings } = stored;
    localStorage.setItem(SETTINGS_KEY, JSON.stringify(settings));
  } catch {
    // Ignore malformed local settings; future writes will normalize them.
  }
}
