import { describe, expect, it, vi } from "vitest";

vi.mock("@tauri-apps/api/core", () => ({
  invoke: vi.fn().mockResolvedValue(null)
}));

import { InfostreamApi } from "../src/api";

// This file verifies that API wrapper methods call the expected backend paths
// and that SSE payload parsing forwards only valid event messages.

describe("InfostreamApi", () => {
  it("sends schedule payload", async () => {
    const fetchSpy = vi.spyOn(globalThis, "fetch").mockResolvedValue({
      json: async () => ({ ok: true, message: "ok", error_code: null })
    } as Response);

    const api = new InfostreamApi("http://127.0.0.1:1234");
    await api.putSchedule({ schedule: "2026-03-02T12:00:00Z", run_every: true, spacing: 3600 });

    expect(fetchSpy).toHaveBeenCalledTimes(1);
    const [url, init] = fetchSpy.mock.calls[0];
    expect(url).toBe("http://127.0.0.1:1234/digest/schedule");
    expect((init as RequestInit).method).toBe("PUT");

    fetchSpy.mockRestore();
  });

  it("encodes refs query", async () => {
    const fetchSpy = vi.spyOn(globalThis, "fetch").mockResolvedValue({
      json: async () => ({ ok: true, message: "ok", error_code: null })
    } as Response);

    const api = new InfostreamApi("http://127.0.0.1:1234");
    await api.getItems(["item:a", "item:b"]);

    const [url] = fetchSpy.mock.calls[0];
    expect(String(url)).toContain("/items?refs=");
    expect(String(url)).toContain("item%3Aa%2Citem%3Ab");

    fetchSpy.mockRestore();
  });

  it("sends cancel run request", async () => {
    const fetchSpy = vi.spyOn(globalThis, "fetch").mockResolvedValue({
      json: async () => ({ ok: true, message: "ok", error_code: null })
    } as Response);

    const api = new InfostreamApi("http://127.0.0.1:1234");
    await api.cancelRun("run-123");

    expect(fetchSpy).toHaveBeenCalledTimes(1);
    const [url, init] = fetchSpy.mock.calls[0];
    expect(url).toBe("http://127.0.0.1:1234/runs/run-123/cancel");
    expect((init as RequestInit).method).toBe("POST");

    fetchSpy.mockRestore();
  });

  it("parses valid SSE events and ignores malformed payloads", () => {
    const originalEventSource = globalThis.EventSource;

    class FakeEventSource {
      static lastUrl = "";
      onmessage: ((event: MessageEvent<string>) => void) | null = null;
      onerror: ((event: Event) => void) | null = null;

      constructor(url: string) {
        FakeEventSource.lastUrl = url;
      }

      close() {
        // No-op in test.
      }
    }

    // Inject test EventSource implementation for deterministic stream behavior.
    globalThis.EventSource = FakeEventSource as unknown as typeof EventSource;

    const received: Array<Record<string, unknown>> = [];
    const api = new InfostreamApi("http://127.0.0.1:1234");
    const stream = api.openEventsStream((event) => {
      received.push(event as unknown as Record<string, unknown>);
    });

    expect(FakeEventSource.lastUrl).toBe("http://127.0.0.1:1234/events/stream");

    const fake = stream as unknown as FakeEventSource;
    fake.onmessage?.({ data: '{"type":"run_started","run_id":"r1","status":"running","details":{},"occurred_at":"2026-03-16T10:00:00Z"}' } as MessageEvent<string>);
    fake.onmessage?.({ data: "not json" } as MessageEvent<string>);

    expect(received).toHaveLength(1);
    expect(received[0].run_id).toBe("r1");

    globalThis.EventSource = originalEventSource;
  });
});
