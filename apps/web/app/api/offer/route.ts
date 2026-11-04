import { NextRequest, NextResponse } from "next/server";
export const dynamic = "force-dynamic";
const agent = "http://127.0.0.1:7860";

async function proxy(request: NextRequest) {
  const body = await request.text();
  if (Buffer.byteLength(body) > 256_000) return NextResponse.json({ detail: "Signaling request too large" }, { status: 413 });
  const origin = request.headers.get("origin");
  const forwardedHost = request.headers.get("x-forwarded-host") || request.headers.get("host");
  if (!origin || new URL(origin).host !== forwardedHost) {
    return NextResponse.json({ detail: "Same-origin signaling is required" }, { status: 403 });
  }
  try {
    const response = await fetch(`${agent}/api/offer`, {
      method: request.method, body, cache: "no-store",
      headers: { "content-type": "application/json", origin },
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
