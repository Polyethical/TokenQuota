import { test } from "node:test";
import assert from "node:assert/strict";
import {
  QuotaClient,
  QuotaExceededError,
  QuotaRequestError,
  QuotaBackendError,
  extractTokens,
} from "../dist/index.js";

const usage = { user_id: "u1", plan: "free", unit: "tokens", limit: 100, used: 0, reserved: 10, remaining: 90, period: "2026-09", resets_at: 1790000000 };

function fakeFetch(routes, calls = []) {
  return async (url, init) => {
    const path = new URL(url).pathname;
    calls.push({ path, body: init.body ? JSON.parse(init.body) : undefined, headers: init.headers });
    const handler = routes[path];
    if (!handler) return new Response("not found", { status: 404 });
    const [status, body] = handler(init.body ? JSON.parse(init.body) : undefined);
    return new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });
  };
}

test("reserve and commit with an AI SDK usage object", async () => {
  const calls = [];
  const client = new QuotaClient({
    baseUrl: "http://quota.test/",
    apiKey: "k",
    fetch: fakeFetch(
      {
        "/v1/reserve": () => [200, { reservation: "tok", model: "small", requested_model: "big", degraded: true, checked: true, usage }],
        "/v1/commit": () => [200, { recorded: true, usage: { ...usage, used: 12 } }],
      },
      calls,
    ),
  });
  const r = await client.reserve({ userId: "u1", model: "big", estTokens: 10 });
  assert.equal(r.model, "small");
  assert.equal(r.degraded, true);
  const after = await r.commit({ inputTokens: 7, outputTokens: 5 });
  assert.equal(after.used, 12);
  assert.deepEqual(calls[0].body, { user_id: "u1", model: "big", est_tokens: 10 });
  assert.equal(calls[0].headers.authorization, "Bearer k");
  assert.deepEqual(calls[1].body, { reservation: "tok", input_tokens: 7, output_tokens: 5 });
  await assert.rejects(r.commit({ inputTokens: 1 }), /already committed/);
});

test("429 becomes QuotaExceededError", async () => {
  const client = new QuotaClient({
    baseUrl: "http://quota.test",
    fetch: fakeFetch({ "/v1/reserve": () => [429, { error: "quota_exceeded", usage, retry_after: 60 }] }),
  });
  await assert.rejects(client.reserve({ userId: "u1", estTokens: 1 }), (err) => {
    assert.ok(err instanceof QuotaExceededError);
    assert.equal(err.retryAfter, 60);
    return true;
  });
});

test("auth and config errors never fail open", async () => {
  const client = new QuotaClient({
    baseUrl: "http://quota.test",
    fetch: fakeFetch({ "/v1/reserve": () => [401, { detail: "missing or invalid API key" }] }),
  });
  await assert.rejects(client.reserve({ userId: "u1", estTokens: 1 }), QuotaRequestError);
});

test("unreachable server fails open by default and records usage later", async () => {
  let down = true;
  const calls = [];
  const inner = fakeFetch({ "/v1/record": () => [200, { recorded: true, usage }] }, calls);
  const client = new QuotaClient({
    baseUrl: "http://quota.test",
    fetch: async (url, init) => {
      if (down) throw new TypeError("fetch failed");
      return inner(url, init);
    },
  });
  const warn = console.warn;
  console.warn = () => {};
  try {
    const r = await client.reserve({ userId: "u1", model: "big", estTokens: 10 });
    assert.equal(r.checked, false);
    assert.equal(r.model, "big");
    down = false;
    await r.commit({ input_tokens: 3, output_tokens: 4 });
    assert.deepEqual(calls[0].body, { user_id: "u1", model: "big", input_tokens: 3, output_tokens: 4 });
  } finally {
    console.warn = warn;
  }
});

test("failOpen: false throws QuotaBackendError", async () => {
  const client = new QuotaClient({
    baseUrl: "http://quota.test",
    failOpen: false,
    fetch: async () => {
      throw new TypeError("fetch failed");
    },
  });
  await assert.rejects(client.reserve({ userId: "u1", estTokens: 1 }), QuotaBackendError);
});

test("withQuota releases on error and commits on success", async () => {
  const calls = [];
  const client = new QuotaClient({
    baseUrl: "http://quota.test",
    fetch: fakeFetch(
      {
        "/v1/reserve": () => [200, { reservation: "tok", model: "big", requested_model: "big", degraded: false, checked: true, usage }],
        "/v1/release": () => [200, { usage }],
        "/v1/commit": () => [200, { usage }],
      },
      calls,
    ),
  });
  await assert.rejects(
    client.withQuota({ userId: "u1", estTokens: 1 }, async () => {
      throw new Error("provider down");
    }, () => ({})),
    /provider down/,
  );
  assert.deepEqual(calls.map((c) => c.path), ["/v1/reserve", "/v1/release"]);
  const out = await client.withQuota({ userId: "u1", estTokens: 1 }, async (r) => ({ text: "hi", usage: { promptTokens: 2, completionTokens: 1 } }), (res) => res.usage);
  assert.equal(out.text, "hi");
  assert.deepEqual(calls.at(-1).body, { reservation: "tok", input_tokens: 2, output_tokens: 1 });
});

test("extractTokens handles provider shapes", () => {
  assert.deepEqual(extractTokens({ prompt_tokens: 3, completion_tokens: 2 }), { inputTokens: 3, outputTokens: 2 });
  assert.deepEqual(extractTokens({ input_tokens: 3, output_tokens: 2, cache_read_input_tokens: 10 }), { inputTokens: 13, outputTokens: 2 });
  assert.deepEqual(extractTokens({ totalTokens: 9 }), { inputTokens: 9, outputTokens: 0 });
  assert.throws(() => extractTokens({}), /could not find/);
});
