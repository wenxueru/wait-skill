import assert from "node:assert/strict";
import test from "node:test";
import {sessionUrl} from "../../assets/session-picker.mjs";

test("session switching preserves the original session and view, clearing stale task focus", () => {
  const initial = new URLSearchParams("session=first&mode=goal&view=graph&focus=/first.json&node=work");
  const switched = new URLSearchParams(sessionUrl(initial, "second").slice(2));
  assert.equal(switched.get("session"), "second");
  assert.equal(switched.get("home"), "first");
  assert.equal(switched.get("mode"), "goal");
  assert.equal(switched.get("view"), "graph");
  assert.equal(switched.has("focus"), false);
  assert.equal(switched.has("node"), false);
  const all = new URLSearchParams(sessionUrl(switched, null).slice(2));
  assert.equal(all.get("all"), "1");
  assert.equal(all.get("home"), "first");
  const returned = new URLSearchParams(sessionUrl(all, all.get("home")).slice(2));
  assert.equal(returned.has("all"), false);
  assert.equal(returned.get("session"), "first");
  assert.equal(initial.get("focus"), "/first.json");
  assert.equal(new URLSearchParams(sessionUrl(new URLSearchParams(), null).slice(2)).get("all"), "1");
});
