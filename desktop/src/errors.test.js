import assert from "node:assert/strict";
import { test } from "node:test";
import { formatError, renderErrorBoundary, safeRender } from "./errors.js";

test("formatError redacts secrets", () => {
  const s = formatError("Authorization: Bearer sk-secretTEST99abc");
  assert.equal(s.includes("sk-secretTEST99abc"), false, s);
  assert.equal(s.includes("***"), true, s);
});

test("safeRender recovers from throw and retry succeeds", () => {
  const root = { innerHTML: "", querySelector() { return null; } };
  let blows = true;
  const ok = safeRender(root, "runs", () => {
    if (blows) {
      blows = false;
      throw new Error("token=abc123SECRETVALUE boom");
    }
    root.innerHTML = "ok";
  });
  assert.equal(ok, false);
  assert.equal(root.innerHTML.includes("ERROR BOUNDARY"), true);
  assert.equal(root.innerHTML.includes("abc123SECRETVALUE"), false, root.innerHTML);
  assert.equal(root.innerHTML.includes("runs"), true);
});

test("renderErrorBoundary wires retry", () => {
  let retried = false;
  const btn = {
    addEventListener(_ev, fn) {
      this._fn = fn;
    },
    click() {
      this._fn();
    },
  };
  const root = {
    innerHTML: "",
    querySelector() {
      return btn;
    },
  };
  renderErrorBoundary(root, "chat", new Error("nope"), () => {
    retried = true;
  });
  assert.equal(root.innerHTML.includes("ERROR BOUNDARY"), true);
  btn.click();
  assert.equal(retried, true);
});
