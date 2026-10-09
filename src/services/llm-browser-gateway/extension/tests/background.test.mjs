import {test} from "node:test";
import assert from "node:assert/strict";
import {readFile} from "node:fs/promises";
import vm from "node:vm";

const source = (await readFile(new URL("../dist/background.js", import.meta.url), "utf8"))
  .replace(/export\s*\{\s*\};?\s*$/, "");
const tick = () => new Promise(resolve => setImmediate(resolve));
async function waitFor(condition) {
  for (let i=0; i<100 && !condition(); i++) await tick();
  assert.ok(condition(), "expected asynchronous browser action");
}
function harness(initialJobs = {}, options = {}) {
  const session = options.session ?? {pairingKey: "b".repeat(32), profileId: "default", jobs: initialJobs};
  const local = options.local ?? {gatewayTabs: {chatgpt: 7}};
  const sockets = [], sent = [], calls = [], timers = [];
  const updates = new Set();
  let listener;
  const runs = new Map();
  let nextTab = 8;
  let activeTab = 99;
  let receiverMissing = options.receiverMissing ?? false;
  let busy = options.busy ?? true;
  const tabs = new Map([[7, {id:7, url:"https://chatgpt.com/", status:"complete", windowId:1}],
    [99, {id:99, url:"https://example.com/", status:"complete", windowId:1}]]);
  const storage = data => ({
    get: async keys => typeof keys === "string" ? {[keys]: data[keys]} :
      Object.fromEntries(keys.map(k => [k, data[k]])),
    set: async updates => Object.assign(data, structuredClone(updates)),
    remove: async keys => { for (const key of keys) delete data[key]; },
    setAccessLevel: async ({accessLevel}) => { calls.push(accessLevel); },
  });
  class Socket {
    static OPEN = 1;
    readyState = 1;
    constructor() {sockets.push(this); setImmediate(() => this.onopen?.());}
    send(raw) {sent.push(JSON.parse(raw));}
    close() {this.readyState = 3; this.onclose?.();}
    receive(message) {this.onmessage?.({data: JSON.stringify(message)});}
  }
  const chrome = {
    storage: {session: storage(session), local: storage(local)},
    runtime: {id: "a".repeat(32), getManifest: () => ({version: "0.1.2"}),
      onMessage: {addListener: callback => {listener=callback;}}},
    alarms: {create() {}, onAlarm: {addListener() {}}},
    tabs: {
      create: async ({url}) => {
        const tab = {id:nextTab++, url, status:"complete", windowId:1};
        tabs.set(tab.id, tab);
        return tab;
      },
      query: async () => [tabs.get(activeTab)],
      onUpdated: {addListener: fn => updates.add(fn), removeListener: fn => updates.delete(fn)},
      reload: async id => {
        calls.push("navigation_loading");
        for (const fn of updates) fn(id, {status:"loading"});
        setImmediate(() => {
          receiverMissing = false;
          calls.push("navigation_complete");
          for (const fn of updates) fn(id, {status:"complete"});
        });
      },
      get: async id => tabs.get(id),
      update: async (id, change) => {
        if (change.active) {
          activeTab = id;
          calls.push(`activate:${id}`);
          return tabs.get(id);
        }
        calls.push("navigation_loading");
        for (const fn of updates) fn(id, {status:"loading"});
        setImmediate(() => {
          calls.push("navigation_complete");
          for (const fn of updates) fn(id, {status:"complete"});
        });
        Object.assign(tabs.get(id), {url:change.url});
        return {...tabs.get(id), status:"loading"};
      },
      sendMessage: async (id, message) => {
        calls.push(message.type);
        if (receiverMissing) throw new Error("Could not establish connection. Receiving end does not exist.");
        if (options.stopCompletes && message.type === "stop_all") busy = false;
        if (message.type === "probe") return {status: "success"};
        if (message.type === "inspect") return {attemptId: "restored", busy};
        if (message.type === "run") {
          return await new Promise(resolve => {runs.set(message.attemptId, {resolve, tabId:id});});
        }
        return {ok:true};
      },
    },
  };
  vm.runInNewContext(source, {chrome, WebSocket: Socket, URL, console,
    setInterval: () => 0, clearInterval() {}, setTimeout: (...args) => {
      timers.push(args);
      const timer=setTimeout(...args); timer.unref(); return timer;
    }, clearTimeout});
  return {session, local, sockets, sent, calls, timers, runs,
    popup: message => new Promise(resolve => listener(message, {id: chrome.runtime.id}, resolve)),
    page: (tabId, message) => new Promise(resolve => listener(message,
      {id:chrome.runtime.id, tab:{id:tabId}}, resolve)),
    active: () => activeTab,
    select: id => { activeTab = id; },
    changeUrl: (id, url) => { tabs.get(id).url = url; },
    url: id => tabs.get(id).url,
    finish: result => runs.get(result.attemptId).resolve(result)};
}
test("reconnect reconciles an existing page job without submitting it again", async () => {
  const h = harness({restored:{provider:"chatgpt", tabId:7, phase:"running"}});
  await waitFor(() => h.sent.some(m => m.type === "authenticate"));
  h.sockets[0].receive({type:"authenticated"});
  await waitFor(() => h.calls.includes("inspect"));
  assert.ok(!h.calls.includes("run"));
  assert.ok(!h.sent.some(m => m.type === "result"));
});

