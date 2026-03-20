import type { ApiEnvelope, LlmConfigPayload, RunEvent, RunRecord, SourceRecord } from "./types";
import { createLogger } from "./logger";

// This file centralizes all HTTP calls from the desktop UI to the backend API.
// Each method maps to one backend endpoint and returns the typed response envelope.
const LOGGER = createLogger("desktop.api");

function nowMs(): number {
  if (typeof performance !== "undefined") {
    return performance.now();
  }
  return Date.now();
}

async function request<T>(baseUrl: string, path: string, init?: RequestInit): Promise<ApiEnvelope<T>> {
  const method = init?.method ?? "GET";
  const started = nowMs();
  LOGGER.debug("request_started", "HTTP request started", {
    method,
    path
  });
  try {
    const response = await fetch(`${baseUrl}${path}`, {
      headers: {
        "Content-Type": "application/json"
      },
      ...init
    });
    const body = (await response.json()) as ApiEnvelope<T>;
    const durationMs = Math.round(nowMs() - started);
    LOGGER.info("request_completed", "HTTP request completed", {
      method,
      path,
      status_code: response.status,
      duration_ms: durationMs,
      ok: body.ok,
      error_code: body.error_code
    });
    return body;
  } catch (error) {
    const durationMs = Math.round(nowMs() - started);
    LOGGER.error("request_failed", "HTTP request failed", {
      method,
      path,
      duration_ms: durationMs,
      error: String(error)
    });
    throw error;
  }
}

export class InfostreamApi {
  constructor(private readonly baseUrl: string) {}

  getHealth() {
    return request<{ api_version: string; scheduler_running: boolean; digest_running: boolean }>(
      this.baseUrl,
      "/health"
    );
  }

  getInstalledSources() {
    return request<{ sources: SourceRecord[] }>(this.baseUrl, "/sources/installed");
  }

  getEnabledSources() {
    return request<{ sources: SourceRecord[] }>(this.baseUrl, "/sources/enabled");
  }

  enableSource(uid: string) {
    return request<SourceRecord>(this.baseUrl, `/sources/${uid}/enable`, { method: "POST" });
  }

  disableSource(uid: string) {
    return request<SourceRecord>(this.baseUrl, `/sources/${uid}/disable`, { method: "POST" });
  }

  installSource(path: string) {
    return request<SourceRecord>(this.baseUrl, "/sources/install", {
      method: "POST",
      body: JSON.stringify({ path })
    });
  }

  uninstallSource(uid: string) {
    return request<void>(this.baseUrl, `/sources/${uid}`, { method: "DELETE" });
  }

  getInterests() {
    return request<{ interests: string }>(this.baseUrl, "/interests");
  }

  putInterests(interests: string) {
    return request<{ interests: string }>(this.baseUrl, "/interests", {
      method: "PUT",
      body: JSON.stringify({ interests })
    });
  }

  getCurrentLlm() {
    return request<{ name: string; mode: "local" | "cloud"; unique_id: string; config: Record<string, unknown> }>(
      this.baseUrl,
      "/llm/current"
    );
  }

  putCurrentLlm(payload: LlmConfigPayload) {
    return request(this.baseUrl, "/llm/current", {
      method: "PUT",
      body: JSON.stringify(payload)
    });
  }

  runDigest() {
    return request<{ run_id: string }>(this.baseUrl, "/digest/run", { method: "POST" });
  }

  putSchedule(payload: { schedule: string; run_every: boolean; spacing: number }) {
    return request(this.baseUrl, "/digest/schedule", {
      method: "PUT",
      body: JSON.stringify(payload)
    });
  }

  getCurrentDigest() {
    return request<Record<string, unknown>>(this.baseUrl, "/digest/current");
  }

  getDigestHistory() {
    return request<{ digests: Array<Record<string, unknown>> }>(this.baseUrl, "/digest/history");
  }

  getLatestRun() {
    return request<{ run: RunRecord; sources: Array<Record<string, unknown>> }>(this.baseUrl, "/runs/latest");
  }

  getRun(runId: string) {
    return request<{ run: RunRecord; sources: Array<Record<string, unknown>> }>(
      this.baseUrl,
      `/runs/${runId}`
    );
  }

  cancelRun(runId: string) {
    return request<{ run_id: string }>(this.baseUrl, `/runs/${runId}/cancel`, {
      method: "POST"
    });
  }

  getItems(refs: string[]) {
    const query = encodeURIComponent(refs.join(","));
    return request<{ items: Array<Record<string, unknown>> }>(this.baseUrl, `/items?refs=${query}`);
  }

  // Subscribe to backend Server-Sent Events for run lifecycle updates.
  openEventsStream(onEvent: (event: RunEvent) => void, onError?: (event: Event) => void): EventSource {
    LOGGER.info("events_stream_connecting", "Connecting to events stream", { path: "/events/stream" });
    const stream = new EventSource(`${this.baseUrl}/events/stream`);
    stream.onmessage = (event) => {
      try {
        const payload = JSON.parse(event.data) as RunEvent;
        LOGGER.debug("events_stream_message", "Received events stream message", {
          type: payload.type,
          run_id: payload.run_id,
          status: payload.status
        });
        onEvent(payload);
      } catch {
        // Ignore malformed event payloads and keep stream alive.
        LOGGER.warn("events_stream_malformed_message", "Ignoring malformed events stream payload");
      }
    };
    stream.onerror = (event) => {
      LOGGER.warn("events_stream_error", "Events stream error received");
      if (onError) {
        onError(event);
      }
    };
    return stream;
  }
}
