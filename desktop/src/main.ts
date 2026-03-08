import "./styles.css";

import { invoke } from "@tauri-apps/api/core";

import { InfostreamApi } from "./api";
import type { ApiEnvelope, SourceRecord } from "./types";

function format(value: unknown): string {
  return JSON.stringify(value, null, 2);
}

function setOutput(id: string, value: unknown): void {
  const el = document.getElementById(id);
  if (el) {
    el.textContent = typeof value === "string" ? value : format(value);
  }
}

function showError(outputId: string, label: string, payload: ApiEnvelope<unknown>): void {
  setOutput(outputId, {
    label,
    ok: payload.ok,
    message: payload.message,
    error_code: payload.error_code
  });
}

function sourceTableRows(sources: SourceRecord[]): string {
  return sources
    .map(
      (source) => `
      <tr>
        <td>${source.uid}</td>
        <td>${source.name}</td>
        <td>${source.enabled ? "yes" : "no"}</td>
        <td>${source.platforms.join(", ")}</td>
        <td>
          <button data-action="enable" data-uid="${source.uid}">Enable</button>
          <button class="secondary" data-action="disable" data-uid="${source.uid}">Disable</button>
          <button class="secondary" data-action="delete" data-uid="${source.uid}">Uninstall</button>
        </td>
      </tr>
    `
    )
    .join("");
}

async function renderSources(api: InfostreamApi): Promise<void> {
  const installed = await api.getInstalledSources();
  if (!installed.ok || !installed.data) {
    showError("sources-output", "installed", installed as ApiEnvelope<unknown>);
    return;
  }

  const tbody = document.getElementById("sources-tbody");
  if (tbody) {
    tbody.innerHTML = sourceTableRows(installed.data.sources);
  }
  setOutput("sources-output", installed);
}