test("pairing migrates to trusted storage and survives a new browser session", async () => {
  const h = harness({}, {session: {pairingKey: '"' + "b".repeat(32) + '"', profileId:"default"}});
  await waitFor(() => h.sent.some(m => m.type === "authenticate"));
  assert.ok(h.calls.includes("TRUSTED_CONTEXTS"));
  assert.equal(h.session.pairingKey, undefined);
  assert.equal(h.local.pairingKey, "b".repeat(32));
  const restarted = harness({}, {session: {}, local: structuredClone(h.local)});
  await waitFor(() => restarted.sent.some(m => m.type === "authenticate"));
  assert.equal(restarted.sent[0].token, "b".repeat(32));
  assert.equal(restarted.sent[0].extensionVersion, "0.1.2");
  restarted.sockets[0].receive({type: "authenticated"});
  assert.equal((await restarted.popup({type:"status"})).connected, true);
});

test("backend disconnect reconnects with the saved pairing without popup input", async () => {
  const h = harness();
  await waitFor(() => h.sent.some(m => m.type === "authenticate"));
  h.sockets[0].receive({type: "authenticated"});
  h.sockets[0].close();
  const retry = h.timers.find(([, delay]) => delay === 3000);
  assert.ok(retry);
  retry[0]();
  await waitFor(() => h.sockets.length === 2);
  await waitFor(() => h.sent.filter(m => m.type === "authenticate").length === 2);
  h.sockets[1].receive({type: "authenticated"});
  assert.equal((await h.popup({type:"status"})).connected, true);
});

test("re-pair never reports the prior socket as connected and forget prevents reconnect", async () => {
  const h = harness();
  await waitFor(() => h.sent.some(m => m.type === "authenticate"));
  h.sockets[0].receive({type: "authenticated"});
  const pairing = await h.popup({type:"pair", key:"c".repeat(32), profileId:"default"});
  assert.equal(pairing.connected, false);
  assert.equal(h.local.pairingKey, "c".repeat(32));
  h.sockets[1].receive({type:"connection_error", code:"invalid_pairing"});
  assert.match((await h.popup({type:"status"})).connectionError, /Pairing rejected/);
  await h.popup({type:"forget"});
  const forgotten = await h.popup({type:"status"});
  assert.equal(forgotten.connected, false);
  assert.equal(forgotten.paired, false);
  assert.equal(h.local.pairingKey, undefined);
  assert.equal(h.sockets.length, 2);
});
test("duplicate attempt delivery cannot duplicate a generation", async () => {
  const h = harness();
  await waitFor(() => h.sent.some(m => m.type === "authenticate"));
  h.sockets[0].receive({type:"authenticated"});
  const command={type:"execute",provider:"chatgpt",slot:0,attemptId:"once",prompt:"test",timeoutMs:10000};
  h.sockets[0].receive(command);
  await waitFor(() => h.calls.includes("run"));
  h.sockets[0].receive(command);
  await tick();
  assert.equal(h.calls.filter(c => c === "run").length, 1);
  h.finish({type:"result",attemptId:"once",provider:"chatgpt",status:"success",submitted:true,text:"ok"});
  await waitFor(() => h.sent.some(m => m.type === "result"));
  h.sockets[0].receive({type:"job_ack",attemptId:"once"});
  await tick();
  assert.equal(h.session.jobs.once, undefined);
});
test("a disconnected extension cannot acknowledge browser submission", async () => {
  const h=harness();
  await waitFor(() => h.sockets.length > 0);
  const status=await h.popup({type:"status"});
  assert.equal(status.connected, false);
  assert.equal(h.sent.filter(m => m.type === "progress").length, 0);
});

