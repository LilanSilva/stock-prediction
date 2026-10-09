type Provider = "chatgpt" | "claude" | "deepseek" | "meta" | "kimi" | "gemini";
type ExecutionStage = "navigation" | "content_connection" | "readiness" | "fresh_conversation" |
  "editor" | "input_verification" | "send_available" | "send_disabled" | "submission_ack" | "extraction";
type Outcome = "success" | "rate_limited" | "login_required" | "verification_required" |
  "browser_disconnected" | "ui_changed" | "temporary_unavailable" | "invalid_output" |
  "submission_unknown";
interface JobResult {
  type: "result";
  attemptId: string;
  provider: Provider;
  status: Outcome;
  submitted: boolean | null;
  text?: string;
  resetAt?: string;
  observedModel?: string;
  diagnostic?: ExecutionStage;
}
interface Command {
  type: "execute" | "probe";
  attemptId: string;
  provider: Provider;
  prompt?: string;
  slot: number;
  timeoutMs: number;
}
interface SavedJob {
  provider: Provider;
  slot: number;
  tabId?: number;
  phase: "preparing" | "submitting" | "running" | "finished";
  result?: JobResult;
}
