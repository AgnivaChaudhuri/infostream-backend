import "./styles.css";

import { invoke } from "@tauri-apps/api/core";

import { InfostreamApi, setApiTraceObserver, type ApiTraceEntry } from "./api";
import { createLogger } from "./logger";
import type {
  AdvancedSettingsPayload,
  ApiEnvelope,
  BackendKillResult,
  LlmConfigPayload,
  RunEvent,
  SourceRecord
} from "./types";

// This file renders the desktop UI, wires user actions to API calls,
// and standardizes section-level status feedback with optional JSON details.

type SectionId =
  | "health"
  | "backend"
  | "sources"
  | "interests"
  | "llm"
  | "advanced"
  | "schedule"
  | "runs"
  | "digest"
  | "items"
  | "events";

type ProviderChoice = "stub_local" | "stub_cloud" | "openai_compatible";
type DebugDirection = "send" | "receive";
type DebugChannel = "http" | "sse" | "tauri" | "ui";

type DebugTraceEntry = {
  timestamp: string;
  direction: DebugDirection;
  channel: DebugChannel;
  from: string;
  to: string;
  event: string;
  details: Record<string, unknown>;
};

type FlowNodeDef = {
  id: string;
  label: string;
  x: number;
  y: number;
  width: number;
  height: number;
};

type FlowEdgeDef = {
  id: string;
  from: string;
  to: string;
  label: string;
};

type DebugState = {
  entries: DebugTraceEntry[];
  activeEdgeIds: Set<string>;
  activeNodeIds: Set<string>;
};

const EVENT_LIMIT = 50;
const DEBUG_TRACE_LIMIT = 250;
const UI_LOGGER = createLogger("desktop.ui");
const DATA_FLOW_DIAGRAM = `Source (src_exec) -> src_output.json/src_media
  -> Backend SourceExecutor/RunService -> SQLite + settings/state files
  -> ProviderRouter
     -> Stub provider (no network) OR OpenAI-compatible /chat/completions
  -> digest output_json persisted in SQLite
  -> Backend API (/digest/current, /runs/*, /items, /events/stream)
  -> Frontend API client + UI rendering
  -> Optional Tauri commands (bootstrap/spawn/kill/picker/log sink)`;

const FLOW_NODES: FlowNodeDef[] = [
  { id: "desktop.tauri", label: "Tauri Shell\n(bootstrap/kill/picker)", x: 80, y: 50, width: 250, height: 74 },
  { id: "frontend.ui", label: "Frontend UI", x: 80, y: 200, width: 250, height: 74 },
  { id: "frontend.api", label: "Frontend API Client", x: 400, y: 200, width: 250, height: 74 },
  { id: "backend.api", label: "Backend API", x: 720, y: 200, width: 250, height: 74 },
  { id: "backend.sse", label: "Backend SSE Stream", x: 720, y: 330, width: 250, height: 74 },
  { id: "backend.digest", label: "DigestRunService", x: 720, y: 470, width: 250, height: 74 },
  { id: "backend.source_executor", label: "SourceExecutor", x: 400, y: 470, width: 250, height: 74 },
  { id: "source.src_exec", label: "Source src_exec", x: 80, y: 470, width: 250, height: 74 },
  { id: "source.src_output", label: "src_output.json/src_media", x: 80, y: 620, width: 250, height: 74 },
  { id: "backend.sqlite", label: "SQLite (items/runs/digests)", x: 400, y: 620, width: 250, height: 74 },
  { id: "backend.filesystem", label: "System Files + lt_media", x: 720, y: 620, width: 250, height: 74 },
  { id: "backend.provider_router", label: "ProviderRouter", x: 1040, y: 470, width: 250, height: 74 },
  { id: "llm.stub", label: "Stub Provider", x: 1360, y: 390, width: 250, height: 74 },
  { id: "llm.openai", label: "OpenAI-Compatible", x: 1360, y: 540, width: 250, height: 74 },
  { id: "cloud.llm_endpoint", label: "Cloud LLM Endpoint", x: 1360, y: 690, width: 250, height: 74 }
];

const FLOW_EDGES: FlowEdgeDef[] = [
  { id: "ui_to_tauri", from: "frontend.ui", to: "desktop.tauri", label: "invoke command" },
  { id: "tauri_to_backend", from: "desktop.tauri", to: "backend.api", label: "spawn/health reuse" },
  { id: "ui_to_frontend_api", from: "frontend.ui", to: "frontend.api", label: "action -> request" },
  { id: "frontend_api_to_backend_api", from: "frontend.api", to: "backend.api", label: "HTTP request" },
  { id: "backend_api_to_frontend_api", from: "backend.api", to: "frontend.api", label: "HTTP response" },
  { id: "frontend_api_to_ui", from: "frontend.api", to: "frontend.ui", label: "render/update" },
  { id: "frontend_api_to_backend_sse", from: "frontend.api", to: "backend.sse", label: "SSE connect" },
  { id: "backend_sse_to_frontend_api", from: "backend.sse", to: "frontend.api", label: "event stream" },
  { id: "backend_api_to_digest", from: "backend.api", to: "backend.digest", label: "launch/run fetch" },
  { id: "digest_to_source_executor", from: "backend.digest", to: "backend.source_executor", label: "execute source" },
  { id: "source_executor_to_src_exec", from: "backend.source_executor", to: "source.src_exec", label: "spawn src_exec" },
  { id: "src_exec_to_src_output", from: "source.src_exec", to: "source.src_output", label: "write output/media" },
  { id: "src_output_to_source_executor", from: "source.src_output", to: "backend.source_executor", label: "parse output" },
  { id: "source_executor_to_sqlite", from: "backend.source_executor", to: "backend.sqlite", label: "upsert items/cursors" },
  { id: "source_executor_to_filesystem", from: "backend.source_executor", to: "backend.filesystem", label: "media ingest" },
  { id: "digest_to_provider_router", from: "backend.digest", to: "backend.provider_router", label: "summarize/repair" },
  { id: "provider_router_to_stub", from: "backend.provider_router", to: "llm.stub", label: "local stub branch" },
  { id: "provider_router_to_openai", from: "backend.provider_router", to: "llm.openai", label: "cloud branch" },
  { id: "openai_to_cloud", from: "llm.openai", to: "cloud.llm_endpoint", label: "chat completions HTTP" },
  { id: "stub_to_digest", from: "llm.stub", to: "backend.digest", label: "stub output" },
  { id: "openai_to_digest", from: "llm.openai", to: "backend.digest", label: "model output" },
  { id: "digest_to_sqlite", from: "backend.digest", to: "backend.sqlite", label: "persist digest/run" },
  { id: "backend_api_to_sqlite", from: "backend.api", to: "backend.sqlite", label: "load items/runs/digests" },
  { id: "backend_api_to_filesystem", from: "backend.api", to: "backend.filesystem", label: "settings/sources/lockfile" }
];

