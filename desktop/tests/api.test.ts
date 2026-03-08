import { describe, expect, it, vi } from "vitest";

import { InfostreamApi } from "../src/api";

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
});
