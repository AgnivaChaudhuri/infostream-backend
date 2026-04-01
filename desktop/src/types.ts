// Shared frontend type contracts matching backend API envelope and payload shapes.

export type ApiEnvelope<T> = {
  ok: boolean;
  message: string;
  error_code: string | null;
  data?: T;
};

export type LlmConfigPayload = {
  name: string;
  mode: "local" | "cloud";
  unique_id: string;
  config: Record<string, unknown>;
};

export type AdvancedSettingsPayload = {
  prompt_template: string;
  repair_prompt_template: string;
  max_items_in_prompt: number;
  use_json_schema_response: boolean;
  skip_repair: boolean;
  force_fallback: boolean;
  capture_raw_provider_exchange: boolean;
  is_default_prompt_template?: boolean;
  is_default_repair_prompt_template?: boolean;
};

export type BackendKillResult = {
  killed_pids: number[];
  managed_children_killed: number;
  lockfile_removed: boolean;
};

export type SourceRecord = {
  uid: string;
  name: string;
  developer: string;
  version: string;
  description: string;
  platforms: string[];
  enabled: boolean;
  exec_path: string;
  interface_version: string;
};

export type RunRecord = {
  run_id: string;
  trigger: "manual" | "scheduled";
  status: string;
  started_at: string;
  finished_at: string | null;
  digest_id: string | null;
  error_code: string | null;
  error_message: string | null;
  raw_llm_output: string | null;
};

export type RunEvent = {
  type: string;
  run_id: string;
  status: string;
  details: Record<string, unknown>;
  occurred_at: string;
};
