import {test} from "node:test";
import assert from "node:assert/strict";
import {readFile} from "node:fs/promises";
import vm from "node:vm";

const source = await readFile(new URL("../dist/content.js", import.meta.url), "utf8");
const prompt = 'Instructions\nREQUEST:\n{"messages":[{"role":"user","content":"sample"}]}';

function harness(options = {}) {
  let now = 0, listener, selected, clicked = false, activated = false, rawSelected = false;
  const events = [];
  class Element {
    constructor(text = "") { this.innerText = text; }
    getClientRects() { return [{}]; }
    getAttribute() { return null; }
    hasAttribute() { return false; }
    querySelector() { return null; }
    closest() { return null; }
    focus() { selected = this; }
    dispatchEvent(event) {
      if (options.lexicalInput && event.data) {
        this.innerText += event.data;
        events.push({type:"duplicate_input"});
      }
    }
  }
  class Textarea extends Element {
    get value() { return this.innerText; }
    set value(text) { this.innerText = text; }
  }
  const Input = options.provider === "deepseek" ? Textarea : Element;
  const original = new Input(options.editorText ?? ""), replacement = new Input(options.replacementText ?? "");
  const editor = () => now >= (options.replaceAt ?? Infinity) ? replacement : original;
  const ready = () => now >= (options.readyAt ?? 0) && editor().innerText === prompt;
  const send = new Element();
  send.hasAttribute = name => !(["deepseek", "kimi"].includes(options.provider)) && name === "disabled" && !ready();
  send.getAttribute = name => name === "class" && !ready() ?
    (options.provider === "deepseek" ? "ds-button ds-button--disabled" :
      options.provider === "kimi" ? "send-button-container disabled" : null) : null;
  send.click = () => {
    assert.ok(ready(), "must not click a disabled button or send a lost draft");
    assert.equal(events.at(-1)?.type, "progress", "durable acknowledgement precedes click");
    events.push({type: "click", text: editor().innerText, at: now});
    clicked = true;
  };
  const answer = new Element();
  const fullAnswer = '{"kind":"final","content":"sample"}';
  const rawButton = new Element();
  rawButton.textContent = "Raw";
  rawButton.click = () => { rawSelected = true; events.push({type:"raw"}); };
  answer.querySelector = selector => selector.includes("JSON tree view") && options.metaTree &&
    !rawSelected ? new Element() : null;
  answer.getAttribute = name => {
    const complete = !options.missingCompletion && now >= (options.completeAt ?? 0);
    if (name === "aria-busy") return options.missingBusy ? null : complete ? "false" : "true";
    if (name === "data-streaming-state") return complete ? "DONE" : "STREAMING";
    if (name === "data-streaming-complete") return complete ? "true" : "false";
    return null;
  };
  answer.querySelectorAll = selector => {
    if (selector === ".ur-json-tree-toolbar button") return options.missingRaw ? [] : [rawButton];
    if (options.deepseekCode && selector !== ".md-code-block pre") return [];
    return [{textContent:now < (options.completeAt ?? 0) ?
      (options.partialText ?? '{"kind":"final","content":"sam') : fullAnswer}];
  };
  answer.cloneNode = () => ({textContent:"jsonCopyDownload" + fullAnswer, querySelectorAll: () => []});
  const turn = new Element();
  turn.getAttribute = name => name === "data-talvt-turn-state" ?
    (now >= (options.completeAt ?? 0) ? "complete" : "streaming") : null;
  const completedControl = new Element();
  turn.querySelector = () => !options.missingCompletion && now >= (options.completeAt ?? 0) ?
    completedControl : null;
  answer.closest = selector => {
    if (selector === "model-response" && options.provider === "gemini") return turn;
    if (selector === ".chat-content-item-assistant" && options.provider === "kimi") return turn;
    if (selector === "[data-virtual-list-item-key]" && options.provider === "deepseek") return turn;
    return selector === "[data-talvt-turn-state]" && options.turnMarker ? turn : null;
  };
  const stop = new Element();
  stop.click = () => events.push({type:"stop",at:now});
  const notice = new Element("You have reached your message limit");
  const standIn = new Element(options.composerNotice ?? "");
  const document = {
    get visibilityState() { return options.background && !activated ? "hidden" : "visible"; },
    createRange: () => ({selectNodeContents() {}}),
    execCommand: (_name, _ui, text) => {
      if (options.lexicalInput) {
        const target = selected;
        setImmediate(() => { target.innerText = text + target.innerText; });
      } else selected.innerText = text;
      events.push({type: "insert", at: now});
      return true;
    },
    querySelectorAll: selector => {
      if (selector === "button[aria-label='Send message']") return [send];
      if (selector === "button[aria-label='Stop response']") return clicked && now < (options.stopUntil ?? 0) ? [stop] : [];
      if (selector === "model-response message-content .markdown[aria-busy]") return clicked ? [answer] : [];
      if (selector.includes(".chat-input-editor")) return options.noEditor ? [] : [editor()];
      if (selector.includes("svg[name='Send']")) return [send];
      if (selector.includes("svg[name='stop']")) return clicked && now < (options.stopUntil ?? 0) ? [stop] : [];
      if (selector === ".send-button-container.loading") return options.initialBusy ? [new Element()] : [];
      if (selector === ".chat-content-item-assistant .markdown") return clicked ? [answer] : [];
      if (selector.includes("composer-input")) return options.noEditor || now < (options.editorAt ?? 0) ||
        (options.hydrateOnFocus && !activated) ? [] : [editor()];
      if (selector.includes("composer-send-button")) return [send];
      if (selector.includes("composer-stop-button")) return clicked && now < (options.stopUntil ?? 0) ? [stop] : [];
      if (selector === "textarea[placeholder='Message DeepSeek']") return options.noEditor ? [] : [editor()];
      if (selector.includes("M8.3125 0.980206")) return [send];
      if (selector.includes("M2 4.88")) return clicked && now < (options.stopUntil ?? 0) ? [stop] : [];
      if (selector.includes(".ds-loading")) return options.initialBusy ? [new Element()] : [];
      if (selector === ".ds-assistant-message-main-content") return clicked ? [answer] : [];
      if (selector.includes("challenges.cloudflare.com") && options.verification) return [new Element()];
      if (selector.includes("contenteditable")) return options.noEditor ? [] : [editor()];
      if (selector.includes("chat-input-send")) return [send];
      if (selector.includes("stop-button")) return clicked && now < (options.stopUntil ?? 0) ? [stop] : [];
      if (selector.includes("assistant-message")) return clicked ? [answer] : [];
      if (selector.includes("rate-limit-warning") && now >= (options.limitAt ?? Infinity)) return [notice];
      if (selector.includes("data-composer-stand-in") && options.composerNotice) return [standIn];
      return [];
    },
  };
  const chrome = {runtime: {
    onMessage: {addListener: callback => {listener = callback;}},
    sendMessage: async event => {
      events.push(event);
      if (event.type === "focus_for_send") {
        if (options.focusRejected) return {ok: false};
        activated = true;
      }
      return {ok: true};
    },
  }};
  vm.runInNewContext(source, {
    document, chrome, location: {hostname: {chatgpt:"chatgpt.com",claude:"claude.ai",
      deepseek:"chat.deepseek.com",meta:"www.meta.ai",kimi:"www.kimi.ai",gemini:"gemini.google.com"}[options.provider ?? "claude"], pathname: options.pathname ?? "/new"},
    Date: {now: () => now}, HTMLElement: Element, HTMLTextAreaElement: Textarea,
    InputEvent: class { constructor(_type, init) {Object.assign(this,init);} }, getComputedStyle: element => ({visibility:
      (element === send && options.background && !activated) ||
      (element === standIn && options.hiddenNotice) ? "hidden" : "visible"}),
    getSelection: () => ({removeAllRanges() {}, addRange() {}}),
    setTimeout: (callback, delay) => setImmediate(() => {
      now += delay;
      if (now >= (options.cancelAt ?? Infinity)) listener({type: "cancel", attemptId: "sample"}, {}, () => {});
      callback();
    }),
  });
  return {events, elapsed: () => now,
    probe: () => new Promise(resolve => listener({type:"probe",attemptId:"sample",timeoutMs:options.timeoutMs ?? 60000}, {}, resolve)),
    run: () => new Promise(resolve => listener({
    type: "run", attemptId: "sample", provider: options.provider ?? "claude", slot: 0,
    prompt, timeoutMs: options.timeoutMs ?? 60000,
  }, {}, resolve))};
}

