export {};

const URLS: Record<Provider, string> = {
  chatgpt: "https://chatgpt.com/", claude: "https://claude.ai/new",
  deepseek: "https://chat.deepseek.com/",
  meta: "https://www.meta.ai/",
  kimi: "https://www.kimi.ai/",
  gemini: "https://gemini.google.com/app",
};
let socket: WebSocket | undefined;
let authenticated = false;
let connecting = false;
let jobs: Record<string, SavedJob> = {};
const cancelled = new Set<string>();
const acknowledgements = new Map<string, () => void>();
let heartbeat: ReturnType<typeof setInterval> | undefined;
let reconnectTimer: ReturnType<typeof setTimeout> | undefined;
let authTimer: ReturnType<typeof setTimeout> | undefined;
let paired = false;
let connectionError = "";
let lastPong = 0;
let tabSlots: Record<string, number> = {};
let tabWrites: Promise<void> = Promise.resolve();
let focusQueue: Promise<void> = Promise.resolve();
let focusLease: {attemptId: string; tabId: number; windowId: number; previousTabId?: number;
  release: () => void; timer?: ReturnType<typeof setTimeout>} | undefined;

async function releaseSendFocus(attemptId: string): Promise<void> {
  const lease = focusLease;
  if (!lease || lease.attemptId !== attemptId) return;
  focusLease = undefined;
  if (lease.timer) clearTimeout(lease.timer);
  try {
    const [active] = await chrome.tabs.query({active: true, windowId: lease.windowId});
    // Preserve a user's intervening tab switch, and never bring a browser window to the front.
    if (active?.id === lease.tabId && lease.previousTabId !== undefined &&
        lease.previousTabId !== lease.tabId) {
      const previous = await chrome.tabs.get(lease.previousTabId);
      if (previous.windowId === lease.windowId) {
        await chrome.tabs.update(lease.previousTabId, {active: true});
      }
    }
  } catch {
    console.warn("Gateway could not restore the previous tab after Send preparation");
  } finally { lease.release(); }
}

async function focusForSend(attemptId: string, job: SavedJob): Promise<{ok: boolean}> {
  const predecessor = focusQueue;
  let release = (): void => {};
  focusQueue = new Promise<void>(resolve => { release = resolve; });
  const until = Date.now() + 10000;
  await predecessor;
  try {
    if (Date.now() >= until || !authenticated || jobs[attemptId] !== job ||
        !(["claude", "meta"].includes(job.provider)) || job.phase !== "preparing" || job.tabId === undefined ||
        cancelled.has(attemptId)) return {ok: false};
    const tab = await chrome.tabs.get(job.tabId);
    if (!tab.url || new URL(tab.url).origin !== new URL(URLS[job.provider]).origin) return {ok: false};
    const [previous] = await chrome.tabs.query({active: true, windowId: tab.windowId});
    focusLease = {attemptId, tabId: job.tabId, windowId: tab.windowId,
      previousTabId: previous?.id, release};
    focusLease.timer = setTimeout(() => void releaseSendFocus(attemptId), 10000);
    await chrome.tabs.update(job.tabId, {active: true});
    return {ok: true};
  } catch {
    await releaseSendFocus(attemptId);
    return {ok: false};
  } finally {
    if (focusLease?.attemptId !== attemptId) release();
  }
}

async function saveTabs(): Promise<void> {
  tabWrites = tabWrites.then(() => chrome.storage.local.set({gatewayTabSlots: tabSlots}));
  await tabWrites;
}

function normalizePairingKey(value: string): string {
  const key = value.trim();
  if (key.length >= 2 && ((key.startsWith('"') && key.endsWith('"')) ||
      (key.startsWith("'") && key.endsWith("'")))) return key.slice(1, -1);
  return key;
}

function closeConnection(): void {
  const old = socket;
  socket = undefined;
  authenticated = false;
  connecting = false;
  if (heartbeat) clearInterval(heartbeat);
  if (authTimer) clearTimeout(authTimer);
  if (reconnectTimer) clearTimeout(reconnectTimer);
  old?.close();
}

