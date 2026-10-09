import {test} from "node:test";
import assert from "node:assert/strict";
import {readFile} from "node:fs/promises";
import vm from "node:vm";

const source = await readFile(new URL("../dist/content.js", import.meta.url), "utf8");
const prompt = 'Instructions\nREQUEST:\n{"messages":[{"role":"user","content":"sample"}]}';

function harness(options = {}) {
  let now = 0, listener, selected, clicked = false, activated = false;
  const events = [];
  class Element {
    constructor(text = "") { this.innerText = text; }
    getClientRects() { return [{}]; }
    getAttribute() { return null; }
    hasAttribute() { return false; }
    querySelector() { return null; }
    focus() { selected = this; }
    dispatchEvent() {}
  }
  const original = new Element(), replacement = new Element(options.replacementText ?? "");
  const editor = () => now >= (options.replaceAt ?? Infinity) ? replacement : original;
  const ready = () => now >= (options.readyAt ?? 0) && editor().innerText === prompt;
  const send = new Element();
  send.hasAttribute = name => name === "disabled" && !ready();
  send.click = () => {
    assert.ok(ready(), "must not click a disabled button or send a lost draft");
    assert.equal(events.at(-1)?.type, "progress", "durable acknowledgement precedes click");
    events.push({type: "click", text: editor().innerText, at: now});
    clicked = true;
  };
  const answer = new Element();
  answer.querySelectorAll = () => [{textContent: '{"kind":"final","content":"sample"}'}];
  const notice = new Element("You have reached your message limit");
  const document = {
    get visibilityState() { return options.background && !activated ? "hidden" : "visible"; },
    createRange: () => ({selectNodeContents() {}}),
    execCommand: (_name, _ui, text) => {
      selected.innerText = text;
      events.push({type: "insert", at: now});
      return true;
    },
    querySelectorAll: selector => {
      if (selector.includes("contenteditable")) return [editor()];
      if (selector.includes("chat-input-send")) return [send];
      if (selector.includes("assistant-message")) return clicked ? [answer] : [];
      if (selector.includes("rate-limit-warning") && now >= (options.limitAt ?? Infinity)) return [notice];
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
    document, chrome, location: {hostname: "claude.ai", pathname: "/new"},
    Date: {now: () => now}, HTMLElement: Element, HTMLTextAreaElement: class {},
    InputEvent: class {}, getComputedStyle: element => ({visibility:
      element === send && options.background && !activated ? "hidden" : "visible"}),
    getSelection: () => ({removeAllRanges() {}, addRange() {}}),
    setTimeout: (callback, delay) => setImmediate(() => {
      now += delay;
      if (now >= (options.cancelAt ?? Infinity)) listener({type: "cancel", attemptId: "sample"}, {}, () => {});
      callback();
    }),
  });
  return {events, elapsed: () => now, run: () => new Promise(resolve => listener({
    type: "run", attemptId: "sample", provider: "claude", slot: 0,
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
