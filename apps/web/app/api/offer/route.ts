import { NextRequest, NextResponse } from "next/server";
export const dynamic = "force-dynamic";
const agent = "http://127.0.0.1:7860";

async function proxy(request: NextRequest) {
  const origin = request.headers.get("origin");
  const forwardedHost = request.headers.get("x-forwarded-host") || request.headers.get("host");
  let validOrigin = false;
  try { validOrigin = !!origin && new URL(origin).host === forwardedHost; } catch {}
  if (!validOrigin) {
    return NextResponse.json({ detail: "Same-origin signaling is required" }, { status: 403 });
  }
  const reader = request.body?.getReader();
  const chunks: Uint8Array[] = [];
  let bytes = 0;
  if (reader) {
    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      bytes += value.byteLength;
      if (bytes > 256_000) { await reader.cancel(); return NextResponse.json({ detail: "Signaling request too large" }, { status: 413 }); }
      chunks.push(value);
    }
  }
  const body = Buffer.concat(chunks);
  try {
    const response = await fetch(`${agent}/api/offer`, {
      method: request.method, body, cache: "no-store",
      headers: { "content-type": "application/json", origin: origin! },
      signal: AbortSignal.timeout(20_000),
    });
    return new NextResponse(await response.text(), { status: response.status, headers: { "content-type": "application/json", "cache-control": "no-store" } });
  } catch {
    return NextResponse.json({ detail: "The local agent is unavailable. Check the agent terminal." }, { status: 502 });
  }
}
export const POST = proxy;
export const PATCH = proxy;
export const DELETE = proxy;