async function saveJobs(): Promise<void> {
  await chrome.storage.session.set({jobs});
}
function send(message: unknown): boolean {
  if (socket?.readyState !== WebSocket.OPEN) return false;
  socket.send(JSON.stringify(message));
  return true;
}
async function terminal(result: JobResult): Promise<void> {
  const job = jobs[result.attemptId];
  if (!job) return;
  job.phase = "finished";
  job.result = result;
  await releaseSendFocus(result.attemptId);
  await saveJobs();
  if (authenticated) send(result);
}
async function navigate(tabId: number, url: string, reload: boolean): Promise<chrome.tabs.Tab> {
  let loaded: (() => void) | undefined;
  let began = false;
  const complete = new Promise<void>(resolve => { loaded = resolve; });
  const listener = (id: number, change: {status?: string}): void => {
    if (id !== tabId) return;
    if (change.status === "loading") began = true;
    if (began && change.status === "complete") loaded?.();
  };
  chrome.tabs.onUpdated.addListener(listener);
  let timer: ReturnType<typeof setTimeout> | undefined;
  try {
    let tab: chrome.tabs.Tab | undefined;
    if (reload) {
      await chrome.tabs.reload(tabId);
      tab = await chrome.tabs.get(tabId);
    } else tab = await chrome.tabs.update(tabId, {url});
    if (!tab) throw new Error("Browser tab disappeared during navigation");
    await Promise.race([complete, new Promise<never>((_, reject) => {
      timer = setTimeout(() => reject(new Error("New conversation navigation timed out")), 30000);
    })]);
    return tab;
  } finally {
    if (timer) clearTimeout(timer);
    chrome.tabs.onUpdated.removeListener(listener);
  }
}
async function getTab(provider: Provider, slot: number, fresh: boolean): Promise<number> {
  const resource = `${provider}:${slot}`;
  let tab: chrome.tabs.Tab | undefined;
  const existing = tabSlots[resource];
  if (existing !== undefined) {
    try { tab = await chrome.tabs.get(existing); } catch { /* Closed tab. */ }
  }
  if (tab?.url && new URL(tab.url).origin !== new URL(URLS[provider]).origin) {
    throw new Error("Designated tab has navigated away");
  }
  if (tab?.id === undefined) {
    tab = await chrome.tabs.create({url: URLS[provider], active: false});
  } else if (fresh) {
    tab = await navigate(tab.id, URLS[provider], tab.url === URLS[provider]);
  }
  if (!tab || tab.id === undefined) throw new Error("No browser tab");
  tabSlots[resource] = tab.id;
  await saveTabs();
  return tab.id;
}
async function messageTab(tabId: number, message: unknown): Promise<any> {
  // Content scripts can register just after the load-complete event.
  const until = Date.now() + 10000;
  while (Date.now() < until) {
    try {
      const tab = await chrome.tabs.get(tabId);
      if (tab.status === "complete") return await chrome.tabs.sendMessage(tabId, message);
    } catch { /* Retry only connection establishment, never a generation command. */ }
    await new Promise(resolve => setTimeout(resolve, 200));
  }
  throw new Error("Page script unavailable");
}
async function stopOwnedTab(resource: string, tabId: number): Promise<void> {
  let tab: chrome.tabs.Tab | undefined;
  try { tab = await chrome.tabs.get(tabId); } catch { return; }
  if (!tab) return;
  const provider = resource.split(":")[0] as Provider;
  if (!(provider in URLS) || !tab.url || new URL(tab.url).origin !== new URL(URLS[provider]).origin) {
    throw new Error("A designated tab has navigated away; close it or restore its provider page before reset");
  }
  const inspect = {type: "inspect"};
  try {
    await chrome.tabs.sendMessage(tabId, inspect);
  } catch (error) {
    const reason = error && typeof error === "object" && "message" in error ? String(error.message) : "";
    if (!/Receiving end does not exist|Extension context invalidated/i.test(reason)) throw error;
    // Operator-requested reset: reload the same conversation to restore scripts after an update.
    await navigate(tabId, tab.url, true);
    await messageTab(tabId, inspect);
  }
  await chrome.tabs.sendMessage(tabId, {type: "stop_all"});
  const until = Date.now() + 5000;
  do {
    const check = await chrome.tabs.sendMessage(tabId, inspect);
    if (check?.busy === false) return;
    if (check?.busy !== true) throw new Error("Could not verify that the gateway tab is idle");
    await new Promise(resolve => setTimeout(resolve, 200));
  } while (Date.now() < until);
  throw new Error("Generation still active; try again after it stops");
}
async function execute(command: Command): Promise<void> {
  if (!(command.provider in URLS) || !command.attemptId || command.timeoutMs <= 0) return;
  if (!Number.isInteger(command.slot) || command.slot < 0 || command.slot >= 4) return;
  const prior = jobs[command.attemptId];
  if (prior) { if (prior.result) send(prior.result); return; }
  if (Object.values(jobs).some(j => j.provider === command.provider &&
      (j.slot ?? 0) === command.slot && (j.phase !== "finished" ||
        j.result?.status === "submission_unknown"))) {
    send({type: "result", attemptId: command.attemptId, provider: command.provider,
      status: "temporary_unavailable", submitted: false});
    return;
  }
  const job: SavedJob = {provider: command.provider, slot: command.slot, phase: "preparing"};
  jobs[command.attemptId] = job;
  await saveJobs();
  let diagnostic: ExecutionStage = "navigation";
  try {
    // Extension reload invalidates content scripts in existing tabs. Navigate even for a probe
    // after reserving an idle resource, so Chrome injects the current script into a clean page.
    job.tabId = await getTab(command.provider, command.slot, true);
    await saveJobs();
    // A harmless probe establishes the content-script connection before sending any work.
    diagnostic = "content_connection";
    const readiness = await messageTab(job.tabId, {type: "probe",
      attemptId: command.attemptId, timeoutMs: command.timeoutMs});
    if (readiness.status !== "success" || command.type === "probe") {
      await terminal({type: "result", attemptId: command.attemptId, provider: command.provider,
        ...readiness, submitted: false});
      return;
    }
    if (cancelled.has(command.attemptId)) throw new Error("Cancelled before submission");
    diagnostic = "readiness";
    // This call is sent once. A lost response is reconciled, never retried here.
    const result = await chrome.tabs.sendMessage(job.tabId, {...command, type: "run"}) as JobResult;
    await terminal(result);
  } catch {
    const unknown = job.phase === "submitting" || job.phase === "running";
    await terminal({type: "result", attemptId: command.attemptId, provider: command.provider,
      status: unknown ? "submission_unknown" : "browser_disconnected",
      submitted: unknown ? null : false, diagnostic});
  } finally {
    cancelled.delete(command.attemptId);
  }
}
async function reconcile(): Promise<void> {
  for (const [attemptId, job] of Object.entries(jobs)) {
    if (job.result) { send(job.result); continue; }
    try {
      if (job.tabId === undefined) throw new Error("No tab");
      const inspection = await chrome.tabs.sendMessage(job.tabId, {type: "inspect"});
      if (inspection.result) await terminal(inspection.result);
      else if (inspection.attemptId !== attemptId) throw new Error("Lost page job");
      // A still-running content script will deliver its terminal event to this worker.
    } catch {
      await terminal({type: "result", attemptId, provider: job.provider,
        status: job.phase === "preparing" ? "browser_disconnected" : "submission_unknown",
        submitted: job.phase === "preparing" ? false : null});
    }
  }
}
async function connect(): Promise<void> {
  await initialized;
  if (connecting || socket?.readyState === WebSocket.OPEN) return;
  connecting = true;
  const config = await chrome.storage.local.get(["pairingKey", "profileId"]);
  paired = Boolean(config.pairingKey);
  if (!paired) { connecting = false; return; }
  const connection = new WebSocket("ws://127.0.0.1:8091/bridge");
  socket = connection;
  authTimer = setTimeout(() => {
    if (socket === connection && !authenticated) connection.close();
  }, 10000);
  connection.onopen = () => {
    if (socket !== connection) return;
    send({type: "authenticate", token: config.pairingKey, profileId: config.profileId ?? "default",
      extensionVersion: chrome.runtime.getManifest().version, protocolVersion: 2});
  };
  connection.onmessage = event => {
    if (socket !== connection) return;
    let message: any;
    try { message = JSON.parse(String(event.data)); } catch { return; }
    if (message.type === "authenticated") {
      authenticated = true;
      connecting = false;
      connectionError = "";
      lastPong = Date.now();
      if (authTimer) clearTimeout(authTimer);
      if (heartbeat) clearInterval(heartbeat);
      heartbeat = setInterval(() => {
        if (Date.now() - lastPong > 45000) connection.close();
        else send({type: "ping"});
      }, 20000);
      void reconcile();
    } else if (message.type === "pong") {
      lastPong = Date.now();
    } else if (message.type === "connection_error") {
      connectionError = message.code === "update_required" ?
        "Extension update required. Reload it from chrome://extensions." :
        message.code === "invalid_pairing" ?
        "Pairing rejected: check the pairing key and profile name." :
        "Another extension connection is active; retrying automatically.";
    } else if (message.type === "progress_ack") {
      acknowledgements.get(message.attemptId)?.();
    } else if (message.type === "reset_ack") {
      acknowledgements.get("reset")?.();
    } else if (message.type === "job_ack") {
      if (jobs[message.attemptId]?.phase === "finished" &&
          jobs[message.attemptId]?.result?.status !== "submission_unknown") {
        delete jobs[message.attemptId];
        void saveJobs();
      }
    } else if (message.type === "cancel") {
      cancelled.add(message.attemptId);
      const tabId = jobs[message.attemptId]?.tabId;
      if (tabId !== undefined) void chrome.tabs.sendMessage(tabId,
        {type: "cancel", attemptId: message.attemptId}).catch(() => undefined);
      void releaseSendFocus(message.attemptId);
    } else if (message.type === "execute" || message.type === "probe") {
      void execute(message as Command);
    }
  };
  connection.onclose = () => {
    if (socket !== connection) return;
    authenticated = false;
    connecting = false;
    if (focusLease) void releaseSendFocus(focusLease.attemptId);
    if (heartbeat) clearInterval(heartbeat);
    if (authTimer) clearTimeout(authTimer);
    if (!connectionError) connectionError = "Gateway unavailable; reconnecting automatically.";
    if (reconnectTimer) clearTimeout(reconnectTimer);
    reconnectTimer = setTimeout(() => void connect(), 3000);
  };
  connection.onerror = () => connection.close();
}
async function acknowledged(key: string, message: unknown): Promise<void> {
  await new Promise<void>((resolve, reject) => {
    const timer = setTimeout(() => {
      acknowledgements.delete(key);
      reject(new Error("Gateway did not acknowledge; no submission allowed"));
    }, 5000);
    acknowledgements.set(key, () => {
      clearTimeout(timer); acknowledgements.delete(key); resolve();
    });
    if (!authenticated || !send(message)) {
      clearTimeout(timer); acknowledgements.delete(key); reject(new Error("Disconnected"));
    }
  });
}
chrome.runtime.onMessage.addListener((message: any, sender, respond) => {
  void (async () => {
    await initialized;
    if (message.type === "progress" || message.type === "terminal" ||
        message.type === "focus_for_send" || message.type === "release_send_focus") {
      const attemptId = message.attemptId ?? message.result?.attemptId;
      const job = jobs[attemptId];
      if (!job || job.tabId !== sender.tab?.id) throw new Error("Unknown page job");
      if (message.type === "focus_for_send") return await focusForSend(attemptId, job);
      if (message.type === "release_send_focus") {
        await releaseSendFocus(attemptId);
        return {ok: true};
      }
      if (message.type === "terminal") { await terminal(message.result); return {ok: true}; }
      job.phase = "submitting";
      await saveJobs();
      await acknowledged(attemptId, {type: "progress", attemptId, provider: job.provider,
        phase: "submitting"});
      job.phase = "running";
      await saveJobs();
      return {ok: true};
    }
    if (sender.tab || sender.id !== chrome.runtime.id) throw new Error("Popup required");
    if (message.type === "pair") {
      const key = typeof message.key === "string" ? normalizePairingKey(message.key) : "";
      if (key.length < 24 ||
          !/^[a-zA-Z0-9_-]{1,64}$/.test(message.profileId)) throw new Error("Invalid pairing values");
      await chrome.storage.local.set({pairingKey: key, profileId: message.profileId});
      paired = true;
      connectionError = "";
      closeConnection();
      await connect();
    } else if (message.type === "forget") {
      if (Object.values(jobs).some(j => j.phase !== "finished" ||
          j.result?.status === "submission_unknown")) throw new Error("Stop gateway work first");
      await chrome.storage.local.remove(["pairingKey", "profileId"]);
      await chrome.storage.session.remove(["pairingKey", "profileId"]);
      paired = false;
      connectionError = "";
      closeConnection();
    } else if (message.type === "status") {
      await connect();
    } else if (message.type === "designate") {
      const [tab] = await chrome.tabs.query({active: true, currentWindow: true});
      const provider = message.provider as Provider;
      if (!(provider in URLS) || tab?.id === undefined || !tab.url ||
          new URL(tab.url).origin !== new URL(URLS[provider]).origin) {
        throw new Error("Open the corresponding provider tab first");
      }
      if (Object.values(jobs).some(j => j.phase !== "finished")) throw new Error("Gateway is busy");
      for (const [resource, id] of Object.entries(tabSlots)) {
        if (id === tab.id) delete tabSlots[resource];
      }
      tabSlots[`${provider}:0`] = tab.id;
      await saveTabs();
    } else if (message.type === "reset") {
      for (const [resource, tabId] of Object.entries(tabSlots)) await stopOwnedTab(resource, tabId);
      await acknowledged("reset", {type: "reset"});
      jobs = {}; await saveJobs();
    }
    return {ok: true, connected: authenticated && socket?.readyState === WebSocket.OPEN,
      paired, connecting, connectionError, jobs: Object.keys(jobs).length,
      extensionId: chrome.runtime.id, extensionVersion: chrome.runtime.getManifest().version};
  })().then(respond).catch(error => respond({ok: false, error: String(error.message)}));
  return true;
});
chrome.alarms.onAlarm.addListener(() => void connect());
chrome.alarms.create("gateway-reconnect", {periodInMinutes: 1});
const initialized = (async () => {
  // Persist pairing across Chrome restarts, but never expose it to website content scripts.
  await chrome.storage.local.setAccessLevel({accessLevel: "TRUSTED_CONTEXTS"});
  const saved = await chrome.storage.session.get(["jobs", "pairingKey", "profileId"]);
  const config = await chrome.storage.local.get("pairingKey");
  const storedTabs = await chrome.storage.local.get(["gatewayTabs", "gatewayTabSlots"]);
  tabSlots = (storedTabs.gatewayTabSlots ?? {}) as Record<string, number>;
  for (const [provider, id] of Object.entries(storedTabs.gatewayTabs ?? {})) {
    if (provider in URLS && typeof id === "number" && tabSlots[`${provider}:0`] === undefined) {
      tabSlots[`${provider}:0`] = id;
    }
  }
  await saveTabs();
  await chrome.storage.local.remove(["gatewayTabs"]);
  if (!config.pairingKey && typeof saved.pairingKey === "string") {
    await chrome.storage.local.set({pairingKey: normalizePairingKey(saved.pairingKey),
      profileId: saved.profileId ?? "default"});
  }
  await chrome.storage.session.remove(["pairingKey", "profileId"]);
  jobs = (saved.jobs ?? {}) as Record<string, SavedJob>;
})();
void initialized.then(connect);
