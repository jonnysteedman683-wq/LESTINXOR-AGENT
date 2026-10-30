// UniSync gateway: per-agent tokens in front of the unisync_events ledger.
//
// Serves the same paths and semantics the clients already use against PostgREST
// (GET/POST <base>/rest/v1/unisync_events), so agents only change their URL and key:
//   UNISYNC_SUPABASE_URL = https://<ref>.supabase.co/functions/v1/unisync
//   UNISYNC_SUPABASE_KEY = that agent's own token (only its sha256 is stored, in unisync_tokens)
// A token can only post as its own agent, only the kinds listed for it, only on top of the
// current head, and only with a digest that matches the canonical envelope. Revoke with
// `update unisync_tokens set revoked_at = now() where agent = '...'`.
import { createClient } from "npm:@supabase/supabase-js@2";

const db = createClient(Deno.env.get("SUPABASE_URL")!, Deno.env.get("SUPABASE_SERVICE_ROLE_KEY")!, {
  auth: { persistSession: false },
});
const COLS = "v,seq,sender,kind,task_id,body,prev,digest,ts";
const GENESIS = "0".repeat(64);
const REQUIRED: Record<string, string[]> = {
  "task.request": ["title"], "task.claim": [], "task.result": ["summary"], "task.failed": ["reason"],
  "memory.write": ["key", "value"], "approval.request": ["action"], "approval.decision": ["approved"],
  "escalate": ["reason"], "heartbeat": [],
};
const TASK_FREE = new Set(["heartbeat", "memory.write", "escalate"]);

const reply = (status: number, body?: unknown) =>
  new Response(body === undefined ? null : JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });

// Python's json.dumps(obj, sort_keys=True, separators=(",", ":")) with ensure_ascii=True.
function pyStr(s: string): string {
  return JSON.stringify(s).replace(/[\u007f-\uffff]/g, (c) => "\\u" + c.charCodeAt(0).toString(16).padStart(4, "0"));
}
function canon(x: unknown): string {
  if (x === null) return "null";
  if (typeof x === "boolean") return x ? "true" : "false";
  if (typeof x === "number") {
    if (!Number.isInteger(x)) throw new Error("non-integer numbers are not accepted in envelopes");
    return String(x);
  }
  if (typeof x === "string") return pyStr(x);
  if (Array.isArray(x)) return "[" + x.map(canon).join(",") + "]";
  if (typeof x === "object") {
    const o = x as Record<string, unknown>;
    return "{" + Object.keys(o).sort().map((k) => pyStr(k) + ":" + canon(o[k])).join(",") + "}";
  }
  throw new Error("unsupported value");
}
async function sha256(s: string): Promise<string> {
  const buf = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(s));
  return [...new Uint8Array(buf)].map((b) => b.toString(16).padStart(2, "0")).join("");
}

Deno.serve(async (req) => {
  const url = new URL(req.url);
  if (!url.pathname.endsWith("/rest/v1/unisync_events")) return reply(404, { error: "not found" });
  const auth = req.headers.get("authorization") ?? "";
  const token = auth.startsWith("Bearer ") ? auth.slice(7).trim() : "";
  if (!token) return reply(401, { error: "missing token" });
  const { data: who } = await db.from("unisync_tokens").select("agent,kinds")
    .eq("token_hash", await sha256(token)).is("revoked_at", null).maybeSingle();
  if (!who) return reply(401, { error: "invalid or revoked token" });

  if (req.method === "GET") {
    let q = db.from("unisync_events").select(COLS);
    const seq = url.searchParams.get("seq");
    if (seq?.startsWith("gte.")) q = q.gte("seq", Number(seq.slice(4)));
    q = q.order("seq", { ascending: url.searchParams.get("order") !== "seq.desc" });
    q = q.limit(Math.min(Math.max(Number(url.searchParams.get("limit") ?? 1000) || 1000, 1), 1000));
    const { data, error } = await q;
    return error ? reply(500, { error: error.message }) : reply(200, data);
  }
  if (req.method !== "POST") return reply(405, { error: "GET or POST only" });

  let row: Record<string, unknown>;
  try {
    row = await req.json();
  } catch {
    return reply(400, { error: "body must be JSON" });
  }
  const { v, seq, sender, kind, task_id, body, prev, digest, ts } = row as Record<string, any>;
  if (v !== "unisync/1") return reply(400, { error: "unsupported protocol version" });
  if (sender !== who.agent) return reply(403, { error: `token is for ${who.agent}, not ${sender}` });
  if (!who.kinds.includes(kind)) return reply(403, { error: `${who.agent} may not post ${kind}` });
  if (!Number.isInteger(seq) || seq < 0 || typeof ts !== "number" || typeof task_id !== "string") {
    return reply(400, { error: "bad envelope fields" });
  }
  if (body === null || typeof body !== "object" || Array.isArray(body)) return reply(400, { error: "body must be an object" });
  const missing = (REQUIRED[kind] ?? []).filter((k) => !(k in body));
  if (missing.length) return reply(400, { error: `${kind} missing body field(s): ${missing.join(", ")}` });
  if (TASK_FREE.has(kind) && task_id !== "") return reply(400, { error: `${kind} must not carry a task_id` });

  let expect: string;
  try {
    expect = await sha256(canon({ v, seq, sender, kind, task_id, body, prev }));
  } catch (e) {
    return reply(400, { error: String((e as Error).message) });
  }
  if (digest !== expect) return reply(400, { error: "digest does not match the canonical envelope" });

  const { data: head } = await db.from("unisync_events").select("seq,digest").order("seq", { ascending: false })
    .limit(1).maybeSingle();
  const [nextSeq, headDigest] = head ? [Number(head.seq) + 1, head.digest] : [0, GENESIS];
  if (seq !== nextSeq || prev !== headDigest) return reply(409, { error: "not at the current head; re-read and retry" });

  const { error } = await db.from("unisync_events").insert({ v, seq, sender, kind, task_id, body, prev, digest, ts });
  if (error) return error.code === "23505" ? reply(409, { error: "lost the race; re-read and retry" }) : reply(500, { error: error.message });
  return reply(201);
});
