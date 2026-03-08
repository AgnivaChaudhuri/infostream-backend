export type ApiEnvelope<T> = {
  ok: boolean;
  message: string;
  error_code: string | null;
  data?: T;
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