test("a probe waits for new-conversation navigation before reading the page", async () => {
  const h=harness();
  await waitFor(() => h.sent.some(m => m.type === "authenticate"));
  h.sockets[0].receive({type:"authenticated"});
  h.sockets[0].receive({type:"probe",provider:"chatgpt",slot:0,attemptId:"probe-fresh",timeoutMs:10000});
  await waitFor(() => h.sent.some(m => m.attemptId === "probe-fresh"));
  assert.ok(h.calls.indexOf("navigation_complete") >= 0);
  assert.ok(h.calls.indexOf("navigation_complete") < h.calls.indexOf("probe"));
  assert.ok(!h.calls.includes("run"));
});

test("two slots use distinct tabs and never allow overlapping work on one slot", async () => {
  const h = harness();
  await waitFor(() => h.sent.some(m => m.type === "authenticate"));
  h.sockets[0].receive({type:"authenticated"});
  const command = {type:"execute", provider:"chatgpt", prompt:"test", timeoutMs:10000};
  h.sockets[0].receive({...command, slot:0, attemptId:"first"});
  h.sockets[0].receive({...command, slot:1, attemptId:"second"});
  await waitFor(() => h.runs.size === 2);
  assert.notEqual(h.runs.get("first").tabId, h.runs.get("second").tabId);
  assert.equal(Object.keys(h.local.gatewayTabSlots).length, 2);
  h.sockets[0].receive({...command, slot:0, attemptId:"overlap"});
  await waitFor(() => h.sent.some(m => m.attemptId === "overlap"));
  assert.equal(h.sent.find(m => m.attemptId === "overlap").status, "temporary_unavailable");
  assert.equal(h.runs.size, 2);
  for (const attemptId of ["first", "second"]) {
    h.finish({type:"result",provider:"chatgpt",attemptId,status:"success",submitted:true,text:"ok"});
  }
  await waitFor(() => h.sent.filter(m => m.type === "result").length === 3);
});

async function twoClaudeJobs() {
  const h = harness();
  await waitFor(() => h.sent.some(m => m.type === "authenticate"));
  h.sockets[0].receive({type:"authenticated"});
  for (const slot of [0, 1]) {
    h.sockets[0].receive({type:"execute",provider:"claude",slot,attemptId:`claude-${slot}`,
      prompt:"test",timeoutMs:60000});
  }
  await waitFor(() => h.runs.size === 2);
  return h;
}

test("operator reset recovers a stale tab script without sending a generation", async () => {
  const h = harness({}, {receiverMissing:true,busy:true,stopCompletes:true});
  h.changeUrl(7, "https://chatgpt.com/c/existing-test");
  await waitFor(() => h.sent.some(m => m.type === "authenticate"));
  h.sockets[0].receive({type:"authenticated"});
  const reset = h.popup({type:"reset"});
  await waitFor(() => h.sent.some(m => m.type === "reset"));
  h.sockets[0].receive({type:"reset_ack"});
  assert.equal((await reset).ok, true);
  assert.equal(h.calls.filter(c => c === "navigation_loading").length, 1);
  assert.ok(h.calls.indexOf("navigation_complete") < h.calls.lastIndexOf("stop_all"));
  assert.ok(!h.calls.includes("run"));
  assert.equal(h.url(7), "https://chatgpt.com/c/existing-test");
});

test("operator reset refuses to reload a designated tab that left its provider", async () => {
  const h = harness({}, {receiverMissing:true});
  h.changeUrl(7, "https://example.com/");
  await waitFor(() => h.sent.some(m => m.type === "authenticate"));
  h.sockets[0].receive({type:"authenticated"});
  const result = await h.popup({type:"reset"});
  assert.equal(result.ok, false);
  assert.match(result.error, /navigated away/);
  assert.ok(!h.sent.some(m => m.type === "reset"));
  assert.ok(!h.calls.includes("navigation_loading"));
});

test("operator reset cannot clear blocked state without an explicit idle result", async () => {
  const h = harness({}, {busy:"unrecognised"});
  await waitFor(() => h.sent.some(m => m.type === "authenticate"));
  h.sockets[0].receive({type:"authenticated"});
  const result = await h.popup({type:"reset"});
  assert.equal(result.ok, false);
  assert.match(result.error, /verify.*idle/);
  assert.ok(!h.sent.some(m => m.type === "reset"));
});