async function bootstrap(): Promise<void> {
  const app = document.getElementById("app");
  if (!app) {
    return;
  }

  app.innerHTML = `
    <section class="panel">
      <h1>Infostream Desktop <span id="base-url" class="badge">starting...</span></h1>
      <div class="controls">
        <button id="health-btn">Check health</button>
      </div>
      <pre id="health-output"></pre>
    </section>

    <section class="panel">
      <h2>Sources</h2>
      <div class="controls">
        <input id="source-path" placeholder="/abs/path/to/source" />
        <button id="source-install">Install source</button>
        <button class="secondary" id="source-refresh">Refresh list</button>
      </div>
      <table class="table">
        <thead>
          <tr>
            <th>UID</th>
            <th>Name</th>
            <th>Enabled</th>
            <th>Platforms</th>
            <th>Actions</th>
          </tr>
        </thead>
        <tbody id="sources-tbody"></tbody>
      </table>
      <pre id="sources-output"></pre>
    </section>

    <section class="grid">
      <div class="panel">
        <h2>Interests</h2>
        <textarea id="interests-text"></textarea>
        <div class="controls">
          <button id="interests-load">Load</button>
          <button id="interests-save">Save</button>
        </div>
        <pre id="interests-output"></pre>
      </div>

      <div class="panel">
        <h2>LLM</h2>
        <input id="llm-name" placeholder="name" />
        <select id="llm-mode">
          <option value="local">local</option>
          <option value="cloud">cloud</option>
        </select>
        <input id="llm-id" placeholder="unique_id (stub-local/stub-cloud)" />
        <textarea id="llm-config" placeholder='{"temperature":0.1}'></textarea>
        <div class="controls">
          <button id="llm-load">Load</button>
          <button id="llm-save">Save</button>
        </div>
        <pre id="llm-output"></pre>
      </div>
    </section>

    <section class="grid">
      <div class="panel">
        <h2>Schedule</h2>
        <input id="schedule-next" placeholder="2026-03-02T12:00:00Z" />
        <label><input id="schedule-run-every" type="checkbox" /> run every</label>
        <input id="schedule-spacing" type="number" placeholder="seconds" value="3600" />
        <div class="controls">
          <button id="schedule-save">Save schedule</button>
        </div>
        <pre id="schedule-output"></pre>
      </div>

      <div class="panel">
        <h2>Runs</h2>
        <div class="controls">
          <button id="run-now">Run digest now</button>
          <button class="secondary" id="run-latest">Load latest run</button>
        </div>
        <input id="run-id" placeholder="run_id" />
        <button class="secondary" id="run-get">Load run by ID</button>
        <pre id="runs-output"></pre>
      </div>
    </section>

    <section class="grid">
      <div class="panel">
        <h2>Digest</h2>
        <div class="controls">
          <button id="digest-current">Current digest</button>
          <button class="secondary" id="digest-history">Digest history</button>
        </div>
        <pre id="digest-output"></pre>
      </div>

      <div class="panel">
        <h2>Citations</h2>
        <input id="items-refs" placeholder="item:a,item:b" />
        <button id="items-fetch">Fetch items by refs</button>
        <pre id="items-output"></pre>
      </div>
    </section>
  `;

  const baseUrl = await invoke<string>("bootstrap_backend");
  const baseUrlEl = document.getElementById("base-url");
  if (baseUrlEl) {
    baseUrlEl.textContent = baseUrl;
  }

  const api = new InfostreamApi(baseUrl);

  const healthBtn = document.getElementById("health-btn");
  healthBtn?.addEventListener("click", async () => {
    const payload = await api.getHealth();
    setOutput("health-output", payload);
  });

  const sourceInstallBtn = document.getElementById("source-install");
  sourceInstallBtn?.addEventListener("click", async () => {
    const sourcePathEl = document.getElementById("source-path") as HTMLInputElement | null;
    if (!sourcePathEl) {
      return;
    }
    const payload = await api.installSource(sourcePathEl.value);
    if (!payload.ok) {
      showError("sources-output", "install", payload as ApiEnvelope<unknown>);
      return;
    }
    await renderSources(api);
  });

  const sourceRefreshBtn = document.getElementById("source-refresh");
  sourceRefreshBtn?.addEventListener("click", async () => {
    await renderSources(api);
  });

  const sourceTable = document.getElementById("sources-tbody");
  sourceTable?.addEventListener("click", async (event) => {
    const target = event.target as HTMLElement;
    const action = target.getAttribute("data-action");
    const uid = target.getAttribute("data-uid");
    if (!action || !uid) {
      return;
    }

    let payload: ApiEnvelope<unknown>;
    if (action === "enable") {
      payload = await api.enableSource(uid);
    } else if (action === "disable") {
      payload = await api.disableSource(uid);
    } else {
      payload = await api.uninstallSource(uid);
    }

    if (!payload.ok) {
      showError("sources-output", action, payload);
      return;
    }
    await renderSources(api);
  });

  const interestsLoadBtn = document.getElementById("interests-load");
  interestsLoadBtn?.addEventListener("click", async () => {
    const payload = await api.getInterests();
    if (payload.ok && payload.data) {
      const text = document.getElementById("interests-text") as HTMLTextAreaElement | null;
      if (text) {
        text.value = payload.data.interests;
      }
    }
    setOutput("interests-output", payload);
  });

  const interestsSaveBtn = document.getElementById("interests-save");
  interestsSaveBtn?.addEventListener("click", async () => {
    const text = document.getElementById("interests-text") as HTMLTextAreaElement | null;
    if (!text) {
      return;
    }
    const payload = await api.putInterests(text.value);
    setOutput("interests-output", payload);
  });

  const llmLoadBtn = document.getElementById("llm-load");
  llmLoadBtn?.addEventListener("click", async () => {
    const payload = await api.getCurrentLlm();
    if (payload.ok && payload.data) {
      (document.getElementById("llm-name") as HTMLInputElement).value = payload.data.name;
      (document.getElementById("llm-mode") as HTMLSelectElement).value = payload.data.mode;
      (document.getElementById("llm-id") as HTMLInputElement).value = payload.data.unique_id;
      (document.getElementById("llm-config") as HTMLTextAreaElement).value = format(payload.data.config);
    }
    setOutput("llm-output", payload);
  });

  const llmSaveBtn = document.getElementById("llm-save");
  llmSaveBtn?.addEventListener("click", async () => {
    const name = (document.getElementById("llm-name") as HTMLInputElement).value;
    const mode = (document.getElementById("llm-mode") as HTMLSelectElement).value as "local" | "cloud";
    const uniqueId = (document.getElementById("llm-id") as HTMLInputElement).value;
    const configText = (document.getElementById("llm-config") as HTMLTextAreaElement).value || "{}";
    let config: Record<string, unknown> = {};
    try {
      config = JSON.parse(configText) as Record<string, unknown>;
    } catch {
      setOutput("llm-output", { error: "Config must be valid JSON" });
      return;
    }
    const payload = await api.putCurrentLlm({
      name,
      mode,
      unique_id: uniqueId,
      config
    });
    setOutput("llm-output", payload);
  });

  const scheduleSaveBtn = document.getElementById("schedule-save");
  scheduleSaveBtn?.addEventListener("click", async () => {
    const schedule = (document.getElementById("schedule-next") as HTMLInputElement).value;
    const runEvery = (document.getElementById("schedule-run-every") as HTMLInputElement).checked;
    const spacingRaw = (document.getElementById("schedule-spacing") as HTMLInputElement).value;
    const spacing = Number.parseInt(spacingRaw, 10);
    const payload = await api.putSchedule({
      schedule,
      run_every: runEvery,
      spacing
    });
    setOutput("schedule-output", payload);
  });

  const runNowBtn = document.getElementById("run-now");
  runNowBtn?.addEventListener("click", async () => {
    const payload = await api.runDigest();
    if (payload.ok && payload.data) {
      (document.getElementById("run-id") as HTMLInputElement).value = payload.data.run_id;
    }
    setOutput("runs-output", payload);
  });

  const runLatestBtn = document.getElementById("run-latest");
  runLatestBtn?.addEventListener("click", async () => {
    const payload = await api.getLatestRun();
    setOutput("runs-output", payload);
  });

  const runGetBtn = document.getElementById("run-get");
  runGetBtn?.addEventListener("click", async () => {
    const runId = (document.getElementById("run-id") as HTMLInputElement).value;
    const payload = await api.getRun(runId);
    setOutput("runs-output", payload);
  });

  const digestCurrentBtn = document.getElementById("digest-current");
  digestCurrentBtn?.addEventListener("click", async () => {
    const payload = await api.getCurrentDigest();
    setOutput("digest-output", payload);
  });

  const digestHistoryBtn = document.getElementById("digest-history");
  digestHistoryBtn?.addEventListener("click", async () => {
    const payload = await api.getDigestHistory();
    setOutput("digest-output", payload);
  });

  const itemsFetchBtn = document.getElementById("items-fetch");
  itemsFetchBtn?.addEventListener("click", async () => {
    const refs = (document.getElementById("items-refs") as HTMLInputElement).value
      .split(",")
      .map((entry) => entry.trim())
      .filter((entry) => entry.length > 0);
    const payload = await api.getItems(refs);
    setOutput("items-output", payload);
  });

  await renderSources(api);
  setOutput("health-output", "Backend bootstrapped. Click health check.");
}

bootstrap().catch((error) => {
  setOutput("app", `Bootstrap failed: ${String(error)}`);
});