test("Claude waits for Send hydration beyond fifteen seconds and submits exactly once", async () => {
  const h = harness({readyAt: 20000});
  const result = await h.run();
  assert.equal(result.status, "success");
  assert.equal(result.submitted, true);
  assert.equal(h.events.filter(event => event.type === "click").length, 1);
  assert.ok(!h.events.some(event => event.type === "focus_for_send"));
});

test("an editor replaced during hydration receives the same draft before submission", async () => {
  const h = harness({replaceAt: 2000, readyAt: 3000});
  assert.equal((await h.run()).status, "success");
  assert.equal(h.events.find(event => event.type === "click").text, prompt);
});

test("hydration recovery does not overwrite an unexpected replacement draft", async () => {
  const h = harness({replaceAt: 2000, readyAt: 3000, replacementText: "user draft"});
  const result = await h.run();
  assert.equal(result.status, "ui_changed");
  assert.equal(result.submitted, false);
  assert.ok(!h.events.some(event => event.type === "progress"));
});

test("a persistently disabled Send is bounded and remains safely unsubmitted", async () => {
  const h = harness({readyAt: Infinity});
  const result = await h.run();
  assert.equal(result.status, "temporary_unavailable");
  assert.equal(result.diagnostic, "send_disabled");
  assert.equal(result.submitted, false);
  assert.ok(!h.events.some(event => event.type === "progress"));
  assert.equal(h.elapsed(), 45000);
});