test("Claude activation is serialized and restores the previous tab between submissions", async () => {
  const h = await twoClaudeJobs();
  const first = h.runs.get("claude-0").tabId, second = h.runs.get("claude-1").tabId;
  assert.equal((await h.page(first, {type:"focus_for_send",attemptId:"claude-0"})).ok, true);
  assert.equal(h.active(), first);
  const waiting = h.page(second, {type:"focus_for_send",attemptId:"claude-1"});
  await tick();
  assert.equal(h.active(), first);
  await h.page(first, {type:"release_send_focus",attemptId:"claude-0"});
  assert.equal((await waiting).ok, true);
  assert.equal(h.active(), second);
  await h.page(second, {type:"release_send_focus",attemptId:"claude-1"});
  assert.equal(h.active(), 99);
  assert.deepEqual(h.calls.filter(c => c.startsWith("activate:")),
    [`activate:${first}`, "activate:99", `activate:${second}`, "activate:99"]);
});

test("focus recovery rejects a different tab and preserves the user's intervening switch", async () => {
  const h = await twoClaudeJobs();
  const tab = h.runs.get("claude-0").tabId;
  assert.equal((await h.page(99, {type:"focus_for_send",attemptId:"claude-0"})).ok, false);
  assert.equal(h.active(), 99);
  await h.page(tab, {type:"focus_for_send",attemptId:"claude-0"});
  h.select(7);
  await h.page(tab, {type:"release_send_focus",attemptId:"claude-0"});
  assert.equal(h.active(), 7);
});

test("cancelling a waiting preparation prevents a later tab activation", async () => {
  const h = await twoClaudeJobs();
  const first = h.runs.get("claude-0").tabId, second = h.runs.get("claude-1").tabId;
  await h.page(first, {type:"focus_for_send",attemptId:"claude-0"});
  const waiting = h.page(second, {type:"focus_for_send",attemptId:"claude-1"});
  h.sockets[0].receive({type:"cancel",attemptId:"claude-1"});
  await h.page(first, {type:"release_send_focus",attemptId:"claude-0"});
  assert.equal((await waiting).ok, false);
  assert.equal(h.active(), 99);
  assert.ok(!h.calls.includes(`activate:${second}`));
});

test("focus lease timeout restores the previous tab if content stops responding", async () => {
  const h = await twoClaudeJobs();
  const tab = h.runs.get("claude-0").tabId;
  await h.page(tab, {type:"focus_for_send",attemptId:"claude-0"});
  h.timers.filter(([, delay]) => delay === 10000).at(-1)[0]();
  await waitFor(() => h.active() === 99);
});

test("terminal failure releases the active preparation tab", async () => {
  const h = await twoClaudeJobs();
  const tab = h.runs.get("claude-0").tabId;
  await h.page(tab, {type:"focus_for_send",attemptId:"claude-0"});
  h.finish({type:"result",provider:"claude",attemptId:"claude-0",status:"ui_changed",submitted:false});
  await waitFor(() => h.sent.some(m => m.type === "result"));
  assert.equal(h.active(), 99);
});

test("backend disconnect restores the focused tab and rejects a queued activation", async () => {
  const h = await twoClaudeJobs();
  const first = h.runs.get("claude-0").tabId, second = h.runs.get("claude-1").tabId;
  await h.page(first, {type:"focus_for_send",attemptId:"claude-0"});
  const waiting = h.page(second, {type:"focus_for_send",attemptId:"claude-1"});
  h.sockets[0].close();
  assert.equal((await waiting).ok, false);
  assert.equal(h.active(), 99);
});


test("DeepSeek uses two isolated owned tabs and the same duplicate protection", async () => {
  const h = harness();
  await waitFor(() => h.sent.some(m => m.type === "authenticate"));
  h.sockets[0].receive({type:"authenticated"});
  const command = {type:"execute",provider:"deepseek",prompt:"test",timeoutMs:10000};
  for (const slot of [0,1]) h.sockets[0].receive({...command,slot,attemptId:`ds-${slot}`});
  await waitFor(() => h.runs.size === 2);
  const first = h.runs.get("ds-0").tabId, second = h.runs.get("ds-1").tabId;
  assert.notEqual(first, second);
  assert.equal(h.url(first), "https://chat.deepseek.com/");
  assert.equal(h.url(second), "https://chat.deepseek.com/");
  h.sockets[0].receive({...command,slot:0,attemptId:"ds-0"});
  await tick();
  assert.equal(h.calls.filter(c => c === "run").length, 2);
  for (const attemptId of ["ds-0","ds-1"]) h.finish({type:"result",provider:"deepseek",attemptId,
    status:"success",submitted:true,text:"ok"});
  await waitFor(() => h.sent.filter(m => m.type === "result").length === 2);
});

