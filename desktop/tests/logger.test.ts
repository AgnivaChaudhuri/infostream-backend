import { describe, expect, it, vi, beforeEach } from "vitest";

vi.mock("@tauri-apps/api/core", () => ({
  invoke: vi.fn().mockResolvedValue(null)
}));

import { createLogger, sanitizeForLog } from "../src/logger";
import { invoke } from "@tauri-apps/api/core";

describe("frontend logger", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("redacts sensitive nested keys", () => {
    const sanitized = sanitizeForLog({
      api_key: "sk-test-key",
      nested: {
        authorization: "Bearer abc",
        ok: "value"
      }
    }) as Record<string, unknown>;

    expect(sanitized.api_key).toBe("***REDACTED***");
    expect((sanitized.nested as Record<string, unknown>).authorization).toBe("***REDACTED***");
    expect((sanitized.nested as Record<string, unknown>).ok).toBe("value");
  });

  it("forwards sanitized payload to tauri command", () => {
    const logger = createLogger("test.component");
    logger.info("test_event", "hello", {
      token: "abc123",
      detail: "safe"
    });

    expect(invoke).toHaveBeenCalledTimes(1);
    const [, args] = (invoke as unknown as { mock: { calls: Array<[string, { payload: Record<string, unknown> }]> } }).mock.calls[0];
    expect(args.payload.component).toBe("test.component");
    expect(args.payload.event).toBe("test_event");
    expect((args.payload.context as Record<string, unknown>).token).toBe("***REDACTED***");
    expect((args.payload.context as Record<string, unknown>).detail).toBe("safe");
  });
});
