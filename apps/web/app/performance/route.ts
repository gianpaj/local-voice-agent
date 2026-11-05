import { readFile } from "node:fs/promises";
import { resolve } from "node:path";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

export async function GET() {
  const headers = {
    "content-type": "text/html; charset=utf-8",
    "cache-control": "no-store",
    "x-content-type-options": "nosniff",
    "referrer-policy": "no-referrer",
  };
  try {
    // pnpm --filter web runs from apps/web. The generated artifact remains
    // outside Git and can be refreshed without bundling benchmark data.
    const report = await readFile(resolve(process.cwd(), "../../.local/reports/performance.html"), "utf8");
    return new Response(report, { headers });
  } catch {
    return new Response("<!doctype html><html lang=\"en\"><meta charset=\"utf-8\"><title>Local Voice report</title><h1>The comparison report is not ready yet.</h1><p>The voice app is still available at <a href=\"/\">the home page</a>.</p></html>", { status: 503, headers });
  }
}
