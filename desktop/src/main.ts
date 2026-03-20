import "./styles.css";

import { invoke } from "@tauri-apps/api/core";

import { InfostreamApi } from "./api";
import { createLogger } from "./logger";
import type { ApiEnvelope, LlmConfigPayload, RunEvent, SourceRecord } from "./types";

// This file renders the desktop UI, wires user actions to API calls,
// and standardizes section-level status feedback with optional JSON details.

type SectionId =
  | "health"
  | "sources"
  | "interests"
  | "llm"
  | "schedule"
  | "runs"
  | "digest"
  | "items"
  | "events";

type ProviderChoice = "stub_local" | "stub_cloud" | "openai_compatible";

const EVENT_LIMIT = 50;
const UI_LOGGER = createLogger("desktop.ui");

function formatJson(value: unknown): string {
  return JSON.stringify(value, null, 2);
}

function escapeHtml(input: string): string {
  return input
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/\"/g, "&quot;")
    .replace(/'/g, "&#039;");
}

function getElement<T extends HTMLElement>(id: string): T {
  const element = document.getElementById(id);
  if (!element) {
    throw new Error(`Missing required element: ${id}`);
  }
  return element as T;
}

function setSectionStatus(sectionId: SectionId, ok: boolean | null, message: string): void {
  const status = getElement<HTMLParagraphElement>(`${sectionId}-status`);
  status.textContent = message;
  status.classList.remove("status-ok", "status-error", "status-info");
  if (ok === true) {
    status.classList.add("status-ok");
  } else if (ok === false) {
    status.classList.add("status-error");
  } else {
    status.classList.add("status-info");
  }
}

function setSectionJson(sectionId: SectionId, value: unknown): void {
  const pre = getElement<HTMLPreElement>(`${sectionId}-json`);
  pre.textContent = typeof value === "string" ? value : formatJson(value);
}

function reportEnvelope(sectionId: SectionId, payload: ApiEnvelope<unknown>, action: string = sectionId): void {
  if (payload.ok) {
    setSectionStatus(sectionId, true, `OK: ${payload.message}`);
    UI_LOGGER.info("ui_action_succeeded", "UI action succeeded", {
      section: sectionId,
      action,
      message: payload.message
    });
  } else {
    const code = payload.error_code ?? "UNKNOWN_ERROR";
    setSectionStatus(sectionId, false, `Failed (${code}): ${payload.message}`);
    UI_LOGGER.warn("ui_action_failed", "UI action failed", {
      section: sectionId,
      action,
      error_code: code,
      message: payload.message
    });
  }
  setSectionJson(sectionId, payload);
}

function reportRuntimeError(sectionId: SectionId, label: string, error: unknown): void {
  setSectionStatus(sectionId, false, `Failed: ${label}`);
  UI_LOGGER.error("ui_runtime_error", "UI runtime error", {
    section: sectionId,
    label,
    error: String(error)
  });
  setSectionJson(sectionId, {
    label,
    error: String(error)
  });
}

// This function centralizes button busy-state visuals so hover/press/loading
// behavior can be tuned from one place later.
function visualFeedback(button: HTMLButtonElement, busy: boolean): void {
  button.disabled = busy;
  button.classList.toggle("is-busy", busy);
}

function wireVisualFeedback(root: ParentNode): void {
  const buttons = root.querySelectorAll("button");
  buttons.forEach((node) => {
    const button = node as HTMLButtonElement;
    const clearPressed = () => button.classList.remove("is-pressed");
    button.addEventListener("pointerdown", () => button.classList.add("is-pressed"));
    button.addEventListener("pointerup", clearPressed);
    button.addEventListener("pointerleave", clearPressed);
    button.addEventListener("blur", clearPressed);
  });
}

async function withButtonBusy(button: HTMLButtonElement, task: () => Promise<void>): Promise<void> {
  visualFeedback(button, true);
  try {
    await task();
  } finally {
    visualFeedback(button, false);
  }
}

function providerFromLlm(llm: {
  name: string;
  mode: "local" | "cloud";
  unique_id: string;
  config: Record<string, unknown>;
}): ProviderChoice {
  const normalizedId = llm.unique_id.toLowerCase();
  const normalizedName = llm.name.toLowerCase();
  if (normalizedId === "stub-cloud" || (normalizedName.startsWith("stub") && llm.mode === "cloud")) {
    return "stub_cloud";
  }
  if (normalizedId === "stub-local" || normalizedId === "stub" || normalizedName.startsWith("stub")) {
    return "stub_local";
  }
  return "openai_compatible";
}

function syncLlmProviderUi(provider: ProviderChoice, preserveExisting: boolean): void {
  const modeInput = getElement<HTMLInputElement>("llm-mode");
  const nameInput = getElement<HTMLInputElement>("llm-name");
  const uniqueIdInput = getElement<HTMLInputElement>("llm-unique-id");
  const openAiFields = getElement<HTMLDivElement>("llm-openai-fields");

  if (provider === "stub_local") {
    modeInput.value = "local";
    nameInput.value = "stub-local";
    uniqueIdInput.value = "stub-local";
    nameInput.readOnly = true;
    uniqueIdInput.readOnly = true;
    openAiFields.hidden = true;
    return;
  }

  if (provider === "stub_cloud") {
    modeInput.value = "cloud";
    nameInput.value = "stub-cloud";
    uniqueIdInput.value = "stub-cloud";
    nameInput.readOnly = true;
    uniqueIdInput.readOnly = true;
    openAiFields.hidden = true;
    return;
  }

  nameInput.readOnly = false;
  uniqueIdInput.readOnly = false;
  if (!preserveExisting && !nameInput.value.startsWith("openai")) {
    nameInput.value = "openai-compatible";
  }
  if (!preserveExisting && (!uniqueIdInput.value || uniqueIdInput.value.startsWith("stub"))) {
    uniqueIdInput.value = "openai-compatible";
  }
  modeInput.value = "cloud";
  openAiFields.hidden = false;
}

function applyLlmToForm(llm: {
  name: string;
  mode: "local" | "cloud";
  unique_id: string;
  config: Record<string, unknown>;
}): void {
  getElement<HTMLInputElement>("llm-name").value = llm.name;
  getElement<HTMLInputElement>("llm-mode").value = llm.mode;
  getElement<HTMLInputElement>("llm-unique-id").value = llm.unique_id;

  const provider = providerFromLlm(llm);
  getElement<HTMLSelectElement>("llm-provider").value = provider;
  syncLlmProviderUi(provider, true);

  const config = llm.config;
  getElement<HTMLInputElement>("llm-openai-base-url").value = String(
    config.base_url ?? "https://api.openai.com/v1"
  );
  getElement<HTMLInputElement>("llm-openai-api-key").value = String(config.api_key ?? "");
  getElement<HTMLInputElement>("llm-openai-model").value = String(config.model ?? "");
  getElement<HTMLInputElement>("llm-openai-organization").value = String(config.organization ?? "");
  getElement<HTMLInputElement>("llm-openai-timeout").value = String(config.timeout_seconds ?? "30");
  getElement<HTMLInputElement>("llm-openai-temperature").value = String(config.temperature ?? "0.1");
}

function parseOptionalNumber(raw: string, fieldName: string): number | undefined {
  if (!raw.trim()) {
    return undefined;
  }
  const value = Number.parseFloat(raw);
  if (Number.isNaN(value)) {
    throw new Error(`${fieldName} must be a valid number`);
  }
  return value;
}

function buildLlmPayloadFromForm(): LlmConfigPayload {
  const provider = getElement<HTMLSelectElement>("llm-provider").value as ProviderChoice;
  const name = getElement<HTMLInputElement>("llm-name").value.trim();
  const mode = getElement<HTMLInputElement>("llm-mode").value.trim() as "local" | "cloud";
  const uniqueId = getElement<HTMLInputElement>("llm-unique-id").value.trim();

  if (!name || !uniqueId || (mode !== "local" && mode !== "cloud")) {
    throw new Error("LLM name, mode, and unique ID are required");
  }

  const config: Record<string, unknown> = {};
  if (provider === "openai_compatible") {
    const baseUrl = getElement<HTMLInputElement>("llm-openai-base-url").value.trim();
    const apiKey = getElement<HTMLInputElement>("llm-openai-api-key").value.trim();
    const model = getElement<HTMLInputElement>("llm-openai-model").value.trim();
    const organization = getElement<HTMLInputElement>("llm-openai-organization").value.trim();
    const timeoutSeconds = parseOptionalNumber(
      getElement<HTMLInputElement>("llm-openai-timeout").value,
      "timeout_seconds"
    );
    const temperature = parseOptionalNumber(
      getElement<HTMLInputElement>("llm-openai-temperature").value,
      "temperature"
    );

    if (!apiKey || !model) {
      throw new Error("OpenAI-compatible provider requires API key and model");
    }

    config.base_url = baseUrl || "https://api.openai.com/v1";
    config.api_key = apiKey;
    config.model = model;
    if (organization) {
      config.organization = organization;
    }
    if (timeoutSeconds !== undefined) {
      config.timeout_seconds = timeoutSeconds;
    }
    if (temperature !== undefined) {
      config.temperature = temperature;
    }
  }

  return {
    name,
    mode,
    unique_id: uniqueId,
    config
  };
}

type SourceView = SourceRecord & {
  loaded: boolean;
};

function sourceRows(sources: SourceView[]): string {
  return sources
    .map(
      (source) => `
      <tr>
        <td>${escapeHtml(source.uid)}</td>
        <td>${escapeHtml(source.name)}</td>
        <td>${escapeHtml(source.version)}</td>
        <td>${source.enabled ? "yes" : "no"}</td>
        <td>${source.loaded ? "yes" : "no"}</td>
        <td>${escapeHtml(source.platforms.join(", "))}</td>
        <td class="table-actions">
          <button data-source-action="enable" data-uid="${escapeHtml(source.uid)}">Enable</button>
          <button class="secondary" data-source-action="disable" data-uid="${escapeHtml(source.uid)}">Disable</button>
          <button class="secondary" data-source-action="delete" data-uid="${escapeHtml(source.uid)}">Uninstall</button>
        </td>
      </tr>
    `
    )
    .join("");
}

async function renderSources(api: InfostreamApi): Promise<void> {
  UI_LOGGER.debug("sources_refresh_started", "Refreshing source table");
  const installedPayload = await api.getInstalledSources();
  if (!installedPayload.ok || !installedPayload.data) {
    reportEnvelope("sources", installedPayload as ApiEnvelope<unknown>);
    return;
  }

  const enabledPayload = await api.getEnabledSources();
  if (!enabledPayload.ok || !enabledPayload.data) {
    reportEnvelope("sources", enabledPayload as ApiEnvelope<unknown>);
    return;
  }

  const enabledUids = new Set(enabledPayload.data.sources.map((source) => source.uid));
  const merged = installedPayload.data.sources.map((source) => ({
    ...source,
    loaded: enabledUids.has(source.uid)
  }));

  const showEnabledOnly = getElement<HTMLInputElement>("sources-show-enabled-only").checked;
  const filtered = showEnabledOnly ? merged.filter((source) => source.enabled) : merged;

  const tbody = getElement<HTMLTableSectionElement>("sources-tbody");
  tbody.innerHTML = sourceRows(filtered);
  const summary = getElement<HTMLSpanElement>("sources-summary");
  summary.textContent = `${merged.length} installed, ${enabledUids.size} enabled`;

  setSectionStatus("sources", true, "OK: Sources loaded");
  UI_LOGGER.info("sources_refresh_completed", "Source table refreshed", {
    installed_count: merged.length,
    enabled_count: enabledUids.size,
    rendered_count: filtered.length
  });
  setSectionJson("sources", {
    installed: installedPayload,
    enabled: enabledPayload,
    rendered_count: filtered.length
  });
}

function appendEvent(events: RunEvent[], event: RunEvent): void {
  events.unshift(event);
  if (events.length > EVENT_LIMIT) {
    events.length = EVENT_LIMIT;
  }

  const list = getElement<HTMLUListElement>("events-list");
  list.innerHTML = events
    .map((entry) => {
      const timestamp = escapeHtml(entry.occurred_at);
      const eventType = escapeHtml(entry.type);
      const status = escapeHtml(entry.status);
      const runId = escapeHtml(entry.run_id);
      return `<li><code>${timestamp}</code> <strong>${eventType}</strong> run=<code>${runId}</code> status=${status}</li>`;
    })
    .join("");

  setSectionJson("events", { events });
}

async function pickSourceDirectory(): Promise<string | null> {
  UI_LOGGER.debug("source_picker_open", "Opening source directory picker");
  const pickedPath = await invoke<string | null>("pick_source_directory");
  if (!pickedPath) {
    UI_LOGGER.info("source_picker_cancelled", "Source directory picker cancelled");
    return null;
  }
  UI_LOGGER.info("source_picker_selected", "Source directory selected", { selected_path: pickedPath });
  return pickedPath;
}

async function bootstrap(): Promise<void> {
  UI_LOGGER.info("bootstrap_started", "Desktop bootstrap started");
  const app = getElement<HTMLDivElement>("app");
  app.innerHTML = `
    <section class="panel">
      <h1>Infostream Desktop <span id="base-url" class="badge">starting...</span></h1>
      <div class="controls">
        <button id="health-btn">Check health</button>
      </div>
      <p id="health-status" class="status-line status-info">Waiting for action</p>
      <details class="json-details">
        <summary>Show JSON</summary>
        <pre id="health-json" class="json-output"></pre>
      </details>
    </section>

    <section class="panel">
      <h2>Sources <span id="sources-summary" class="badge">0 installed</span></h2>
      <div class="controls">
        <input id="source-path" placeholder="Select source directory" readonly />
        <button class="secondary" id="source-pick">Browse...</button>
        <button id="source-install">Install source</button>
        <button class="secondary" id="source-refresh">Refresh list</button>
      </div>
      <label class="checkbox-inline">
        <input id="sources-show-enabled-only" type="checkbox" /> Show enabled only
      </label>
      <table class="table">
        <thead>
          <tr>
            <th>UID</th>
            <th>Name</th>
            <th>Version</th>
            <th>Enabled</th>
            <th>Loaded</th>
            <th>Platforms</th>
            <th>Actions</th>
          </tr>
        </thead>
        <tbody id="sources-tbody"></tbody>
      </table>
      <p id="sources-status" class="status-line status-info">Waiting for action</p>
      <details class="json-details">
        <summary>Show JSON</summary>
        <pre id="sources-json" class="json-output"></pre>
      </details>
    </section>

    <section class="grid">
      <div class="panel">
        <h2>Interests</h2>
        <textarea id="interests-text" placeholder="Comma-separated or freeform interests"></textarea>
        <div class="controls">
          <button id="interests-load">Load</button>
          <button id="interests-save">Save</button>
        </div>
        <p id="interests-status" class="status-line status-info">Waiting for action</p>
        <details class="json-details">
          <summary>Show JSON</summary>
          <pre id="interests-json" class="json-output"></pre>
        </details>
      </div>

      <div class="panel">
        <h2>LLM</h2>
        <div class="field-grid">
          <label>Provider
            <select id="llm-provider">
              <option value="stub_local">Stub (Local)</option>
              <option value="stub_cloud">Stub (Cloud)</option>
              <option value="openai_compatible">OpenAI-Compatible</option>
            </select>
          </label>
          <label>Name
            <input id="llm-name" placeholder="provider name" />
          </label>
          <label>Mode
            <input id="llm-mode" placeholder="local|cloud" readonly />
          </label>
          <label>Unique ID
            <input id="llm-unique-id" placeholder="provider unique id" />
          </label>
        </div>

        <div id="llm-openai-fields" class="field-grid">
          <label>Base URL
            <input id="llm-openai-base-url" placeholder="https://api.openai.com/v1" />
          </label>
          <label>API Key
            <input id="llm-openai-api-key" type="password" placeholder="sk-..." />
          </label>
          <label>Model
            <input id="llm-openai-model" placeholder="gpt-4o-mini" />
          </label>
          <label>Organization (optional)
            <input id="llm-openai-organization" placeholder="org_..." />
          </label>
          <label>Timeout Seconds
            <input id="llm-openai-timeout" type="number" step="1" min="1" placeholder="30" />
          </label>
          <label>Temperature
            <input id="llm-openai-temperature" type="number" step="0.1" min="0" max="2" placeholder="0.1" />
          </label>
        </div>

        <div class="controls">
          <button id="llm-load">Load</button>
          <button id="llm-save">Save</button>
        </div>
        <p id="llm-status" class="status-line status-info">Waiting for action</p>
        <details class="json-details">
          <summary>Show JSON</summary>
          <pre id="llm-json" class="json-output"></pre>
        </details>
      </div>
    </section>

    <section class="grid">
      <div class="panel">
        <h2>Schedule</h2>
        <div class="field-grid">
          <label>Next schedule (RFC3339)
            <input id="schedule-next" placeholder="2026-03-02T12:00:00Z" />
          </label>
          <label class="checkbox-inline">
            <input id="schedule-run-every" type="checkbox" /> Run every
          </label>
          <label>Spacing seconds
            <input id="schedule-spacing" type="number" placeholder="3600" value="3600" />
          </label>
        </div>
        <div class="controls">
          <button id="schedule-save">Save schedule</button>
        </div>
        <p id="schedule-status" class="status-line status-info">Waiting for action</p>
        <details class="json-details">
          <summary>Show JSON</summary>
          <pre id="schedule-json" class="json-output"></pre>
        </details>
      </div>

      <div class="panel">
        <h2>Runs</h2>
        <div class="controls">
          <button id="run-now">Run digest now</button>
          <button class="secondary" id="run-latest">Load latest run</button>
          <button class="secondary" id="run-cancel">Cancel run</button>
        </div>
        <div class="controls">
          <input id="run-id" placeholder="run_id" />
          <button class="secondary" id="run-get">Load run by ID</button>
        </div>
        <p id="runs-status" class="status-line status-info">Waiting for action</p>
        <details class="json-details">
          <summary>Show JSON</summary>
          <pre id="runs-json" class="json-output"></pre>
        </details>
      </div>
    </section>

    <section class="grid">
      <div class="panel">
        <h2>Digest</h2>
        <div class="controls">
          <button id="digest-current">Current digest</button>
          <button class="secondary" id="digest-history">Digest history</button>
        </div>
        <p id="digest-status" class="status-line status-info">Waiting for action</p>
        <details class="json-details">
          <summary>Show JSON</summary>
          <pre id="digest-json" class="json-output"></pre>
        </details>
      </div>

      <div class="panel">
        <h2>Citations</h2>
        <div class="controls">
          <input id="items-refs" placeholder="item:a,item:b" />
          <button id="items-fetch">Fetch by refs</button>
        </div>
        <p id="items-status" class="status-line status-info">Waiting for action</p>
        <details class="json-details">
          <summary>Show JSON</summary>
          <pre id="items-json" class="json-output"></pre>
        </details>
      </div>
    </section>

    <section class="panel">
      <h2>Live Events (SSE)</h2>
      <p id="events-status" class="status-line status-info">Connecting...</p>
      <ul id="events-list" class="events-list"></ul>
      <details class="json-details">
        <summary>Show JSON</summary>
        <pre id="events-json" class="json-output"></pre>
      </details>
    </section>
  `;

  wireVisualFeedback(app);

  const baseUrl = await invoke<string>("bootstrap_backend");
  UI_LOGGER.info("backend_bootstrapped", "Backend bootstrap completed", { base_url: baseUrl });
  getElement<HTMLSpanElement>("base-url").textContent = baseUrl;

  const api = new InfostreamApi(baseUrl);
  let latestRunId = "";
  const events: RunEvent[] = [];

  // Source picker and source table controls.
  getElement<HTMLButtonElement>("source-pick").addEventListener("click", async (event) => {
    const button = event.currentTarget as HTMLButtonElement;
    await withButtonBusy(button, async () => {
      const pickedPath = await pickSourceDirectory();
      if (!pickedPath) {
        setSectionStatus("sources", null, "No directory selected");
        UI_LOGGER.debug("source_pick_no_selection", "No source directory was selected");
        return;
      }
      getElement<HTMLInputElement>("source-path").value = pickedPath;
      setSectionStatus("sources", true, "OK: Selected source directory");
      setSectionJson("sources", { selected_path: pickedPath });
    });
  });

  getElement<HTMLButtonElement>("source-install").addEventListener("click", async (event) => {
    const button = event.currentTarget as HTMLButtonElement;
    await withButtonBusy(button, async () => {
      const pathInput = getElement<HTMLInputElement>("source-path");
      const path = pathInput.value.trim();
      if (!path) {
        setSectionStatus("sources", false, "Failed: source path is required");
        UI_LOGGER.warn("source_install_missing_path", "Source install attempted without path");
        return;
      }

      const payload = await api.installSource(path);
      reportEnvelope("sources", payload as ApiEnvelope<unknown>, "source_install");
      if (payload.ok) {
        await renderSources(api);
      }
    });
  });

  getElement<HTMLButtonElement>("source-refresh").addEventListener("click", async (event) => {
    const button = event.currentTarget as HTMLButtonElement;
    await withButtonBusy(button, async () => {
      await renderSources(api);
    });
  });

  getElement<HTMLInputElement>("sources-show-enabled-only").addEventListener("change", async () => {
    try {
      await renderSources(api);
    } catch (error) {
      reportRuntimeError("sources", "Unable to refresh source table", error);
    }
  });

  getElement<HTMLTableSectionElement>("sources-tbody").addEventListener("click", async (event) => {
    const target = event.target as HTMLElement;
    if (!(target instanceof HTMLButtonElement)) {
      return;
    }

    const action = target.getAttribute("data-source-action");
    const uid = target.getAttribute("data-uid");
    if (!action || !uid) {
      return;
    }

    await withButtonBusy(target, async () => {
      let payload: ApiEnvelope<unknown>;
      if (action === "enable") {
        payload = await api.enableSource(uid);
      } else if (action === "disable") {
        payload = await api.disableSource(uid);
      } else {
        payload = await api.uninstallSource(uid);
      }

      reportEnvelope("sources", payload, `source_${action}`);
      if (payload.ok) {
        await renderSources(api);
      }
    });
  });

  // Health actions.
  getElement<HTMLButtonElement>("health-btn").addEventListener("click", async (event) => {
    const button = event.currentTarget as HTMLButtonElement;
    await withButtonBusy(button, async () => {
      const payload = await api.getHealth();
      reportEnvelope("health", payload as ApiEnvelope<unknown>, "health_check");
    });
  });

  // Interests actions.
  getElement<HTMLButtonElement>("interests-load").addEventListener("click", async (event) => {
    const button = event.currentTarget as HTMLButtonElement;
    await withButtonBusy(button, async () => {
      const payload = await api.getInterests();
      if (payload.ok && payload.data) {
        getElement<HTMLTextAreaElement>("interests-text").value = payload.data.interests;
      }
      reportEnvelope("interests", payload as ApiEnvelope<unknown>, "interests_load");
    });
  });

  getElement<HTMLButtonElement>("interests-save").addEventListener("click", async (event) => {
    const button = event.currentTarget as HTMLButtonElement;
    await withButtonBusy(button, async () => {
      const interests = getElement<HTMLTextAreaElement>("interests-text").value;
      const payload = await api.putInterests(interests);
      reportEnvelope("interests", payload as ApiEnvelope<unknown>, "interests_save");
    });
  });

  // LLM provider actions.
  getElement<HTMLSelectElement>("llm-provider").addEventListener("change", (event) => {
    const provider = (event.currentTarget as HTMLSelectElement).value as ProviderChoice;
    syncLlmProviderUi(provider, false);
  });

  getElement<HTMLButtonElement>("llm-load").addEventListener("click", async (event) => {
    const button = event.currentTarget as HTMLButtonElement;
    await withButtonBusy(button, async () => {
      const payload = await api.getCurrentLlm();
      if (payload.ok && payload.data) {
        applyLlmToForm(payload.data);
      }
      reportEnvelope("llm", payload as ApiEnvelope<unknown>, "llm_load");
    });
  });

  getElement<HTMLButtonElement>("llm-save").addEventListener("click", async (event) => {
    const button = event.currentTarget as HTMLButtonElement;
    await withButtonBusy(button, async () => {
      try {
        const payload = buildLlmPayloadFromForm();
        const response = await api.putCurrentLlm(payload);
        reportEnvelope("llm", response as ApiEnvelope<unknown>, "llm_save");
      } catch (error) {
        reportRuntimeError("llm", "Invalid LLM form data", error);
      }
    });
  });

  // Schedule actions.
  getElement<HTMLButtonElement>("schedule-save").addEventListener("click", async (event) => {
    const button = event.currentTarget as HTMLButtonElement;
    await withButtonBusy(button, async () => {
      const schedule = getElement<HTMLInputElement>("schedule-next").value.trim();
      const runEvery = getElement<HTMLInputElement>("schedule-run-every").checked;
      const spacing = Number.parseInt(getElement<HTMLInputElement>("schedule-spacing").value, 10);
      const payload = await api.putSchedule({
        schedule,
        run_every: runEvery,
        spacing
      });
      reportEnvelope("schedule", payload as ApiEnvelope<unknown>, "schedule_save");
    });
  });

  // Run actions including cancel.
  getElement<HTMLButtonElement>("run-now").addEventListener("click", async (event) => {
    const button = event.currentTarget as HTMLButtonElement;
    await withButtonBusy(button, async () => {
      const payload = await api.runDigest();
      if (payload.ok && payload.data) {
        latestRunId = payload.data.run_id;
        getElement<HTMLInputElement>("run-id").value = payload.data.run_id;
      }
      reportEnvelope("runs", payload as ApiEnvelope<unknown>, "run_now");
    });
  });

  getElement<HTMLButtonElement>("run-latest").addEventListener("click", async (event) => {
    const button = event.currentTarget as HTMLButtonElement;
    await withButtonBusy(button, async () => {
      const payload = await api.getLatestRun();
      if (payload.ok && payload.data) {
        latestRunId = payload.data.run.run_id;
        getElement<HTMLInputElement>("run-id").value = latestRunId;
      }
      reportEnvelope("runs", payload as ApiEnvelope<unknown>, "run_latest");
    });
  });

  getElement<HTMLButtonElement>("run-get").addEventListener("click", async (event) => {
    const button = event.currentTarget as HTMLButtonElement;
    await withButtonBusy(button, async () => {
      const runId = getElement<HTMLInputElement>("run-id").value.trim();
      if (!runId) {
        setSectionStatus("runs", false, "Failed: run_id is required");
        UI_LOGGER.warn("run_get_missing_id", "Run lookup attempted without run_id");
        return;
      }
      const payload = await api.getRun(runId);
      reportEnvelope("runs", payload as ApiEnvelope<unknown>, "run_get");
    });
  });

  getElement<HTMLButtonElement>("run-cancel").addEventListener("click", async (event) => {
    const button = event.currentTarget as HTMLButtonElement;
    await withButtonBusy(button, async () => {
      const manualRunId = getElement<HTMLInputElement>("run-id").value.trim();
      const runId = manualRunId || latestRunId;
      if (!runId) {
        setSectionStatus("runs", false, "Failed: provide run_id or load latest run first");
        UI_LOGGER.warn("run_cancel_missing_id", "Run cancel attempted without run_id");
        return;
      }
      const payload = await api.cancelRun(runId);
      reportEnvelope("runs", payload as ApiEnvelope<unknown>, "run_cancel");
    });
  });

  // Digest actions.
  getElement<HTMLButtonElement>("digest-current").addEventListener("click", async (event) => {
    const button = event.currentTarget as HTMLButtonElement;
    await withButtonBusy(button, async () => {
      const payload = await api.getCurrentDigest();
      reportEnvelope("digest", payload as ApiEnvelope<unknown>, "digest_current");
    });
  });

  getElement<HTMLButtonElement>("digest-history").addEventListener("click", async (event) => {
    const button = event.currentTarget as HTMLButtonElement;
    await withButtonBusy(button, async () => {
      const payload = await api.getDigestHistory();
      reportEnvelope("digest", payload as ApiEnvelope<unknown>, "digest_history");
    });
  });

  // Citation lookup actions.
  getElement<HTMLButtonElement>("items-fetch").addEventListener("click", async (event) => {
    const button = event.currentTarget as HTMLButtonElement;
    await withButtonBusy(button, async () => {
      const refs = getElement<HTMLInputElement>("items-refs")
        .value.split(",")
        .map((entry) => entry.trim())
        .filter((entry) => entry.length > 0);
      const payload = await api.getItems(refs);
      reportEnvelope("items", payload as ApiEnvelope<unknown>, "items_fetch");
    });
  });

  // Live event stream subscription and display.
  setSectionStatus("events", null, "Connecting to event stream...");
  const stream = api.openEventsStream(
    (eventPayload) => {
      setSectionStatus("events", true, "OK: Connected to event stream");
      UI_LOGGER.debug("events_stream_message", "Received SSE event", {
        type: eventPayload.type,
        run_id: eventPayload.run_id,
        status: eventPayload.status
      });
      appendEvent(events, eventPayload);
    },
    () => {
      setSectionStatus("events", false, "Failed: Event stream disconnected, retrying...");
      UI_LOGGER.warn("events_stream_disconnected", "Events stream disconnected");
    }
  );

  window.addEventListener("beforeunload", () => {
    stream.close();
  });

  syncLlmProviderUi("stub_local", false);
  const initialLlm = await api.getCurrentLlm();
  if (initialLlm.ok && initialLlm.data) {
    applyLlmToForm(initialLlm.data);
    reportEnvelope("llm", initialLlm as ApiEnvelope<unknown>, "llm_initial_load");
  } else if (!initialLlm.ok && initialLlm.error_code === "LLM_NOT_CONFIGURED") {
    setSectionStatus("llm", null, "No LLM configured yet");
    UI_LOGGER.info("llm_not_configured", "No LLM configured yet");
    setSectionJson("llm", initialLlm);
  } else {
    reportEnvelope("llm", initialLlm as ApiEnvelope<unknown>, "llm_initial_load");
  }
  await renderSources(api);
  setSectionStatus("health", true, "OK: Backend bootstrapped");
  setSectionJson("health", { base_url: baseUrl });
  UI_LOGGER.info("bootstrap_completed", "Desktop bootstrap completed");
}

bootstrap().catch((error) => {
  UI_LOGGER.error("bootstrap_failed", "Desktop bootstrap failed", { error: String(error) });
  const app = document.getElementById("app");
  if (app) {
    app.textContent = `Bootstrap failed: ${String(error)}`;
  }
});