const FLOW_EDGE_MAP = new Map(FLOW_EDGES.map((edge) => [edge.id, edge]));
const FLOW_NODE_MAP = new Map(FLOW_NODES.map((node) => [node.id, node]));

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

function nowIso(): string {
  return new Date().toISOString();
}

// Keep long payload previews readable in the debug table.
function trimDetailValue(value: unknown): string {
  const text = typeof value === "string" ? value : formatJson(value);
  if (text.length <= 240) {
    return text;
  }
  return `${text.slice(0, 240)}...[truncated]`;
}

function flowNodeCenter(nodeId: string): { x: number; y: number } {
  const node = FLOW_NODE_MAP.get(nodeId);
  if (!node) {
    return { x: 0, y: 0 };
  }
  return { x: node.x + node.width / 2, y: node.y + node.height / 2 };
}

function activateFlowEdge(state: DebugState, edgeId: string): void {
  const edge = FLOW_EDGE_MAP.get(edgeId);
  if (!edge) {
    return;
  }
  state.activeEdgeIds.add(edgeId);
  state.activeNodeIds.add(edge.from);
  state.activeNodeIds.add(edge.to);
}

function activateFlowEdgesForTrace(state: DebugState, entry: DebugTraceEntry): void {
  const path = String(entry.details.path ?? "");

  if (entry.channel === "tauri") {
    activateFlowEdge(state, "ui_to_tauri");
    if (entry.event.startsWith("bootstrap_backend")) {
      activateFlowEdge(state, "tauri_to_backend");
    }
  }

  if (entry.channel === "http" && entry.event === "request_started") {
    activateFlowEdge(state, "ui_to_frontend_api");
    activateFlowEdge(state, "frontend_api_to_backend_api");

    if (path.startsWith("/digest") || path.startsWith("/runs")) {
      activateFlowEdge(state, "backend_api_to_digest");
    }
    if (path.startsWith("/items") || path.startsWith("/digest") || path.startsWith("/runs")) {
      activateFlowEdge(state, "backend_api_to_sqlite");
    }
    if (
      path.startsWith("/sources") ||
      path.startsWith("/interests") ||
      path.startsWith("/llm") ||
      path.startsWith("/settings")
    ) {
      activateFlowEdge(state, "backend_api_to_filesystem");
    }
  }

  if (entry.channel === "http" && (entry.event === "request_completed" || entry.event === "request_failed")) {
    activateFlowEdge(state, "backend_api_to_frontend_api");
    activateFlowEdge(state, "frontend_api_to_ui");
  }

  if (entry.channel === "sse" && entry.event === "events_stream_connect") {
    activateFlowEdge(state, "ui_to_frontend_api");
    activateFlowEdge(state, "frontend_api_to_backend_sse");
  }

  if (entry.channel === "sse" && entry.event.startsWith("events_stream_")) {
    activateFlowEdge(state, "backend_sse_to_frontend_api");
    activateFlowEdge(state, "frontend_api_to_ui");
  }

  if (entry.event === "run_accepted") {
    activateFlowEdge(state, "backend_api_to_digest");
  }

  if (entry.event === "source_result_loaded" || entry.from.startsWith("source.")) {
    activateFlowEdge(state, "digest_to_source_executor");
    activateFlowEdge(state, "source_executor_to_src_exec");
    activateFlowEdge(state, "src_exec_to_src_output");
    activateFlowEdge(state, "src_output_to_source_executor");
    activateFlowEdge(state, "source_executor_to_sqlite");
    activateFlowEdge(state, "source_executor_to_filesystem");
  }

  if (entry.event === "raw_llm_output_attached") {
    const provider = String(entry.details.provider ?? "unknown");
    activateFlowEdge(state, "digest_to_provider_router");
    if (provider === "stub") {
      activateFlowEdge(state, "provider_router_to_stub");
      activateFlowEdge(state, "stub_to_digest");
    } else {
      activateFlowEdge(state, "provider_router_to_openai");
      activateFlowEdge(state, "openai_to_cloud");
      activateFlowEdge(state, "openai_to_digest");
    }
  }

  if (entry.event === "digest_payload_loaded" || entry.event === "digest_history_loaded") {
    activateFlowEdge(state, "digest_to_sqlite");
    activateFlowEdge(state, "backend_api_to_sqlite");
    activateFlowEdge(state, "backend_api_to_frontend_api");
    activateFlowEdge(state, "frontend_api_to_ui");
  }

  if (entry.event === "sources_snapshot_loaded") {
    activateFlowEdge(state, "backend_api_to_filesystem");
    activateFlowEdge(state, "backend_api_to_frontend_api");
    activateFlowEdge(state, "frontend_api_to_ui");
  }
}