test("quota notices during Send readiness are returned without submission", async () => {
  const h = harness({readyAt: Infinity, limitAt: 2000});
  const result = await h.run();
  assert.equal(result.status, "rate_limited");
  assert.equal(result.submitted, false);
  assert.ok(!h.events.some(event => event.type === "progress"));
});

test("Claude's composer quota placeholder is rate limited without guessing its reset timezone", async () => {
  const h = harness({noEditor:true, composerNotice:"Your free messages return at 8:00 PM."});
  const probe = await h.probe();
  assert.equal(probe.status, "rate_limited");
  assert.equal(probe.resetAt, undefined);
  const result = await h.run();
  assert.equal(result.status, "rate_limited");
  assert.equal(result.submitted, false);
  assert.ok(!h.events.some(event => ["insert", "progress", "click"].includes(event.type)));
});

test("a non-quota composer placeholder remains a UI readiness failure", async () => {
  const h = harness({noEditor:true, composerNotice:"Loading your conversation"});
  assert.equal((await h.probe()).status, "ui_changed");
});

test("a hidden quota placeholder does not block a ready composer", async () => {
  const h = harness({hiddenNotice:true, composerNotice:"Your free messages return at 8:00 PM."});
  assert.equal((await h.probe()).status, "success");
});

test("quota wording quoted in a user draft is not a provider limit notice", async () => {
  const h = harness({editorText:"Your free messages return at 8:00 PM."});
  assert.equal((await h.probe()).status, "success");
});

test("ChatGPT waits through a sixty-second pause until its turn completes without a Stop button", async () => {
  const h = harness({provider:"chatgpt",turnMarker:true,completeAt:65000,timeoutMs:90000});
  const result = await h.run();
  assert.equal(result.status, "success");
  assert.equal(JSON.parse(result.text).content, "sample");
  assert.ok(h.elapsed() >= 65000 && h.elapsed() < 70000);
  assert.equal(h.events.filter(event => event.type === "click").length, 1);
});

