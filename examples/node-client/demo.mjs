// Talks to a local tokenquota-server. Start one first:
//   cd ../../quota-server && docker compose up
// then:
//   npm install tokenquota
//   TOKENQUOTA_API_KEY=dev-key node demo.mjs
import { QuotaClient, QuotaExceededError } from "tokenquota";

const quota = new QuotaClient({
  baseUrl: process.env.TOKENQUOTA_URL ?? "http://localhost:8080",
  apiKey: process.env.TOKENQUOTA_API_KEY,
});

const fakeModel = async (model) => ({ inputTokens: 900 + Math.floor(Math.random() * 300), outputTokens: 1500 + Math.floor(Math.random() * 1000) });
const userId = `demo-${Date.now()}`;

for (let i = 1; i <= 20; i++) {
  try {
    const r = await quota.reserve({ userId, model: "model-large", estTokens: 3700 });
    const usage = await fakeModel(r.model);
    const after = await r.commit(usage);
    console.log(`request ${String(i).padStart(2)}: ${r.degraded ? "degraded -> " : "            "}${r.model.padEnd(12)} ${after.used} / ${after.limit} tokens`);
  } catch (err) {
    if (err instanceof QuotaExceededError) {
      console.log(`request ${String(i).padStart(2)}: BLOCKED     ${err.usage.used} / ${err.usage.limit} tokens`);
      continue;
    }
    throw err;
  }
}
