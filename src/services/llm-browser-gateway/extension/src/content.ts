(() => {
  const provider: Provider = location.hostname === "chatgpt.com" ? "chatgpt" : "claude";
  let current: {attemptId: string; cancelled: boolean; submitted: boolean; result?: JobResult} |
    undefined;
  const pause = (ms: number): Promise<void> => new Promise(resolve => setTimeout(resolve, ms));
  function visible(element: Element): boolean {
    return element.getClientRects().length > 0 && getComputedStyle(element).visibility !== "hidden";
  }
  function first(selector: string): HTMLElement | undefined {
    return Array.from(document.querySelectorAll<HTMLElement>(selector)).find(visible);
  }
  function editor(): HTMLElement | undefined {
    return first(provider === "chatgpt" ?
      "#prompt-textarea, [data-testid='prompt-textarea'], textarea[data-id='root'], " +
      "[contenteditable='true'][role='textbox'][aria-label='Ask ChatGPT']" :
      "[data-testid='chat-input'][contenteditable='true'], " +
      "[contenteditable='true'][role='textbox'], .ProseMirror[contenteditable='true']");
  }
  function stopButton(): HTMLElement | undefined {
    return first("button[data-testid='stop-button'], button[aria-label='Stop generating'], " +
      "button[aria-label='Stop response'], button[aria-label='Stop Response']");
  }
  async function prepare(): Promise<void> {
    // Both sites hydrate after load; the ChatGPT homepage can also default to Work.
    const until = Date.now() + 8000;
    while (Date.now() < until) {
      const state = readiness().status;
      if (state !== "success" && state !== "ui_changed") return;
      const chat = provider === "chatgpt" ?
        Array.from(document.querySelectorAll<HTMLButtonElement>("button[aria-pressed]"))
          .find(button => visible(button) && button.textContent?.trim() === "Chat") : undefined;
      if (chat?.getAttribute("aria-pressed") === "false") {
        if (!chat.disabled) chat.click();
      } else if (editor()) return;
      await pause(100);
    }
    throw new Error("Provider composer is not ready");
  }
  function readiness(): {status: Outcome; resetAt?: string} {
    if (/\/login|\/auth/.test(location.pathname) || first("a[href*='/auth/login']")) {
      return {status: "login_required"};
    }
    if (first("iframe[src*='challenges.cloudflare.com'], [data-testid*='captcha']") ||
        /verify you are human/i.test(first("h1")?.innerText ?? "")) {
      return {status: "verification_required"};
    }
    // Inspect notices, not assistant text or the prompt (which may quote a limit message).
    const notices = "[role='alert'], [role='dialog'], [data-sonner-toast], " +
      "[data-testid='rate-limit-warning'], [data-testid='usage-limit-notice']";
    for (const notice of document.querySelectorAll<HTMLElement>(notices)) {
      if (!visible(notice)) continue;
      if (/usage limit|message limit|rate limit|you(?:'ve| have) reached|too many requests|out of messages|limit resets/i
          .test(notice.innerText)) {
        const raw = notice.querySelector("time[datetime]")?.getAttribute("datetime");
        const resetAt = raw && /(?:Z|[+-]\d\d:\d\d)$/.test(raw) && !Number.isNaN(Date.parse(raw)) ?
          new Date(raw).toISOString() : undefined;
        return {status: "rate_limited", ...(resetAt ? {resetAt} : {})};
      }
    }
    return {status: editor() ? "success" : "ui_changed"};
  }
  function answer(): HTMLElement | undefined {
    const selector = provider === "chatgpt" ?
      "[data-message-author-role='assistant'], [data-markdown-text-style='assistant-message']" :
      "[data-testid='assistant-message'], .font-claude-response, .font-claude-response-body";
    return Array.from(document.querySelectorAll<HTMLElement>(selector)).filter(visible).at(-1);
  }
  function answerText(node: HTMLElement): string {
    const code = node.querySelectorAll("pre code");
    if (code.length === 1) return code[0]?.textContent?.trim() ?? "";
    const copy = (node.querySelector(".markdown") ?? node).cloneNode(true) as HTMLElement;
    copy.querySelectorAll("button, style, script, svg").forEach(n => n.remove());
    return (copy.textContent ?? "").trim();
  }
  function draft(input: HTMLElement): string {
    return input instanceof HTMLTextAreaElement ? input.value : input.innerText;
  }
  function writePrompt(input: HTMLElement, prompt: string): boolean {
    input.focus();
    if (input instanceof HTMLTextAreaElement) {
      const setter = Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, "value")?.set;
      setter?.call(input, prompt);
    } else {
      const selection = getSelection();
      const range = document.createRange();
      range.selectNodeContents(input);
      selection?.removeAllRanges(); selection?.addRange(range);
      if (!document.execCommand("insertText", false, prompt)) return false;
    }
    input.dispatchEvent(new InputEvent("input", {bubbles: true, inputType: "insertText", data: prompt}));
    return true;
  }
  function sendButtons(): HTMLElement[] {
    return Array.from(document.querySelectorAll<HTMLElement>(
      "button[data-testid='send-button'], button[data-testid='chat-input-send'], " +
      "button[aria-label='Send prompt'], button[aria-label='Send message'], " +
      "button[aria-label='Send Message'], button[type='submit'][aria-label='Send']")).filter(visible);
  }
  function enabled(button: HTMLElement): boolean {
    return !button.hasAttribute("disabled") && button.getAttribute("aria-disabled") !== "true";
  }
  function result(command: Command, status: Outcome, submitted: boolean | null,
      extra: Partial<JobResult> = {}): JobResult {
    return {type: "result", attemptId: command.attemptId, provider, status, submitted, ...extra};
  }
  async function run(command: Command): Promise<JobResult> {
    if (current && !current.result) return result(command, "temporary_unavailable", false);
    const job = {attemptId: command.attemptId, cancelled: false, submitted: false,
      result: undefined as JobResult | undefined};
    current = job;
    const deadline = Date.now() + command.timeoutMs;
    let diagnostic: ExecutionStage = "readiness";
    let focusedForSend = false;
    const releaseFocus = async (): Promise<void> => {
      if (!focusedForSend) return;
      focusedForSend = false;
      await chrome.runtime.sendMessage({type: "release_send_focus", attemptId: command.attemptId})
        .catch(() => console.warn("Gateway Send focus release unavailable; background timeout will restore it"));
    };
    const outcome = (status: Outcome, submitted: boolean | null,
        extra: Partial<JobResult> = {}): JobResult =>
      result(command, status, submitted, {...extra, diagnostic});
    try {
      try { await prepare(); } catch { return outcome("ui_changed", false); }
      const ready = readiness();
      if (ready.status !== "success") return outcome(ready.status, false, ready);
      diagnostic = "editor";
      let input = editor();
      if (!input || typeof command.prompt !== "string") return outcome("ui_changed", false);
      diagnostic = "fresh_conversation";
      if (answer() || stopButton()) return outcome("ui_changed", false);
      diagnostic = "editor";
      if (!writePrompt(input, command.prompt)) return outcome("ui_changed", false);
      diagnostic = "input_verification";
      const payload = command.prompt.split("\nREQUEST:\n").at(-1) ?? command.prompt;
      if (!draft(input).includes(payload)) return outcome("ui_changed", false);
      diagnostic = "send_available";
      let send: HTMLElement | undefined;
      let sawSend = false, restorations = 0;
      const focusAfter = Date.now() + 2000;
      // A cold Claude page can expose its editor before Send finishes hydrating.
      const sendUntil = Math.min(deadline, Date.now() + (provider === "claude" ? 45000 : 15000));
      while (Date.now() < sendUntil && !job.cancelled) {
        const state = readiness();
        if (state.status !== "success" && state.status !== "ui_changed") {
          return outcome(state.status, false, state);
        }
        const liveInput = editor();
        if (liveInput) {
          const text = draft(liveInput);
          if (text.trim() && !text.includes(payload)) return outcome("ui_changed", false);
          if (liveInput !== input || !text.trim()) {
            // Hydration may replace/reset the editor. Restore only our own or an empty draft.
            if (restorations >= 2 || !writePrompt(liveInput, command.prompt)) {
              return outcome("ui_changed", false);
            }
            restorations++;
            input = liveInput;
          }
          if (!draft(liveInput).includes(payload)) return outcome("ui_changed", false);
          const buttons = sendButtons();
          sawSend = buttons.length > 0;
          send = buttons.find(enabled);
          if (send) break;
        }
        // Background Claude pages can defer rendering Send until the tab becomes active.
        if (provider === "claude" && document.visibilityState === "hidden" &&
            !focusedForSend && Date.now() >= focusAfter) {
          const focused = await chrome.runtime.sendMessage({type: "focus_for_send",
            attemptId: command.attemptId});
          if (!focused?.ok) return outcome("temporary_unavailable", false);
          focusedForSend = true;
        }
        await pause(100);
      }
      if (job.cancelled) return outcome("browser_disconnected", false);
      if (!send) {
        if (sawSend) diagnostic = "send_disabled";
        return outcome(sawSend ? "temporary_unavailable" : "ui_changed", false);
      }
      diagnostic = "submission_ack";
      const ack = await chrome.runtime.sendMessage({type: "progress", attemptId: command.attemptId});
      if (!ack?.ok || job.cancelled) return outcome("browser_disconnected", false);
      send = sendButtons().find(enabled);
      const confirmedInput = editor();
      if (Date.now() >= deadline || !send || !confirmedInput || !draft(confirmedInput).includes(payload)) {
        return outcome("temporary_unavailable", false);
      }
      job.submitted = true;
      send.click();
      await releaseFocus();
      diagnostic = "extraction";
      const until = deadline;
      let previous = "", stableSince = Date.now();
      const submittedAt = Date.now();
      while (Date.now() < until) {
        if (job.cancelled) {
          stopButton()?.click();
          await pause(500);
          return outcome("submission_unknown", null);
        }
        const check = readiness();
        if (check.status !== "success" && check.status !== "ui_changed") {
          return outcome(check.status, true, check);
        }
        const node = answer();
        const text = node ? answerText(node) : "";
        if (text !== previous) { previous = text; stableSince = Date.now(); }
        if (text && !stopButton() && Date.now() - submittedAt > 2500 &&
            Date.now() - stableSince > 1500) return outcome("success", true, {text});
        await pause(250);
      }
      stopButton()?.click();
      return outcome("submission_unknown", null);
    } catch {
      return outcome(job.submitted ? "submission_unknown" : "browser_disconnected",
        job.submitted ? null : false);
    } finally { await releaseFocus(); }
  }
  chrome.runtime.onMessage.addListener((message: any, _sender, respond) => {
    if (message.type === "probe") {
      void prepare().then(() => respond({...readiness(), diagnostic: "readiness"}))
        .catch(() => respond({diagnostic: "readiness", status: readiness().status === "success" ? "ui_changed" : readiness().status}));
      return true;
    }
    if (message.type === "inspect") {
      respond({attemptId: current?.attemptId, result: current?.result,
        busy: Boolean(stopButton()) || Boolean(current && !current.result)});
      return false;
    }
    if (message.type === "cancel" || message.type === "stop_all") {
      if (current && (message.type === "stop_all" || current.attemptId === message.attemptId)) {
        current.cancelled = true;
      }
      stopButton()?.click(); respond({ok: true}); return false;
    }
    if (message.type !== "run") return false;
    void run(message as Command).then(async completed => {
      if (current?.attemptId === completed.attemptId) current.result = completed;
      respond(completed);
      await chrome.runtime.sendMessage({type: "terminal", result: completed}).catch(() => undefined);
    });
    return true;
  });
})();