test("even valid interim JSON is not final while the ChatGPT turn is streaming", async () => {
  const h = harness({provider:"chatgpt",turnMarker:true,completeAt:65000,timeoutMs:90000,
    partialText:'{"kind":"final","content":"interim"}'});
  assert.equal(JSON.parse((await h.run()).text).content, "sample");
  assert.ok(h.elapsed() >= 65000);
});

test("missing ChatGPT completion evidence times out rather than returning a paused answer", async () => {
  const h = harness({provider:"chatgpt",timeoutMs:10000});
  assert.equal((await h.run()).status, "submission_unknown");
  assert.equal(h.elapsed(), 10000);
});

test("a legacy ChatGPT turn can finish after its observed Stop control disappears", async () => {
  const h = harness({provider:"chatgpt",stopUntil:6000,completeAt:6000,timeoutMs:10000});
  assert.equal(JSON.parse((await h.run()).text).content, "sample");
  assert.ok(h.elapsed() >= 6000);
});

test("cancellation during Send readiness never submits a draft", async () => {
  const h = harness({readyAt: 5000, cancelAt: 2000});
  const result = await h.run();
  assert.equal(result.submitted, false);
  assert.ok(!h.events.some(event => event.type === "progress"));
  assert.equal(h.elapsed(), 2000);
});

test("a shorter request deadline cannot be extended by the Send wait", async () => {
  const h = harness({readyAt: 6000, timeoutMs: 5000});
  const result = await h.run();
  assert.equal(result.submitted, false);
  assert.ok(!h.events.some(event => event.type === "progress"));
  assert.equal(h.elapsed(), 5000);
});

test("a background Claude tab activates only for Send preparation and releases after one click", async () => {
  const h = harness({background: true});
  const result = await h.run();
  assert.equal(result.status, "success");
  assert.equal(h.events.filter(event => event.type === "focus_for_send").length, 1);
  assert.equal(h.events.filter(event => event.type === "click").length, 1);
  assert.ok(h.events.findIndex(event => event.type === "release_send_focus") >
    h.events.findIndex(event => event.type === "click"));
  assert.ok(h.elapsed() < 10000);
});

test("a rejected focus recovery cannot submit the background draft", async () => {
  const h = harness({background: true, focusRejected: true});
  const result = await h.run();
  assert.equal(result.submitted, false);
  assert.ok(!h.events.some(event => event.type === "progress"));
});

test("failed preparation releases its focus lease without clicking", async () => {
  const h = harness({background: true, readyAt: Infinity, limitAt: 3000});
  assert.equal((await h.run()).status, "rate_limited");
  assert.ok(h.events.some(event => event.type === "release_send_focus"));
  assert.ok(!h.events.some(event => event.type === "click"));
});

test("cancellation after activation releases the tab without acknowledging submission", async () => {
  const h = harness({background:true, readyAt:5000, cancelAt:3000});
  assert.equal((await h.run()).submitted, false);
  assert.ok(h.events.some(event => event.type === "focus_for_send"));
  assert.ok(h.events.some(event => event.type === "release_send_focus"));
  assert.ok(!h.events.some(event => event.type === "progress"));
});


test("DeepSeek writes its textarea and waits through a valid JSON pause for its own completed toolbar", async () => {
  const h = harness({provider:"deepseek",completeAt:65000,timeoutMs:90000,
    partialText:'{"kind":"final","content":"interim"}'});
  const result = await h.run();
  assert.equal(result.provider, "deepseek");
  assert.equal(result.status, "success");
  assert.equal(JSON.parse(result.text).content, "sample");
  assert.ok(h.elapsed() >= 65000);
  assert.equal(h.events.filter(e => e.type === "click").length, 1);
});

test("DeepSeek requires completion evidence even when text is stable and Stop is absent", async () => {
  const h = harness({provider:"deepseek",missingCompletion:true,timeoutMs:10000});
  assert.equal((await h.run()).status, "submission_unknown");
});

