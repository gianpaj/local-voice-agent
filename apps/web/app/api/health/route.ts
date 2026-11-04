import { NextResponse } from "next/server";
export const dynamic = "force-dynamic";
export async function GET() {
  try {
    const response = await fetch("http://127.0.0.1:7860/health", { cache: "no-store", signal: AbortSignal.timeout(2000) });
    return NextResponse.json(await response.json(), { headers: { "cache-control": "no-store" } });
  } catch {
    return NextResponse.json({ ready: false, phase: "offline", error: "The local agent is not running." }, { status: 503 });
  }
}
