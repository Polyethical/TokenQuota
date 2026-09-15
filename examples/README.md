# tokenquota examples

| Example | Needs | What it shows |
|---|---|---|
| [offline-demo](offline-demo/demo.py) | nothing | Allow, then degrade to a cheaper model, then block, with a fake model. Start here. |
| [openai-python](openai-python/chat.py) | OpenAI key | Token budget around the OpenAI SDK, with release on failure. |
| [anthropic-python](anthropic-python/chat.py) | Anthropic key | Dollar budget around the Anthropic SDK, including prompt-cache tokens. |
| [stripe-plans](stripe-plans/plans.py) | Stripe test key | Choose each user's plan from their Stripe subscription. |
| [node-client](node-client/demo.mjs) | a running quota-server | The TypeScript client against the HTTP server. |
| [nextjs-route](nextjs-route/app/api/chat/route.ts) | Next.js app, quota-server | Drop-in App Router route using the Vercel AI SDK. |

```bash
pip install tokenquota
python offline-demo/demo.py
```

The model names in the examples are placeholders or defaults you should check against your provider's current model list. The Anthropic example refuses to run until you fill in real prices in `prices.json`.

The Next.js route targets AI SDK 5 (`maxOutputTokens`). On AI SDK 4, use `maxTokens` instead; `commit()` understands both usage formats.