test("DeepSeek's Stop control prevents early completion", async () => {
  const h = harness({provider:"deepseek",stopUntil:8000});
  assert.equal((await h.run()).status, "success");
  assert.ok(h.elapsed() >= 8000);
});

test("DeepSeek's CSS-disabled Send cannot submit until enabled", async () => {
  const h = harness({provider:"deepseek",readyAt:5000});
  assert.equal((await h.run()).status, "success");
  assert.equal(h.events.find(e => e.type === "click").at, 5000);
});

test("DeepSeek reports sign-in, verification and quota states without submission", async () => {
  for (const [options,status] of [
    [{pathname:"/sign_in",noEditor:true},"login_required"],
    [{verification:true},"verification_required"],
    [{limitAt:0},"rate_limited"],
  ]) {
    const h = harness({provider:"deepseek",...options});
    assert.equal((await h.probe()).status, status);
    assert.equal((await h.run()).submitted, false);
    assert.ok(!h.events.some(e => e.type === "click" || e.type === "progress"));
  }
});

test("DeepSeek refuses a fresh request while the composer is already loading", async () => {
  const h = harness({provider:"deepseek",initialBusy:true});
  assert.equal((await h.run()).status, "ui_changed");
  assert.ok(!h.events.some(e => e.type === "click"));
});

test("DeepSeek cancellation requests Stop and remains an unknown submission", async () => {
  const h = harness({provider:"deepseek",completeAt:30000,stopUntil:30000,cancelAt:2000});
  const result = await h.run();
  assert.equal(result.status, "submission_unknown");
  assert.equal(result.submitted, null);
  assert.ok(h.events.some(e => e.type === "stop"));
});

test("DeepSeek extracts its pre/span code block without the language and Copy/Download banner", async () => {
  const h = harness({provider:"deepseek",deepseekCode:true});
  const result = await h.run();
  assert.equal(result.status, "success");
  assert.deepEqual(JSON.parse(result.text), {kind:"final",content:"sample"});
});


test("Meta waits for both streaming completion flags through a valid JSON pause", async () => {
  const h = harness({provider:"meta",completeAt:65000,timeoutMs:90000,
    partialText:'{"kind":"final","content":"interim"}'});
  const result = await h.run();
  assert.equal(result.provider, "meta");
  assert.equal(result.status, "success");
  assert.equal(JSON.parse(result.text).content, "sample");
  assert.ok(h.elapsed() >= 65000);
  assert.equal(h.events.filter(e => e.type === "click").length, 1);
});

test("Meta opens Raw JSON instead of extracting a collapsed tree", async () => {
  const h = harness({provider:"meta",metaTree:true});
  const result = await h.run();
  assert.equal(result.status, "success");
  assert.deepEqual(JSON.parse(result.text), {kind:"final",content:"sample"});
  assert.equal(h.events.filter(e => e.type === "raw").length, 1);
});

test("Meta cannot complete without raw JSON or completion evidence", async () => {
  for (const options of [{metaTree:true,missingRaw:true},{missingCompletion:true}]) {
    const h = harness({provider:"meta",timeoutMs:10000,...options});
    assert.equal((await h.run()).status, "submission_unknown");
  }
});

test("Meta respects disabled Send and Stop even with completion flags", async () => {
  const h = harness({provider:"meta",readyAt:5000,stopUntil:8000});
  assert.equal((await h.run()).status, "success");
  const clickedAt = h.events.find(e => e.type === "click").at;
  assert.ok(clickedAt >= 5000 && clickedAt < 5100);
  assert.ok(h.elapsed() >= 8000);
});

test("Meta reports login, verification and quota without sending", async () => {
  for (const [options,status] of [
    [{pathname:"/login",noEditor:true},"login_required"],
    [{verification:true},"verification_required"],
    [{limitAt:0},"rate_limited"],
  ]) {
    const h = harness({provider:"meta",...options});
    assert.equal((await h.probe()).status, status);
    assert.equal((await h.run()).submitted, false);
    assert.ok(!h.events.some(e => e.type === "click" || e.type === "progress"));
  }
});

