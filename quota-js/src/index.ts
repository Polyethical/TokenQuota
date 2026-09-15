/**
 * tokenquota: client for tokenquota-server.
 *
 * Serverless and edge runtimes (Next.js, Vercel) cannot hold shared state,
 * so budgets live in tokenquota-server + Redis and this client talks to it.
 */

export type Unit = "tokens" | "usd";

export interface QuotaUsage {
  user_id: string;
  plan: string;
  unit: Unit;
  /** Tokens, or US dollars for usd plans. */
  limit: number;
  used: number;
  reserved: number;
  remaining: number;
  period: string;
  /** Unix seconds, or null for plans that never reset. */
  resets_at: number | null;
}

export interface ReserveRequest {
  userId: string;
  /** Plan name from the server config. Omit to use the server's default plan. */
  plan?: string;
  /** The model you want. The reservation may hand back a cheaper one. */
  model?: string;
  /** Total token estimate. Use your max output tokens plus a prompt estimate. */
  estTokens?: number;
  estInputTokens?: number;
  estOutputTokens?: number;
  /** Seconds before an uncommitted reservation is released. */
  ttl?: number;
}

export interface TokenCounts {
  inputTokens: number;
  outputTokens: number;
}

export interface QuotaClientOptions {
  /** e.g. https://quota.internal.example.com */
  baseUrl: string;
  apiKey?: string;
  /** If the server can't be reached: true = allow the request (default), false = throw. */
  failOpen?: boolean;
  timeoutMs?: number;
  fetch?: typeof fetch;
}

export class QuotaError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "QuotaError";
  }
}

/** The user is out of budget. Show them an upgrade prompt, not a stack trace. */
export class QuotaExceededError extends QuotaError {
  readonly usage: QuotaUsage;
  readonly retryAfter: number | null;
  constructor(usage: QuotaUsage, retryAfter: number | null) {
    super(`quota exceeded for user ${usage.user_id} on plan ${usage.plan}`);
    this.name = "QuotaExceededError";
    this.usage = usage;
    this.retryAfter = retryAfter;
  }
}

/** The server rejected the request (bad API key, unknown plan or model). Never fails open. */
export class QuotaRequestError extends QuotaError {
  readonly status: number;
  readonly code: string;
  constructor(status: number, code: string, detail: string) {
    super(`${code}: ${detail}`);
    this.name = "QuotaRequestError";
    this.status = status;
    this.code = code;
  }
}

/** The server was unreachable and failOpen is false. */
export class QuotaBackendError extends QuotaError {
  constructor(message: string) {
    super(message);
    this.name = "QuotaBackendError";
  }
}

type Json = Record<string, unknown>;

function num(v: unknown): number | undefined {
  return typeof v === "number" && Number.isFinite(v) ? v : undefined;
}

/**
 * Pull token counts out of any provider's usage object: Vercel AI SDK
 * (inputTokens/outputTokens or promptTokens/completionTokens), OpenAI
 * (prompt_tokens/completion_tokens or input_tokens/output_tokens) and
 * Anthropic (including prompt-cache fields).
 */
export function extractTokens(usage: unknown): TokenCounts {
  if (!usage || typeof usage !== "object") throw new QuotaError("usage object is missing");
  const u = usage as Json;
  const pick = (...names: string[]) => {
    for (const n of names) {
      const v = num(u[n]);
      if (v !== undefined) return v;
    }
    return undefined;
  };
  const input = pick("inputTokens", "promptTokens", "input_tokens", "prompt_tokens");
  const output = pick("outputTokens", "completionTokens", "output_tokens", "completion_tokens");
  if (input === undefined && output === undefined) {
    const total = pick("totalTokens", "total_tokens");
    if (total === undefined) throw new QuotaError("could not find token counts in usage object");
    return { inputTokens: total, outputTokens: 0 };
  }
  const cache = (pick("cache_creation_input_tokens") ?? 0) + (pick("cache_read_input_tokens") ?? 0);
  return { inputTokens: (input ?? 0) + cache, outputTokens: output ?? 0 };
}

function toCounts(usage: unknown): TokenCounts {
  const u = usage as Partial<TokenCounts> | undefined;
  if (u && typeof u === "object" && ("inputTokens" in u || "outputTokens" in u)) {
    return { inputTokens: u.inputTokens ?? 0, outputTokens: u.outputTokens ?? 0 };
  }
  return extractTokens(usage);
}

export class Reservation {
  /** Signed reservation token, or null if the server was unreachable (fail-open). */
  readonly token: string | null;
  /** Call the model with this. It may be cheaper than the one you asked for. */
  readonly model: string | null;
  readonly requestedModel: string | null;
  readonly degraded: boolean;
  /** false when the budget could not be checked (server down, failOpen). */
  readonly checked: boolean;
  readonly usage: QuotaUsage | null;
  private closed = false;

  constructor(
    private readonly client: QuotaClient,
    private readonly req: ReserveRequest,
    data: { token: string | null; model: string | null; requestedModel: string | null; degraded: boolean; checked: boolean; usage: QuotaUsage | null },
  ) {
    this.token = data.token;
    this.model = data.model;
    this.requestedModel = data.requestedModel;
    this.degraded = data.degraded;
    this.checked = data.checked;
    this.usage = data.usage;
  }

  /** Record actual usage. Pass the provider's usage object or explicit counts. */
  async commit(usage: unknown): Promise<QuotaUsage | null> {
    this.close();
    const { inputTokens, outputTokens } = toCounts(usage);
    if (this.token === null) {
      return this.client.record({
        userId: this.req.userId,
        plan: this.req.plan,
        model: this.model ?? undefined,
        inputTokens,
        outputTokens,
      });
    }
    const body = await this.client._send("/v1/commit", {
      reservation: this.token,
      input_tokens: inputTokens,
      output_tokens: outputTokens,
    });
    return (body?.usage as QuotaUsage) ?? null;
  }

