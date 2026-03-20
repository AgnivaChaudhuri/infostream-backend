import { invoke } from "@tauri-apps/api/core";

// Frontend logging helper used by TS modules and UI event handlers.
// It writes readable logs to console and forwards structured payloads to Rust.

export type FrontendLogLevel = "debug" | "info" | "warn" | "error";

export type FrontendLogPayload = {
  timestamp: string;
  level: FrontendLogLevel;
  component: string;
  event: string;
  message: string;
  context: Record<string, unknown>;
};

const SENSITIVE_KEY_PARTS = [
  "api_key",
  "apikey",
  "authorization",
  "token",
  "password",
  "secret",
  "cookie",
  "access_key",
  "private_key"
];

const REDACTED = "***REDACTED***";
const MAX_STRING_LENGTH = 4000;

function isSensitiveKey(key: string): boolean {
  const lowered = key.toLowerCase();
  return SENSITIVE_KEY_PARTS.some((part) => lowered.includes(part));
}

export function sanitizeForLog(value: unknown, keyHint?: string): unknown {
  if (keyHint && isSensitiveKey(keyHint)) {
    return REDACTED;
  }

  if (Array.isArray(value)) {
    return value.map((entry) => sanitizeForLog(entry, keyHint));
  }

  if (value && typeof value === "object") {
    const mapped: Record<string, unknown> = {};
    for (const [key, nested] of Object.entries(value)) {
      mapped[key] = sanitizeForLog(nested, key);
    }
    return mapped;
  }

  if (typeof value === "string") {
    if (/^\s*bearer\s+/i.test(value) || /^\s*sk-[a-z0-9]{10,}/i.test(value)) {
      return REDACTED;
    }
    if (value.length > MAX_STRING_LENGTH) {
      return `${value.slice(0, MAX_STRING_LENGTH)}...[truncated]`;
    }
  }

  return value;
}

function toContextRecord(context: Record<string, unknown> | undefined): Record<string, unknown> {
  if (!context) {
    return {};
  }
  const sanitized = sanitizeForLog(context);
  if (sanitized && typeof sanitized === "object" && !Array.isArray(sanitized)) {
    return sanitized as Record<string, unknown>;
  }
  return {};
}

function toConsoleMethod(level: FrontendLogLevel): (...data: unknown[]) => void {
  if (level === "debug") {
    return console.debug;
  }
  if (level === "info") {
    return console.info;
  }
  if (level === "warn") {
    return console.warn;
  }
  return console.error;
}

export class FrontendLogger {
  private readonly component: string;

  constructor(component: string) {
    this.component = component;
  }

  debug(event: string, message: string, context?: Record<string, unknown>): void {
    this.emit("debug", event, message, context);
  }

  info(event: string, message: string, context?: Record<string, unknown>): void {
    this.emit("info", event, message, context);
  }

  warn(event: string, message: string, context?: Record<string, unknown>): void {
    this.emit("warn", event, message, context);
  }

  error(event: string, message: string, context?: Record<string, unknown>): void {
    this.emit("error", event, message, context);
  }

  private emit(
    level: FrontendLogLevel,
    event: string,
    message: string,
    context?: Record<string, unknown>
  ): void {
    const payload: FrontendLogPayload = {
      timestamp: new Date().toISOString(),
      level,
      component: this.component,
      event,
      message,
      context: toContextRecord(context)
    };

    const consoleMethod = toConsoleMethod(level);
    consoleMethod(
      `${payload.timestamp} ${level.toUpperCase()} [${this.component}] ${event} ${message}`,
      payload.context
    );

    // Fire-and-forget: UI flow must not block on logging I/O.
    try {
      void invoke("emit_frontend_log", { payload }).catch((error) => {
        console.warn("frontend log bridge failed", {
          component: this.component,
          event,
          error: String(error)
        });
      });
    } catch (error) {
      console.warn("frontend log bridge unavailable", {
        component: this.component,
        event,
        error: String(error)
      });
    }
  }
}

export function createLogger(component: string): FrontendLogger {
  return new FrontendLogger(component);
}