test("Meta cancellation requests Stop without retrying the submission", async () => {
  const h = harness({provider:"meta",completeAt:30000,stopUntil:30000,cancelAt:2000});
  const result = await h.run();
  assert.equal(result.status, "submission_unknown");
  assert.equal(result.submitted, null);
  assert.ok(h.events.some(e => e.type === "stop"));
  assert.equal(h.events.filter(e => e.type === "click").length, 1);
});

test("Kimi writes its editor and waits through a valid JSON pause for its own completed toolbar", async () => {
  const h = harness({provider:"kimi",completeAt:65000,timeoutMs:90000,
    partialText:'{"kind":"final","content":"interim"}'});
  const result = await h.run();
  assert.equal(result.provider, "kimi");
  assert.equal(result.status, "success");
  assert.equal(JSON.parse(result.text).content, "sample");
  assert.ok(h.elapsed() >= 65000);
  assert.equal(h.events.filter(e => e.type === "click").length, 1);
});

test("Kimi requires completion evidence even when text is stable and Stop is absent", async () => {
  const h = harness({provider:"kimi",missingCompletion:true,timeoutMs:10000});
  assert.equal((await h.run()).status, "submission_unknown");
});

test("Kimi's Stop control prevents early completion", async () => {
  const h = harness({provider:"kimi",stopUntil:8000});
  assert.equal((await h.run()).status, "success");
  assert.ok(h.elapsed() >= 8000);
});

test("Kimi's CSS-disabled Send cannot submit until enabled", async () => {
  const h = harness({provider:"kimi",readyAt:5000});
  assert.equal((await h.run()).status, "success");
  const clickedAt = h.events.find(e => e.type === "click").at;
  assert.ok(clickedAt >= 5000 && clickedAt < 5100);
});

test("Kimi reports sign-in, verification and quota states without submission", async () => {
  for (const [options,status] of [
    [{pathname:"/login",noEditor:true},"login_required"],
    [{verification:true},"verification_required"],
    [{limitAt:0},"rate_limited"],
  ]) {
    const h = harness({provider:"kimi",...options});
    assert.equal((await h.probe()).status, status);
    assert.equal((await h.run()).submitted, false);
    assert.ok(!h.events.some(e => e.type === "click" || e.type === "progress"));
  }
});

test("Kimi refuses a fresh request while the composer is already loading", async () => {
  const h = harness({provider:"kimi",initialBusy:true});
  assert.equal((await h.run()).status, "ui_changed");
  assert.ok(!h.events.some(e => e.type === "click"));
});

test("Kimi cancellation requests Stop and remains an unknown submission", async () => {
  const h = harness({provider:"kimi",completeAt:30000,stopUntil:30000,cancelAt:2000});
  const result = await h.run();
  assert.equal(result.status, "submission_unknown");
  assert.equal(result.submitted, null);
  assert.ok(h.events.some(e => e.type === "stop"));
});

test("Gemini writes its editor and waits through a valid JSON pause for its own completed toolbar", async () => {
  const h = harness({provider:"gemini",completeAt:65000,timeoutMs:90000,
    partialText:'{"kind":"final","content":"interim"}'});
  const result = await h.run();
  assert.equal(result.provider, "gemini");
  assert.equal(result.status, "success");
  assert.equal(JSON.parse(result.text).content, "sample");
  assert.ok(h.elapsed() >= 65000);
  assert.equal(h.events.filter(e => e.type === "click").length, 1);
});

test("Gemini requires completion evidence even when text is stable and Stop is absent", async () => {
  const h = harness({provider:"gemini",missingCompletion:true,timeoutMs:10000});
  assert.equal((await h.run()).status, "submission_unknown");
});

