// Next.js App Router route with a per-user budget, using the Vercel AI SDK.
//   npm install tokenquota ai @ai-sdk/openai
// Env: TOKENQUOTA_URL, TOKENQUOTA_API_KEY, MODEL_LARGE, MODEL_SMALL
// The model names must match degrade_to in your tokenquota-server config.
import { generateText } from "ai";
import { openai } from "@ai-sdk/openai";
import { QuotaClient, QuotaExceededError } from "tokenquota";

const quota = new QuotaClient({
  baseUrl: process.env.TOKENQUOTA_URL!,
  apiKey: process.env.TOKENQUOTA_API_KEY,
});

const MAX_OUTPUT = 800;

export async function POST(req: Request) {
  const { prompt } = await req.json();
  const userId = req.headers.get("x-user-id") ?? "anonymous"; // replace with your auth session

  try {
    const { text } = await quota.withQuota(
      { userId, model: process.env.MODEL_LARGE, estInputTokens: Math.ceil(prompt.length / 3) + 50, estOutputTokens: MAX_OUTPUT },
      (r) => generateText({ model: openai(r.model!), prompt, maxOutputTokens: MAX_OUTPUT }),
      (result) => result.usage,
    );
    return Response.json({ text });
  } catch (err) {
    if (err instanceof QuotaExceededError) {
      return Response.json(
        { error: "You've used this month's AI allowance.", resetsAt: err.usage.resets_at },
        { status: 429, headers: err.retryAfter ? { "Retry-After": String(err.retryAfter) } : {} },
      );
    }
    throw err;
  }
}

// Streaming: reserve first, then commit inside streamText's onFinish callback:
//   const r = await quota.reserve({ userId, model, estTokens });
//   const result = streamText({ model: openai(r.model!), prompt,
//     onFinish: ({ usage }) => r.commit(usage), onError: () => r.release() });
//   return result.toTextStreamResponse();
