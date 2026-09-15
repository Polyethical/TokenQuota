# tokenquota (JavaScript / TypeScript)

[![CI](https://github.com/YOUR-ORG/quota-js/actions/workflows/ci.yml/badge.svg)](https://github.com/YOUR-ORG/quota-js/actions/workflows/ci.yml)
[![npm](https://img.shields.io/npm/v/tokenquota)](https://www.npmjs.com/package/tokenquota)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue)](LICENSE)

Per-user AI budgets for Next.js, the Vercel AI SDK and any Node 18+ backend. Zero dependencies.

Serverless functions can't share state, so budgets live in [tokenquota-server](https://github.com/YOUR-ORG/quota-server) and this package is its client.

```bash
npm install tokenquota
```

## Next.js route with the Vercel AI SDK

```ts
import { generateText } from "ai";
import { openai } from "@ai-sdk/openai";
import { QuotaClient, QuotaExceededError } from "tokenquota";

const quota = new QuotaClient({ baseUrl: process.env.TOKENQUOTA_URL!, apiKey: process.env.TOKENQUOTA_API_KEY });

export async function POST(req: Request) {
  const { prompt } = await req.json();
  const userId = await getUserId(req); // your auth
  try {
    const { text } = await quota.withQuota(
      { userId, model: "model-large", estInputTokens: prompt.length / 3 + 50, estOutputTokens: 800 },
      (r) => generateText({ model: openai(r.model!), prompt, maxOutputTokens: 800 }),
      (result) => result.usage,
    );
    return Response.json({ text });
  } catch (err) {
    if (err instanceof QuotaExceededError) {
      return Response.json({ error: "Out of AI credits", resetsAt: err.usage.resets_at }, { status: 429 });
    }
    throw err;
  }
}
```

`withQuota` reserves, runs your call with the (possibly cheaper) model, commits the real usage, and releases the budget if your call throws.

## Manual control (streaming)

```ts
const r = await quota.reserve({ userId, model: "model-large", estTokens: 3000 });
const result = streamText({
  model: openai(r.model!),
  prompt,
  onFinish: ({ usage }) => r.commit(usage),
  onError: () => r.release(),
});
```

## Failure behaviour

| Situation | Behaviour |
|---|---|
| User is out of budget | `QuotaExceededError` (has `usage` and `retryAfter`) |
| Server unreachable or 5xx | Allowed with `checked: false`, usage recorded later (`failOpen: true`, default). Set `failOpen: false` to throw `QuotaBackendError`. |
| Bad API key, unknown plan or model | `QuotaRequestError`. Never fails open, so a misconfiguration can't silently disable your limits. |

`commit()` accepts Vercel AI SDK usage (`inputTokens`/`outputTokens` or `promptTokens`/`completionTokens`), OpenAI and Anthropic usage objects.

## License

MIT