test("Gemini's Stop control prevents early completion", async () => {
  const h = harness({provider:"gemini",stopUntil:8000});
  assert.equal((await h.run()).status, "success");
  assert.ok(h.elapsed() >= 8000);
});

test("Gemini's disabled Send cannot submit until enabled", async () => {
  const h = harness({provider:"gemini",readyAt:5000});
  assert.equal((await h.run()).status, "success");
  assert.equal(h.events.find(e => e.type === "click").at, 5000);
});

test("Gemini reports sign-in, verification and quota states without submission", async () => {
  for (const [options,status] of [
    [{pathname:"/login",noEditor:true},"login_required"],
    [{verification:true},"verification_required"],
    [{limitAt:0},"rate_limited"],
  ]) {
    const h = harness({provider:"gemini",...options});
    assert.equal((await h.probe()).status, status);
    assert.equal((await h.run()).submitted, false);
    assert.ok(!h.events.some(e => e.type === "click" || e.type === "progress"));
  }
});

test("Gemini cancellation requests Stop and remains an unknown submission", async () => {
  const h = harness({provider:"gemini",completeAt:30000,stopUntil:30000,cancelAt:2000});
  const result = await h.run();
  assert.equal(result.status, "submission_unknown");
  assert.equal(result.submitted, null);
  assert.ok(h.events.some(e => e.type === "stop"));
});

test("Gemini cannot complete without an explicit aria-busy state", async () => {
  const h = harness({provider:"gemini",missingBusy:true,timeoutMs:10000});
  assert.equal((await h.run()).status, "submission_unknown");
});

test("Meta probe recovers background hydration and releases focus without submitting", async () => {
  const h = harness({provider:"meta",background:true,hydrateOnFocus:true});
  assert.equal((await h.probe()).status, "success");
  assert.equal(h.events.filter(e => e.type === "focus_for_send").length, 1);
  assert.ok(h.events.some(e => e.type === "release_send_focus"));
  assert.ok(!h.events.some(e => ["insert", "progress", "click"].includes(e.type)));
});

test("Meta waits for a late hydrated editor beyond the old eight-second limit", async () => {
  const h = harness({provider:"meta",editorAt:12000});
  assert.equal((await h.run()).status, "success");
  assert.equal(h.events.filter(e => e.type === "click").length, 1);
});

test("Meta readiness respects a shorter deadline without inserting or submitting", async () => {
  const h = harness({provider:"meta",editorAt:12000,timeoutMs:3000});
  assert.equal((await h.run()).submitted, false);
  assert.equal(h.elapsed(),3000);
  assert.ok(!h.events.some(e => ["insert", "progress", "click"].includes(e.type)));
});

test("Meta readiness releases a focus lease on timeout and stops on rejected activation", async () => {
  for (const options of [{noEditor:true},{hydrateOnFocus:true,focusRejected:true}]) {
    const h = harness({provider:"meta",background:true,...options});
    assert.equal((await h.probe()).status, "ui_changed");
    assert.equal(h.events.filter(e => e.type === "focus_for_send").length,1);
    if (!options.focusRejected) assert.ok(h.events.some(e => e.type === "release_send_focus"));
    assert.ok(!h.events.some(e => e.type === "click"));
  }
});

test("Meta waits for native editor commit and does not dispatch duplicate text input", async () => {
  const h = harness({provider:"meta",lexicalInput:true});
  assert.equal((await h.run()).status,"success");
  assert.equal(h.events.filter(e => e.type === "click").length,1);
  assert.equal(h.events.find(e => e.type === "click").text,prompt);
  assert.ok(!h.events.some(e => e.type === "duplicate_input"));
});

test("Kimi waits for native editor commit and does not dispatch duplicate text input", async () => {
  const h = harness({provider:"kimi",lexicalInput:true});
  assert.equal((await h.run()).status,"success");
  assert.equal(h.events.filter(e => e.type === "click").length,1);
  assert.equal(h.events.find(e => e.type === "click").text,prompt);
  assert.ok(!h.events.some(e => e.type === "duplicate_input"));
});
