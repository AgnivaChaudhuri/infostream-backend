import type { ApiEnvelope, RunRecord, SourceRecord } from "./types";

async function request<T>(baseUrl: string, path: string, init?: RequestInit): Promise<ApiEnvelope<T>> {
  const response = await fetch(`${baseUrl}${path}`, {
    headers: {
      "Content-Type": "application/json"
    },
    ...init
  });
  const body = (await response.json()) as ApiEnvelope<T>;
  return body;
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

  putCurrentLlm(payload: { name: string; mode: "local" | "cloud"; unique_id: string; config: Record<string, unknown> }) {
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

  getItems(refs: string[]) {
    const query = encodeURIComponent(refs.join(","));
    return request<{ items: Array<Record<string, unknown>> }>(this.baseUrl, `/items?refs=${query}`);
  }
}