function renderFlowchartSvg(state: DebugState): string {
  const edges = FLOW_EDGES.map((edge) => {
    const from = flowNodeCenter(edge.from);
    const to = flowNodeCenter(edge.to);
    const active = state.activeEdgeIds.has(edge.id);
    const marker = active ? "flow-arrow-active" : "flow-arrow";
    const labelX = (from.x + to.x) / 2;
    const labelY = (from.y + to.y) / 2 - 6;
    return `
      <g>
        <line
          x1="${from.x}"
          y1="${from.y}"
          x2="${to.x}"
          y2="${to.y}"
          class="flow-edge${active ? " active" : ""}"
          marker-end="url(#${marker})"
        />
        <text x="${labelX}" y="${labelY}" class="flow-edge-label">${escapeHtml(edge.label)}</text>
      </g>
    `;
  }).join("");

  const nodes = FLOW_NODES.map((node) => {
    const active = state.activeNodeIds.has(node.id);
    const lines = node.label.split("\n");
    const text = lines
      .map((line, index) => {
        return `<tspan x="${node.x + node.width / 2}" dy="${index === 0 ? 0 : 16}">${escapeHtml(line)}</tspan>`;
      })
      .join("");
    return `
      <g>
        <rect
          x="${node.x}"
          y="${node.y}"
          width="${node.width}"
          height="${node.height}"
          rx="10"
          class="flow-node${active ? " active" : ""}"
        />
        <text x="${node.x + node.width / 2}" y="${node.y + node.height / 2 - (lines.length > 1 ? 8 : 0)}" class="flow-node-label">
          ${text}
        </text>
      </g>
    `;
  }).join("");

  return `
    <svg viewBox="0 0 1700 820" role="img" aria-label="Infostream debug flowchart">
      <defs>
        <marker id="flow-arrow" markerWidth="10" markerHeight="10" refX="9" refY="5" orient="auto" markerUnits="strokeWidth">
          <path d="M 0 0 L 10 5 L 0 10 z" fill="#111111"></path>
        </marker>
        <marker id="flow-arrow-active" markerWidth="10" markerHeight="10" refX="9" refY="5" orient="auto" markerUnits="strokeWidth">
          <path d="M 0 0 L 10 5 L 0 10 z" fill="#c5162b"></path>
        </marker>
      </defs>
      ${edges}
      ${nodes}
    </svg>
  `;
}

// Append newest-first trace entries and enforce bounded memory usage.
function appendDebugTrace(state: DebugState, entry: DebugTraceEntry): void {
  state.entries.unshift(entry);
  if (state.entries.length > DEBUG_TRACE_LIMIT) {
    state.entries.length = DEBUG_TRACE_LIMIT;
  }
  activateFlowEdgesForTrace(state, entry);
}

// Render full trace history into table rows.
function debugTraceRows(state: DebugState): string {
  return state.entries
    .map((entry) => {
      const details = escapeHtml(trimDetailValue(entry.details));
      return `
        <tr>
          <td><code>${escapeHtml(entry.timestamp)}</code></td>
          <td>${escapeHtml(entry.channel)}</td>
          <td>${escapeHtml(entry.direction)}</td>
          <td><code>${escapeHtml(entry.from)}</code></td>
          <td><code>${escapeHtml(entry.to)}</code></td>
          <td>${escapeHtml(entry.event)}</td>
          <td><pre>${details}</pre></td>
        </tr>
      `;
    })
    .join("");
}

// Collapse trace history into one "latest event" row per communication edge.
function debugEdgeSummaryRows(state: DebugState): string {
  const latestByEdge = new Map<string, DebugTraceEntry>();
  for (const entry of state.entries) {
    const edge = `${entry.from} -> ${entry.to} (${entry.channel})`;
    if (!latestByEdge.has(edge)) {
      latestByEdge.set(edge, entry);
    }
  }

  return Array.from(latestByEdge.entries())
    .map(([edge, entry]) => {
      const details = escapeHtml(trimDetailValue(entry.details));
      return `
        <tr>
          <td>${escapeHtml(edge)}</td>
          <td>${escapeHtml(entry.event)}</td>
          <td><code>${escapeHtml(entry.timestamp)}</code></td>
          <td><pre>${details}</pre></td>
        </tr>
      `;
    })
    .join("");
}

// Render the debug view's derived edge summary and full event timeline.
function renderDebugView(state: DebugState): void {
  const flowchart = getElement<HTMLDivElement>("debug-flowchart");
  flowchart.innerHTML = renderFlowchartSvg(state);

  const tableBody = getElement<HTMLTableSectionElement>("debug-trace-body");
  tableBody.innerHTML =
    debugTraceRows(state) ||
    `<tr><td colspan="7"><span class="status-line status-info">No trace events yet.</span></td></tr>`;

  const edgeBody = getElement<HTMLTableSectionElement>("debug-edge-body");
  edgeBody.innerHTML =
    debugEdgeSummaryRows(state) ||
    `<tr><td colspan="4"><span class="status-line status-info">No edges observed yet.</span></td></tr>`;

  const count = getElement<HTMLSpanElement>("debug-trace-count");
  count.textContent = String(state.entries.length);
}

// Record one local debug event and refresh the debug view when visible.
function emitDebugTrace(
  state: DebugState,
  channel: DebugChannel,
  direction: DebugDirection,
  from: string,
  to: string,
  event: string,
  details: Record<string, unknown>
): void {
  appendDebugTrace(state, {
    timestamp: nowIso(),
    channel,
    direction,
    from,
    to,
    event,
    details
  });
  const container = getElement<HTMLDivElement>("debug-view");
  if (!container.hidden) {
    renderDebugView(state);
  }
}

