# Offline performance report

`performance-template.html` is the standalone interactive report source. It needs no CDN, dashboard service, model, or network connection. Curated **measured** metadata is embedded as inert JSON; no values are invented by the template.

```bash
pnpm report .local/reports/comparison.json
# Open .local/reports/performance.html directly, or visit /performance
# on the existing private app after its production build includes that route.
```

Refresh the HTML when an experiment finishes; it is read at request time, not baked into the Next.js bundle. The generated HTML, JSON/CSV exports, raw benchmark records and public fixtures remain under ignored `.local/`. Do not commit recordings/transcripts, machine paths or secrets.

Input schema: `{schema_version:1, generated_at, summary, caveats:[], metric_definitions:{}, comparisons:[], timeline:[]}`. Every comparison requires a unique `id`; use `component` (`stt`, `llm`, `tts`, `pipeline`, `framework`), `label`, `model`, `engine`, `mode`, `boundary`, `comparison_group`, `decision`, numeric-or-null `metrics`, `quality`, `outcomes`, `configuration`, `methodology`, `limitations`, `evidence`, and optional per-public-fixture `samples`/primary `sources`.

Each metric definition supplies `label`, `unit`, `direction` (`lower` or `higher`) and a precise `description`. Use the same comparison group only for compatible corpus/settings/timing boundaries. The chart refuses to rank mixed boundaries; the detail panel explains why a headless buffered prototype is not equivalent to a browser streaming agent. Include failures/missing values and quality regressions, not only successful medians. A prototype or optional model is not an adopted production optimization.

JSON/CSV downloads preserve the underlying numbers and nulls. Interactive filters, per-case settings, public fixture drills and evidence metadata work when opened as a local file. Only explicit primary-source links need networking.