test("Meta AI uses two isolated owned tabs and the same duplicate protection", async () => {
  const h = harness();
  await waitFor(() => h.sent.some(m => m.type === "authenticate"));
  h.sockets[0].receive({type:"authenticated"});
  const command = {type:"execute",provider:"meta",prompt:"test",timeoutMs:10000};
  for (const slot of [0,1]) h.sockets[0].receive({...command,slot,attemptId:`ds-${slot}`});
  await waitFor(() => h.runs.size === 2);
  const first = h.runs.get("ds-0").tabId, second = h.runs.get("ds-1").tabId;
  assert.notEqual(first, second);
  assert.equal(h.url(first), "https://www.meta.ai/");
  assert.equal(h.url(second), "https://www.meta.ai/");
  h.sockets[0].receive({...command,slot:0,attemptId:"ds-0"});
  await tick();
  assert.equal(h.calls.filter(c => c === "run").length, 2);
  for (const attemptId of ["ds-0","ds-1"]) h.finish({type:"result",provider:"meta",attemptId,
    status:"success",submitted:true,text:"ok"});
  await waitFor(() => h.sent.filter(m => m.type === "result").length === 2);
});

test("Kimi uses two isolated owned tabs and the same duplicate protection", async () => {
  const h = harness();
  await waitFor(() => h.sent.some(m => m.type === "authenticate"));
  h.sockets[0].receive({type:"authenticated"});
  const command = {type:"execute",provider:"kimi",prompt:"test",timeoutMs:10000};
  for (const slot of [0,1]) h.sockets[0].receive({...command,slot,attemptId:`ds-${slot}`});
  await waitFor(() => h.runs.size === 2);
  const first = h.runs.get("ds-0").tabId, second = h.runs.get("ds-1").tabId;
  assert.notEqual(first, second);
  assert.equal(h.url(first), "https://www.kimi.ai/");
  assert.equal(h.url(second), "https://www.kimi.ai/");
  h.sockets[0].receive({...command,slot:0,attemptId:"ds-0"});
  await tick();
  assert.equal(h.calls.filter(c => c === "run").length, 2);
  for (const attemptId of ["ds-0","ds-1"]) h.finish({type:"result",provider:"kimi",attemptId,
    status:"success",submitted:true,text:"ok"});
  await waitFor(() => h.sent.filter(m => m.type === "result").length === 2);
});

test("Gemini uses two isolated owned tabs and the same duplicate protection", async () => {
  const h = harness();
  await waitFor(() => h.sent.some(m => m.type === "authenticate"));
  h.sockets[0].receive({type:"authenticated"});
  const command = {type:"execute",provider:"gemini",prompt:"test",timeoutMs:10000};
  for (const slot of [0,1]) h.sockets[0].receive({...command,slot,attemptId:`ds-${slot}`});
  await waitFor(() => h.runs.size === 2);
  const first = h.runs.get("ds-0").tabId, second = h.runs.get("ds-1").tabId;
  assert.notEqual(first, second);
  assert.equal(h.url(first), "https://gemini.google.com/app");
  assert.equal(h.url(second), "https://gemini.google.com/app");
  h.sockets[0].receive({...command,slot:0,attemptId:"ds-0"});
  await tick();
  assert.equal(h.calls.filter(c => c === "run").length, 2);
  for (const attemptId of ["ds-0","ds-1"]) h.finish({type:"result",provider:"gemini",attemptId,
    status:"success",submitted:true,text:"ok"});
  await waitFor(() => h.sent.filter(m => m.type === "result").length === 2);
});

test("Meta preparation can lease only its owned tab and restores the previous tab", async () => {
  const h = harness();
  await waitFor(() => h.sent.some(m => m.type === "authenticate"));
  h.sockets[0].receive({type:"authenticated"});
  h.sockets[0].receive({type:"execute",provider:"meta",slot:0,attemptId:"meta-prep",prompt:"test",timeoutMs:60000});
  await waitFor(() => h.runs.has("meta-prep"));
  const tab = h.runs.get("meta-prep").tabId;
  assert.equal((await h.page(99,{type:"focus_for_send",attemptId:"meta-prep"})).ok,false);
  assert.equal((await h.page(tab,{type:"focus_for_send",attemptId:"meta-prep"})).ok,true);
  assert.equal(h.active(),tab);
  await h.page(tab,{type:"release_send_focus",attemptId:"meta-prep"});
  assert.equal(h.active(),99);
});