// Ingest API-level trace entries emitted by the shared HTTP/SSE wrapper.
function ingestApiTrace(state: DebugState, entry: ApiTraceEntry): void {
  appendDebugTrace(state, {
    timestamp: entry.timestamp,
    channel: entry.channel,
    direction: entry.direction,
    from: entry.from,
    to: entry.to,
    event: entry.event,
    details: entry.details
  });
  const container = getElement<HTMLDivElement>("debug-view");
  if (!container.hidden) {
    renderDebugView(state);
  }
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

function jsonDetails(sectionId: SectionId): string {
  return `
    <details class="json-details">
      <summary>Show JSON</summary>
      <div class="json-actions">
        <button class="secondary" type="button" data-copy-json="${sectionId}">Copy JSON</button>
      </div>
      <pre id="${sectionId}-json" class="json-output"></pre>
    </details>
  `;
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

async function copyTextToClipboard(value: string): Promise<void> {
  if (!value) {
    throw new Error("Nothing to copy");
  }
  if (!navigator.clipboard || !navigator.clipboard.writeText) {
    throw new Error("Clipboard API is unavailable");
  }
  await navigator.clipboard.writeText(value);
}

function wireJsonCopyButtons(root: ParentNode): void {
  const buttons = root.querySelectorAll<HTMLButtonElement>("button[data-copy-json]");
  buttons.forEach((button) => {
    button.addEventListener("click", async () => {
      const sectionId = button.dataset.copyJson as SectionId | undefined;
      if (!sectionId) {
        return;
      }
      const pre = getElement<HTMLPreElement>(`${sectionId}-json`);
      const payload = pre.textContent ?? "";
      try {
        await copyTextToClipboard(payload);
        setSectionStatus(sectionId, true, "OK: JSON copied to clipboard");
        UI_LOGGER.info("json_copied", "Copied section JSON to clipboard", { section: sectionId });
      } catch (error) {
        setSectionStatus(sectionId, false, "Failed: Unable to copy JSON");
        UI_LOGGER.warn("json_copy_failed", "Failed to copy section JSON", {
          section: sectionId,
          error: String(error)
        });
      }
    });
  });
}

async function killCurrentBackend(state: DebugState): Promise<BackendKillResult> {
  emitDebugTrace(
    state,
    "tauri",
    "send",
    "frontend.ui",
    "desktop.tauri",
    "stop_backend_current",
    {}
  );
  const result = await invoke<BackendKillResult>("stop_backend_current");
  emitDebugTrace(
    state,
    "tauri",
    "receive",
    "desktop.tauri",
    "frontend.ui",
    "stop_backend_current_result",
    { killed_pids: result.killed_pids }
  );
  return result;
}

async function killAllBackends(state: DebugState): Promise<BackendKillResult> {
  emitDebugTrace(
    state,
    "tauri",
    "send",
    "frontend.ui",
    "desktop.tauri",
    "stop_backend_all",
    {}
  );
  const result = await invoke<BackendKillResult>("stop_backend_all");
  emitDebugTrace(
    state,
    "tauri",
    "receive",
    "desktop.tauri",
    "frontend.ui",
    "stop_backend_all_result",
    { killed_pids: result.killed_pids }
  );
  return result;
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

function applyAdvancedToForm(settings: AdvancedSettingsPayload): void {
  getElement<HTMLTextAreaElement>("advanced-prompt-template").value = settings.prompt_template;
  getElement<HTMLTextAreaElement>("advanced-repair-template").value = settings.repair_prompt_template;
  getElement<HTMLInputElement>("advanced-max-items").value = String(settings.max_items_in_prompt);
  getElement<HTMLInputElement>("advanced-json-schema").checked = settings.use_json_schema_response;
  getElement<HTMLInputElement>("advanced-skip-repair").checked = settings.skip_repair;
  getElement<HTMLInputElement>("advanced-force-fallback").checked = settings.force_fallback;
  getElement<HTMLInputElement>("advanced-capture-exchange").checked =
    settings.capture_raw_provider_exchange;
  const promptOrigin = settings.is_default_prompt_template ? "default" : "custom";
  const repairOrigin = settings.is_default_repair_prompt_template ? "default" : "custom";
  getElement<HTMLParagraphElement>("advanced-template-origin").textContent =
    `Prompt source: ${promptOrigin}. Repair prompt source: ${repairOrigin}.`;
}

function buildAdvancedPayloadFromForm(): AdvancedSettingsPayload {
  const maxItems = Number.parseInt(getElement<HTMLInputElement>("advanced-max-items").value, 10);
  if (Number.isNaN(maxItems) || maxItems <= 0) {
    throw new Error("max_items_in_prompt must be a positive integer");
  }
  return {
    prompt_template: getElement<HTMLTextAreaElement>("advanced-prompt-template").value,
    repair_prompt_template: getElement<HTMLTextAreaElement>("advanced-repair-template").value,
    max_items_in_prompt: maxItems,
    use_json_schema_response: getElement<HTMLInputElement>("advanced-json-schema").checked,
    skip_repair: getElement<HTMLInputElement>("advanced-skip-repair").checked,
    force_fallback: getElement<HTMLInputElement>("advanced-force-fallback").checked,
    capture_raw_provider_exchange: getElement<HTMLInputElement>("advanced-capture-exchange").checked
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

async function renderSources(api: InfostreamApi, debugState: DebugState): Promise<void> {
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

  // Merge installed metadata with enabled state so the table reflects both views.
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
  emitDebugTrace(debugState, "http", "receive", "backend.sources", "frontend.ui", "sources_snapshot_loaded", {
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

function renderDigestAccordion(outputJson: Record<string, unknown>): void {
  const container = getElement<HTMLDivElement>("digest-accordion");
  const topLevelEntries = Object.entries(outputJson).filter(([key]) => key !== "version");
  if (topLevelEntries.length === 0) {
    container.innerHTML = "<p class=\"status-line status-info\">No digest content available.</p>";
    return;
  }

  // Render each top-level key as an accordion section with custom handling for interests.
  const html = topLevelEntries
    .map(([key, value]) => {
      if (key === "interests" && value && typeof value === "object" && !Array.isArray(value)) {
        return digestAccordionBlock("Interests", renderInterestsAccordion(value as Record<string, unknown>));
      }
      return digestAccordionBlock(key, renderDigestValue(value));
    })
    .join("");
  container.innerHTML = html;
}

function digestAccordionBlock(title: string, contentHtml: string): string {
  return `
    <details class="digest-accordion-item">
      <summary>${escapeHtml(title)}</summary>
      <div class="digest-accordion-content">${contentHtml}</div>
    </details>
  `;
}

function renderInterestsAccordion(interests: Record<string, unknown>): string {
  const entries = Object.entries(interests);
  if (entries.length === 0) {
    return "<p>No interests returned.</p>";
  }
  return entries
    .map(([interestName, bullets]) => {
      const bulletArray = Array.isArray(bullets) ? bullets : [];
      // Normalize bullet variants and keep confidence/source refs visible for review.
      const bulletHtml = bulletArray
        .map((bullet) => {
          if (!bullet || typeof bullet !== "object") {
            return `<li><pre>${escapeHtml(formatJson(bullet))}</pre></li>`;
          }
          const mapped = bullet as Record<string, unknown>;
          const text = String(mapped.text ?? "");
          const confidence = String(mapped.confidence ?? "unknown");
          const sourceRefs = Array.isArray(mapped.source_refs) ? mapped.source_refs : [];
          return `
            <li>
              <p>${escapeHtml(text)}</p>
              <p class="digest-confidence">Confidence: <strong>${escapeHtml(confidence)}</strong></p>
              <p class="digest-refs">Refs: ${escapeHtml(sourceRefs.map((entry) => String(entry)).join(", "))}</p>
            </li>
          `;
        })
        .join("");
      return digestAccordionBlock(
        interestName,
        `<ul class="digest-interest-list">${bulletHtml || "<li>No bullets</li>"}</ul>`
      );
    })
    .join("");
}

function renderDigestValue(value: unknown): string {
  if (typeof value === "string") {
    return `<p>${escapeHtml(value)}</p>`;
  }
  if (Array.isArray(value)) {
    if (value.length === 0) {
      return "<p>[]</p>";
    }
    return `<pre>${escapeHtml(formatJson(value))}</pre>`;
  }
  if (value && typeof value === "object") {
    return `<pre>${escapeHtml(formatJson(value))}</pre>`;
  }
  return `<p>${escapeHtml(String(value))}</p>`;
}

function renderRunDebug(run: Record<string, unknown>): void {
  const container = getElement<HTMLDivElement>("run-debug");
  const diagnosticsHtml = renderRunDiagnostics(run);
  const rawValue = run.raw_llm_output;
  if (typeof rawValue !== "string" || !rawValue.trim()) {
    container.innerHTML = `
      ${diagnosticsHtml}
      <p class="status-line status-info">No raw provider output captured for this run.</p>
    `;
    return;
  }

  // Prefer structured provider exchange view when raw_llm_output is wrapped JSON.
  const parsed = tryParseJson(rawValue);
  if (
    parsed &&
    typeof parsed === "object" &&
    !Array.isArray(parsed) &&
    "provider_exchange" in parsed &&
    "raw_output" in parsed
  ) {
    const mapped = parsed as Record<string, unknown>;
    const rawOutput = mapped.raw_output;
    const providerExchange = mapped.provider_exchange;
    container.innerHTML = `
      ${diagnosticsHtml}
      <details class="digest-accordion-item" open>
        <summary>Provider exchange (debug)</summary>
        <div class="digest-accordion-content">
          <pre>${escapeHtml(formatJson(providerExchange))}</pre>
        </div>
      </details>
      <details class="digest-accordion-item">
        <summary>Raw model output</summary>
        <div class="digest-accordion-content">
          <pre>${escapeHtml(typeof rawOutput === "string" ? rawOutput : formatJson(rawOutput))}</pre>
        </div>
      </details>
    `;
    return;
  }

  container.innerHTML = `
    ${diagnosticsHtml}
    <details class="digest-accordion-item" open>
      <summary>Raw model output</summary>
      <div class="digest-accordion-content">
        <pre>${escapeHtml(rawValue)}</pre>
      </div>
    </details>
  `;
}

function parseValidationErrors(errorMessage: unknown): string[] {
  if (typeof errorMessage !== "string" || !errorMessage.trim()) {
    return [];
  }
  return errorMessage
    .split(";")
    .map((entry) => entry.trim())
    .filter((entry) => entry.length > 0);
}

function renderRunDiagnostics(run: Record<string, unknown>): string {
  const runStatus = String(run.status ?? "unknown");
  const errorCode = typeof run.error_code === "string" ? run.error_code : null;
  const errorMessage = typeof run.error_message === "string" ? run.error_message : null;
  const validationErrors = parseValidationErrors(errorMessage);
  const isFailureContext = errorCode === "LLM_FALLBACK_USED" || runStatus === "failed";

  if (!isFailureContext && !errorCode && !errorMessage) {
    return `
      <details class="digest-accordion-item">
        <summary>Validation/Fallback diagnostics</summary>
        <div class="digest-accordion-content">
          <p>No validation or fallback diagnostics for this run.</p>
        </div>
      </details>
    `;
  }

  const errorRows = validationErrors.length
    ? `<ul class="run-diagnostics-list">${validationErrors.map((entry) => `<li>${escapeHtml(entry)}</li>`).join("")}</ul>`
    : "<p>No parsed validation errors available.</p>";

  return `
    <details class="digest-accordion-item" open>
      <summary>Validation/Fallback diagnostics</summary>
      <div class="digest-accordion-content">
        <p><strong>Status:</strong> ${escapeHtml(runStatus)}</p>
        <p><strong>Error code:</strong> ${escapeHtml(errorCode ?? "none")}</p>
        <p><strong>Error message:</strong> ${escapeHtml(errorMessage ?? "none")}</p>
        ${errorRows}
      </div>
    </details>
  `;
}

function tryParseJson(raw: string): unknown | null {
  try {
    return JSON.parse(raw);
  } catch {
    return null;
  }
}

function syncRunDebugFromEnvelope(payload: ApiEnvelope<unknown>): void {
  if (!payload.ok || !payload.data || typeof payload.data !== "object") {
    return;
  }
  const mapped = payload.data as Record<string, unknown>;
  const run = mapped.run;
  if (run && typeof run === "object" && !Array.isArray(run)) {
    renderRunDebug(run as Record<string, unknown>);
  }
}

function traceRunDataFlowFromEnvelope(state: DebugState, payload: ApiEnvelope<unknown>, action: string): void {
  if (!payload.ok || !payload.data || typeof payload.data !== "object") {
    return;
  }
  // Turn run payload fields into explicit data-flow checkpoints for the debug view.
  const data = payload.data as Record<string, unknown>;
  const run = data.run;
  if (run && typeof run === "object" && !Array.isArray(run)) {
    const runRecord = run as Record<string, unknown>;
    emitDebugTrace(state, "http", "receive", "backend.digest", "frontend.ui", "run_snapshot_loaded", {
      action,
      run_id: runRecord.run_id,
      status: runRecord.status,
      digest_id: runRecord.digest_id
    });
    if (typeof runRecord.raw_llm_output === "string" && runRecord.raw_llm_output.length > 0) {
      let provider = "unknown";
      const parsed = tryParseJson(runRecord.raw_llm_output);
      if (parsed && typeof parsed === "object" && !Array.isArray(parsed)) {
        const mappedExchange = parsed as Record<string, unknown>;
        const providerExchange = mappedExchange.provider_exchange;
        if (providerExchange && typeof providerExchange === "object" && !Array.isArray(providerExchange)) {
          const providerValue = (providerExchange as Record<string, unknown>).provider;
          if (typeof providerValue === "string" && providerValue.trim()) {
            provider = providerValue;
          }
        }
      }
      emitDebugTrace(state, "http", "receive", "backend.llm", "backend.digest", "raw_llm_output_attached", {
        action,
        run_id: runRecord.run_id,
        payload_size: runRecord.raw_llm_output.length,
        provider
      });
    }
  }

  // Limit per-run source rows to keep the trace readable in large source sets.
  const sourceResults = Array.isArray(data.sources) ? data.sources.slice(0, 20) : [];
  for (const result of sourceResults) {
    if (!result || typeof result !== "object") {
      continue;
    }
    const mapped = result as Record<string, unknown>;
    const sourceUid = String(mapped.source_uid ?? "unknown");
    emitDebugTrace(state, "http", "receive", `source.${sourceUid}`, "backend.digest", "source_result_loaded", {
      action,
      status: mapped.status,
      items_ingested_count: mapped.items_ingested_count,
      duration_ms: mapped.duration_ms
    });
  }
}

function traceDigestEnvelope(state: DebugState, payload: ApiEnvelope<unknown>, action: string): void {
  if (!payload.ok || !payload.data || typeof payload.data !== "object") {
    return;
  }
  // Expose digest-level milestones so reviewers can see payload movement clearly.
  const data = payload.data as Record<string, unknown>;
  if (data.output_json && typeof data.output_json === "object" && !Array.isArray(data.output_json)) {
    emitDebugTrace(state, "http", "receive", "backend.digest", "frontend.ui", "digest_payload_loaded", {
      action,
      keys: Object.keys(data.output_json as Record<string, unknown>)
    });
  }
  if (Array.isArray(data.digests)) {
    emitDebugTrace(state, "http", "receive", "backend.digest", "frontend.ui", "digest_history_loaded", {
      action,
      digest_count: data.digests.length
    });
  }
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

async function pickSourceDirectory(state: DebugState): Promise<string | null> {
  emitDebugTrace(
    state,
    "tauri",
    "send",
    "frontend.ui",
    "desktop.tauri",
    "pick_source_directory",
    {}
  );
  UI_LOGGER.debug("source_picker_open", "Opening source directory picker");
  const pickedPath = await invoke<string | null>("pick_source_directory");
  if (!pickedPath) {
    emitDebugTrace(
      state,
      "tauri",
      "receive",
      "desktop.tauri",
      "frontend.ui",
      "pick_source_directory_result",
      { selected_path: null }
    );
    UI_LOGGER.info("source_picker_cancelled", "Source directory picker cancelled");
    return null;
  }
  emitDebugTrace(
    state,
    "tauri",
    "receive",
    "desktop.tauri",
    "frontend.ui",
    "pick_source_directory_result",
    { selected_path: pickedPath }
  );
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
        <button class="secondary" id="debug-open">Open Debug View</button>
      </div>
      <p id="health-status" class="status-line status-info">Waiting for action</p>
      ${jsonDetails("health")}
    </section>

    <section class="panel">
      <h2>Backend Process Control</h2>
      <div class="controls">
        <button class="secondary" id="backend-kill-current">Kill current backend</button>
        <button class="secondary" id="backend-kill-all">Kill all Infostream backends</button>
      </div>
      <p id="backend-status" class="status-line status-info">Waiting for action</p>
      ${jsonDetails("backend")}
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
      ${jsonDetails("sources")}
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
        ${jsonDetails("interests")}
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
        ${jsonDetails("llm")}
      </div>

      <div class="panel">
        <h2>Advanced Options</h2>
        <div class="field-grid">
          <label>Prompt template (supports {interests_text} and {items_json})
            <textarea id="advanced-prompt-template" placeholder="Optional full prompt override"></textarea>
          </label>
          <label>Repair prompt template (supports {base_prompt}, {invalid_output}, {errors})
            <textarea id="advanced-repair-template" placeholder="Optional full repair prompt override"></textarea>
          </label>
          <label>Max items in prompt
            <input id="advanced-max-items" type="number" min="1" step="1" value="50" />
          </label>
          <label class="checkbox-inline">
            <input id="advanced-json-schema" type="checkbox" checked /> Use OpenAI JSON schema response format
          </label>
          <label class="checkbox-inline">
            <input id="advanced-skip-repair" type="checkbox" /> Skip repair attempt
          </label>
          <label class="checkbox-inline">
            <input id="advanced-force-fallback" type="checkbox" /> Force fallback digest
          </label>
          <label class="checkbox-inline">
            <input id="advanced-capture-exchange" type="checkbox" /> Capture raw provider exchange in run output
          </label>
        </div>
        <div class="controls">
          <button id="advanced-load">Load</button>
          <button id="advanced-save">Save</button>
          <button class="secondary" id="advanced-reset">Reset to Default Settings</button>
        </div>
        <p id="advanced-template-origin" class="status-line status-info">
          Prompt source: unknown. Repair prompt source: unknown.
        </p>
        <p id="advanced-status" class="status-line status-info">Waiting for action</p>
        ${jsonDetails("advanced")}
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
        ${jsonDetails("schedule")}
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
        <div id="run-debug" class="digest-accordion"></div>
        <p id="runs-status" class="status-line status-info">Waiting for action</p>
        ${jsonDetails("runs")}
      </div>
    </section>

    <section class="grid">
      <div class="panel">
        <h2>Digest</h2>
        <div class="controls">
          <button id="digest-current">Current digest</button>
          <button class="secondary" id="digest-history">Digest history</button>
        </div>
        <div id="digest-accordion" class="digest-accordion"></div>
        <p id="digest-status" class="status-line status-info">Waiting for action</p>
        ${jsonDetails("digest")}
      </div>

      <div class="panel">
        <h2>Citations</h2>
        <div class="controls">
          <input id="items-refs" placeholder="item:a,item:b" />
          <button id="items-fetch">Fetch by refs</button>
        </div>
        <p id="items-status" class="status-line status-info">Waiting for action</p>
        ${jsonDetails("items")}
      </div>
    </section>

    <section class="panel">
      <h2>Live Events (SSE)</h2>
      <p id="events-status" class="status-line status-info">Connecting...</p>
      <ul id="events-list" class="events-list"></ul>
      ${jsonDetails("events")}
    </section>

    <section id="debug-view" class="panel debug-view" hidden>
      <div class="debug-header">
        <h2>Debug View</h2>
        <div class="controls">
          <button class="secondary" id="debug-clear">Clear Trace</button>
          <button class="secondary" id="debug-copy">Copy Trace JSON</button>
          <button id="debug-close">Close Debug View</button>
        </div>
      </div>
      <p class="status-line status-info">
        Data-flow diagram and live communication trace. Trace count: <strong id="debug-trace-count">0</strong>
      </p>
      <div id="debug-flowchart" class="debug-flowchart"></div>
      <details class="json-details">
        <summary>Show text pipeline summary</summary>
        <pre class="debug-diagram" id="debug-diagram"></pre>
      </details>
      <h3>Latest per Edge</h3>
      <table class="table debug-table">
        <thead>
          <tr>
            <th>Edge</th>
            <th>Last Event</th>
            <th>Last Timestamp</th>
            <th>Last Payload</th>
          </tr>
        </thead>
        <tbody id="debug-edge-body"></tbody>
      </table>
      <h3>Live Trace</h3>
      <table class="table debug-table">
        <thead>
          <tr>
            <th>Timestamp</th>
            <th>Channel</th>
            <th>Direction</th>
            <th>From</th>
            <th>To</th>
            <th>Event</th>
            <th>Payload</th>
          </tr>
        </thead>
        <tbody id="debug-trace-body"></tbody>
      </table>
    </section>
  `;

  wireVisualFeedback(app);
  wireJsonCopyButtons(app);
  const debugState: DebugState = {
    entries: [],
    activeEdgeIds: new Set<string>(),
    activeNodeIds: new Set<string>()
  };
  getElement<HTMLPreElement>("debug-diagram").textContent = DATA_FLOW_DIAGRAM;
  renderDebugView(debugState);
  setApiTraceObserver((entry) => ingestApiTrace(debugState, entry));

  getElement<HTMLButtonElement>("debug-open").addEventListener("click", () => {
    const container = getElement<HTMLDivElement>("debug-view");
    container.hidden = false;
    renderDebugView(debugState);
    emitDebugTrace(debugState, "ui", "send", "frontend.user", "frontend.debug_view", "debug_view_opened", {});
  });

  getElement<HTMLButtonElement>("debug-close").addEventListener("click", () => {
    const container = getElement<HTMLDivElement>("debug-view");
    container.hidden = true;
    emitDebugTrace(debugState, "ui", "send", "frontend.user", "frontend.debug_view", "debug_view_closed", {});
  });

  getElement<HTMLButtonElement>("debug-clear").addEventListener("click", () => {
    debugState.entries = [];
    debugState.activeEdgeIds.clear();
    debugState.activeNodeIds.clear();
    renderDebugView(debugState);
  });

  getElement<HTMLButtonElement>("debug-copy").addEventListener("click", async () => {
    try {
      const payload = formatJson(debugState.entries);
      await copyTextToClipboard(payload);
      emitDebugTrace(debugState, "ui", "send", "frontend.user", "frontend.debug_view", "debug_trace_copied", {
        count: debugState.entries.length
      });
      renderDebugView(debugState);
    } catch (error) {
      emitDebugTrace(debugState, "ui", "receive", "frontend.debug_view", "frontend.user", "debug_trace_copy_failed", {
        error: String(error)
      });
    }
  });

  getElement<HTMLDivElement>("run-debug").innerHTML =
    "<p class=\"status-line status-info\">Load a run to inspect raw provider output.</p>";

  emitDebugTrace(
    debugState,
    "tauri",
    "send",
    "frontend.ui",
    "desktop.tauri",
    "bootstrap_backend",
    {}
  );
  const baseUrl = await invoke<string>("bootstrap_backend");
  emitDebugTrace(
    debugState,
    "tauri",
    "receive",
    "desktop.tauri",
    "frontend.ui",
    "bootstrap_backend_result",
    { base_url: baseUrl }
  );
  UI_LOGGER.info("backend_bootstrapped", "Backend bootstrap completed", { base_url: baseUrl });
  getElement<HTMLSpanElement>("base-url").textContent = baseUrl;

  const api = new InfostreamApi(baseUrl);
  let latestRunId = "";
  const events: RunEvent[] = [];

  // Source picker and source table controls.
  getElement<HTMLButtonElement>("source-pick").addEventListener("click", async (event) => {
    const button = event.currentTarget as HTMLButtonElement;
    await withButtonBusy(button, async () => {
      const pickedPath = await pickSourceDirectory(debugState);
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
        await renderSources(api, debugState);
      }
    });
  });

  getElement<HTMLButtonElement>("source-refresh").addEventListener("click", async (event) => {
    const button = event.currentTarget as HTMLButtonElement;
    await withButtonBusy(button, async () => {
      await renderSources(api, debugState);
    });
  });

  getElement<HTMLInputElement>("sources-show-enabled-only").addEventListener("change", async () => {
    try {
      await renderSources(api, debugState);
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
        await renderSources(api, debugState);
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

  // Backend process control actions.
  getElement<HTMLButtonElement>("backend-kill-current").addEventListener("click", async (event) => {
    const button = event.currentTarget as HTMLButtonElement;
    await withButtonBusy(button, async () => {
      try {
        const result = await killCurrentBackend(debugState);
        setSectionStatus("backend", true, "OK: Requested current backend termination");
        setSectionJson("backend", result);
        UI_LOGGER.info("backend_kill_current_succeeded", "Requested current backend termination", {
          killed_pids: result.killed_pids
        });
      } catch (error) {
        reportRuntimeError("backend", "Unable to kill current backend", error);
      }
    });
  });

  getElement<HTMLButtonElement>("backend-kill-all").addEventListener("click", async (event) => {
    const button = event.currentTarget as HTMLButtonElement;
    await withButtonBusy(button, async () => {
      try {
        const result = await killAllBackends(debugState);
        setSectionStatus("backend", true, "OK: Requested all backend termination");
        setSectionJson("backend", result);
        UI_LOGGER.info("backend_kill_all_succeeded", "Requested all backend termination", {
          killed_pids: result.killed_pids
        });
      } catch (error) {
        reportRuntimeError("backend", "Unable to kill all backends", error);
      }
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

  // Advanced options actions.
  getElement<HTMLButtonElement>("advanced-load").addEventListener("click", async (event) => {
    const button = event.currentTarget as HTMLButtonElement;
    await withButtonBusy(button, async () => {
      const payload = await api.getAdvancedSettings();
      if (payload.ok && payload.data) {
        applyAdvancedToForm(payload.data);
      }
      reportEnvelope("advanced", payload as ApiEnvelope<unknown>, "advanced_load");
    });
  });

  getElement<HTMLButtonElement>("advanced-save").addEventListener("click", async (event) => {
    const button = event.currentTarget as HTMLButtonElement;
    await withButtonBusy(button, async () => {
      try {
        const payload = buildAdvancedPayloadFromForm();
        const response = await api.putAdvancedSettings(payload);
        if (response.ok && response.data) {
          applyAdvancedToForm(response.data);
        }
        reportEnvelope("advanced", response as ApiEnvelope<unknown>, "advanced_save");
      } catch (error) {
        reportRuntimeError("advanced", "Invalid advanced options form data", error);
      }
    });
  });

  getElement<HTMLButtonElement>("advanced-reset").addEventListener("click", async (event) => {
    const button = event.currentTarget as HTMLButtonElement;
    await withButtonBusy(button, async () => {
      const payload = await api.resetAdvancedSettings();
      if (payload.ok && payload.data) {
        applyAdvancedToForm(payload.data);
      }
      reportEnvelope("advanced", payload as ApiEnvelope<unknown>, "advanced_reset");
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
        emitDebugTrace(debugState, "http", "receive", "backend.digest", "frontend.ui", "run_accepted", {
          run_id: payload.data.run_id
        });
        getElement<HTMLDivElement>("run-debug").innerHTML =
          "<p class=\"status-line status-info\">Run accepted. Load latest/by-id after completion to view debug output.</p>";
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
      syncRunDebugFromEnvelope(payload as ApiEnvelope<unknown>);
      traceRunDataFlowFromEnvelope(debugState, payload as ApiEnvelope<unknown>, "run_latest");
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
      syncRunDebugFromEnvelope(payload as ApiEnvelope<unknown>);
      traceRunDataFlowFromEnvelope(debugState, payload as ApiEnvelope<unknown>, "run_get");
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
      if (payload.ok && payload.data && typeof payload.data === "object") {
        const mapped = payload.data as Record<string, unknown>;
        if (mapped.output_json && typeof mapped.output_json === "object") {
          renderDigestAccordion(mapped.output_json as Record<string, unknown>);
        }
      }
      reportEnvelope("digest", payload as ApiEnvelope<unknown>, "digest_current");
      traceDigestEnvelope(debugState, payload as ApiEnvelope<unknown>, "digest_current");
    });
  });

  getElement<HTMLButtonElement>("digest-history").addEventListener("click", async (event) => {
    const button = event.currentTarget as HTMLButtonElement;
    await withButtonBusy(button, async () => {
      const payload = await api.getDigestHistory();
      reportEnvelope("digest", payload as ApiEnvelope<unknown>, "digest_history");
      traceDigestEnvelope(debugState, payload as ApiEnvelope<unknown>, "digest_history");
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
      setSectionStatus("events", true, "OK: Event received");
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
    },
    () => {
      setSectionStatus("events", true, "OK: Connected to event stream (waiting for events)");
      UI_LOGGER.info("events_stream_connected", "Events stream connected");
    }
  );

  window.addEventListener("beforeunload", () => {
    stream.close();
    setApiTraceObserver(null);
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

  const initialInterests = await api.getInterests();
  if (initialInterests.ok && initialInterests.data) {
    getElement<HTMLTextAreaElement>("interests-text").value = initialInterests.data.interests;
    reportEnvelope(
      "interests",
      initialInterests as ApiEnvelope<unknown>,
      "interests_initial_load"
    );
  } else {
    reportEnvelope(
      "interests",
      initialInterests as ApiEnvelope<unknown>,
      "interests_initial_load"
    );
  }

  const initialAdvanced = await api.getAdvancedSettings();
  if (initialAdvanced.ok && initialAdvanced.data) {
    applyAdvancedToForm(initialAdvanced.data);
    reportEnvelope("advanced", initialAdvanced as ApiEnvelope<unknown>, "advanced_initial_load");
  } else {
    reportEnvelope("advanced", initialAdvanced as ApiEnvelope<unknown>, "advanced_initial_load");
  }

  await renderSources(api, debugState);
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