  /** Give the budget back, e.g. when the model call failed before billing. */
  async release(): Promise<void> {
    this.close();
    if (this.token !== null) await this.client._send("/v1/release", { reservation: this.token });
  }

  private close() {
    if (this.closed) throw new QuotaError("reservation already committed or released");
    this.closed = true;
  }
}

export class QuotaClient {
  private readonly baseUrl: string;
  private readonly apiKey?: string;
  private readonly failOpen: boolean;
  private readonly timeoutMs: number;
  private readonly fetchImpl: typeof fetch;

  constructor(opts: QuotaClientOptions) {
    this.baseUrl = opts.baseUrl.replace(/\/+$/, "");
    this.apiKey = opts.apiKey;
    this.failOpen = opts.failOpen ?? true;
    this.timeoutMs = opts.timeoutMs ?? 2000;
    this.fetchImpl = opts.fetch ?? globalThis.fetch.bind(globalThis);
  }

  /** Hold budget for one model call. Throws QuotaExceededError when the user is out. */
  async reserve(req: ReserveRequest): Promise<Reservation> {
    let body: Json | null;
    try {
      body = await this._send("/v1/reserve", {
        user_id: req.userId,
        plan: req.plan,
        model: req.model,
        est_tokens: req.estTokens,
        est_input_tokens: req.estInputTokens,
        est_output_tokens: req.estOutputTokens,
        ttl: req.ttl,
      });
    } catch (err) {
      if (err instanceof QuotaBackendError && this.failOpen) {
        console.warn(`[tokenquota] ${err.message}; allowing request (failOpen)`);
        return new Reservation(this, req, {
          token: null,
          model: req.model ?? null,
          requestedModel: req.model ?? null,
          degraded: false,
          checked: false,
          usage: null,
        });
      }
      throw err;
    }
    return new Reservation(this, req, {
      token: body!.reservation as string,
      model: (body!.model as string | null) ?? null,
      requestedModel: (body!.requested_model as string | null) ?? null,
      degraded: Boolean(body!.degraded),
      checked: Boolean(body!.checked),
      usage: (body!.usage as QuotaUsage | null) ?? null,
    });
  }

  /** Add usage without a reservation (e.g. from a billing webhook). */
  async record(req: {
    userId: string;
    plan?: string;
    model?: string;
    inputTokens?: number;
    outputTokens?: number;
    idempotencyKey?: string;
  }): Promise<QuotaUsage | null> {
    try {
      const body = await this._send("/v1/record", {
        user_id: req.userId,
        plan: req.plan,
        model: req.model,
        input_tokens: req.inputTokens ?? 0,
        output_tokens: req.outputTokens ?? 0,
        idempotency_key: req.idempotencyKey,
      });
      return (body?.usage as QuotaUsage) ?? null;
    } catch (err) {
      if (err instanceof QuotaBackendError && this.failOpen) {
        console.warn(`[tokenquota] usage not recorded: ${err.message}`);
        return null;
      }
      throw err;
    }
  }

  async usage(userId: string, plan?: string): Promise<QuotaUsage> {
    const q = plan ? `?plan=${encodeURIComponent(plan)}` : "";
    const body = await this._send(`/v1/usage/${encodeURIComponent(userId)}${q}`, undefined, "GET");
    return body!.usage as QuotaUsage;
  }

  /**
   * Reserve, run your model call, commit the real usage, or release on error.
   *
   *   const { text } = await quota.withQuota(
   *     { userId, model: "model-large", estTokens: 2000 },
   *     (r) => generateText({ model: openai(r.model!), prompt }),
   *     (result) => result.usage,
   *   );
   */
  async withQuota<T>(req: ReserveRequest, run: (r: Reservation) => Promise<T>, usageOf: (result: T) => unknown): Promise<T> {
    const r = await this.reserve(req);
    let result: T;
    try {
      result = await run(r);
    } catch (err) {
      await r.release().catch(() => undefined);
      throw err;
    }
    await r.commit(usageOf(result));
    return result;
  }

  /** @internal */
  async _send(path: string, payload?: Json, method = "POST"): Promise<Json | null> {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), this.timeoutMs);
    let res: Response;
    try {
      res = await this.fetchImpl(`${this.baseUrl}${path}`, {
        method,
        headers: {
          "content-type": "application/json",
          ...(this.apiKey ? { authorization: `Bearer ${this.apiKey}` } : {}),
        },
        body: payload === undefined ? undefined : JSON.stringify(payload),
        signal: controller.signal,
      });
    } catch (err) {
      throw new QuotaBackendError(`quota server unreachable: ${(err as Error).message}`);
    } finally {
      clearTimeout(timer);
    }
    const text = await res.text();
    let body: Json | null = null;
    try {
      body = text ? (JSON.parse(text) as Json) : null;
    } catch {
      body = null;
    }
    if (res.ok) return body;
    if (res.status === 429 && body?.usage) {
      throw new QuotaExceededError(body.usage as QuotaUsage, (body.retry_after as number | null) ?? null);
    }
    if (res.status >= 500) {
      throw new QuotaBackendError(`quota server error ${res.status}: ${text.slice(0, 200)}`);
    }
    const detail = typeof body?.detail === "string" ? body.detail : JSON.stringify(body?.detail ?? text);
    throw new QuotaRequestError(res.status, (body?.error as string) ?? "http_error", detail);
  }
}
