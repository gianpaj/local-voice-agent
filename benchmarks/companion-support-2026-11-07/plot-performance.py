#!/usr/bin/env python3
"""Plot saved per-call counters, checking the committed summary; no inference."""

import csv
import hashlib
import importlib.util
import json
import math
from pathlib import Path
import xml.etree.ElementTree as ET

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Patch


ROOT = Path(__file__).resolve().parent
OUT = ROOT / "assets"
COLORS = {"qwen": "#2968AD", "nanbeige": "#087E75"}
ORDER = (("qwen", "eq"), ("nanbeige", "eq"), ("qwen", "retail"), ("nanbeige", "retail"))
LABELS = {"qwen": "Qwen", "nanbeige": "Nanbeige", "eq": "EQ", "retail": "Retail"}
TIMING = (
    ("first_text_seconds", "Request → first text", "Seconds · lower is better", 2, 3),
    ("server_prompt_seconds", "Server prompt processing", "Seconds · lower is better · cache-affected", 2, 3),
    ("decode_tokens_per_second", "Server decode throughput", "Model-native tokens/second · higher is better", 50, 2),
    ("wall_seconds", "Complete candidate call", "Seconds · lower is better · excludes simulator/judge/audio", 100, 2),
)


def read_evidence():
    spec = importlib.util.spec_from_file_location("saved_performance", ROOT / "performance-evidence.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    with (ROOT / "performance/candidate-call-metrics.csv").open(newline="") as stream:
        rows = list(csv.DictReader(stream))
    summary = json.loads((ROOT / "performance/summary.json").read_text())
    cohorts = {}
    for cohort in summary["cohorts"]:
        if cohort["stage"] != "all":
            continue
        key = cohort["model"], cohort["suite"]
        attempts = [r for r in rows if (r["model"], r["suite"]) == key]
        complete = [r for r in attempts if r["completed_timing"] == "1"]
        assert cohort["attempts"] == len(attempts)
        assert cohort["completed_timing"] == len(complete)
        assert cohort["text_bearing_calls"] == sum(r["first_text_seconds"] != "" for r in complete)
        for metric, recorded in cohort["metrics"].items():
            recomputed = module.distribution(r[metric] for r in complete)
            for statistic, value in recomputed.items():
                assert recorded[statistic] is None if value is None else math.isclose(value, recorded[statistic])
        cohorts[key] = cohort
    assert set(cohorts) == set(ORDER)
    assert sum(c["attempts"] for c in cohorts.values()) == len(rows) == 333
    assert sum(c["completed_timing"] for c in cohorts.values()) == 332
    assert sum(c["text_bearing_calls"] for c in cohorts.values()) == 262
    assert all(cohorts[("qwen", suite)]["metrics"]["cached_prompt_tokens"]["n"] == 0 for suite in ("eq", "retail"))
    return cohorts


def style_axes(ax, title, xlabel, maximum):
    ax.set_facecolor("white")
    ax.set_title(title, loc="left", weight="bold", fontsize=14, color="#142536", pad=15)
    ax.set_xlabel(xlabel, fontsize=10.5, color="#4d5966", labelpad=8)
    ax.set_xlim(0, maximum)
    ax.xaxis.grid(True, color="#D9DFE5", linewidth=0.75)
    ax.set_axisbelow(True)
    ax.tick_params(axis="both", length=0, labelcolor="#293747", pad=8)
    for spine in ax.spines.values():
        spine.set_visible(False)


def paired_bars(ax, records, metric, maximum, decimals, compact=False):
    labels, rendered = [], []
    for y, (model, suite, label, cohort) in enumerate(records):
        stats = cohort["metrics"][metric]
        assert stats["n"] > 0
        labels.append(f"{label}  ·  n={stats['n']}")
        for statistic, offset in (("p50", -0.17), ("p95", 0.17)):
            value = stats[statistic]
            assert 0 <= value <= maximum
            color = COLORS[model]
            ax.barh(y + offset, value, height=0.28,
                    color=color if statistic == "p50" else "white",
                    edgecolor=color, linewidth=1.1,
                    hatch=None if statistic == "p50" else "////", zorder=3)
            value_label = f"{value:,.{decimals}f}"
            # Leave native axis limits intact; reserve space for right-edge labels.
            inside = value > maximum * (0.76 if compact else 0.87)
            ax.text(value - maximum * .018 if inside else value + maximum * .018,
                    y + offset, value_label,
                    ha="right" if inside else "left", va="center", fontsize=10.5,
                    color="white" if inside and statistic == "p50" else "#142536",
                    bbox=None if statistic == "p50" else {"facecolor": "white", "edgecolor": "none", "pad": .4})
            rendered.append(dict(model=model, suite=suite, metric=metric, statistic=statistic,
                                 value=value, n=stats["n"], maximum=maximum))
    ax.set_yticks(range(len(labels)), labels)
    ax.set_ylim(len(labels) - .48, -.55)
    return rendered


def add_legend(fig, y):
    fig.legend(handles=[Patch(facecolor="#566170", edgecolor="#566170", label="p50 · median"),
                        Patch(facecolor="white", edgecolor="#566170", hatch="////", label="p95 · 95th percentile")],
               loc="upper left", bbox_to_anchor=(.055, y), ncol=2, frameon=False, fontsize=11)


def save_chart(fig, name, title_text, description):
    png, svg = OUT / f"{name}.png", OUT / f"{name}.svg"
    fig.savefig(png, dpi=170, facecolor="white", metadata={"Software": "Matplotlib"})
    fig.savefig(svg, facecolor="white", metadata={"Date": None, "Title": title_text})
    plt.close(fig)
    ns = "http://www.w3.org/2000/svg"
    ET.register_namespace("", ns)
    tree = ET.parse(svg)
    root = tree.getroot()
    root.set("role", "img")
    root.set("aria-labelledby", "chart-title chart-description")
    title = ET.Element(f"{{{ns}}}title", id="chart-title")
    title.text = title_text
    desc = ET.Element(f"{{{ns}}}desc", id="chart-description")
    desc.text = description
    root.insert(0, desc)
    root.insert(0, title)
    tree.write(svg, encoding="utf-8", xml_declaration=True)


def timing_chart(cohorts):
    fig, axes = plt.subplots(4, 1, figsize=(10.4, 14.8))
    fig.subplots_adjust(left=.29, right=.94, top=.825, bottom=.15, hspace=.85)
    fig.text(.06, .965, "Qwen vs Nanbeige · performance", fontsize=24, weight="bold", color="#142536")
    fig.text(.06, .935, "Retained companion/support text pilot  /  7 Nov 2026", fontsize=12, color="#4d5966")
    fig.text(.06, .913, "Qwen3.5 9B Q4_K_M / Ollama   ·   Nanbeige4.3-33B IQ2_M / llama.cpp", fontsize=10.8, color="#4d5966")
    add_legend(fig, .895)
    records = [(model, suite, f"{LABELS[model]} · {LABELS[suite]}", cohorts[(model, suite)]) for model, suite in ORDER]
    rendered = []
    for ax, (metric, title, xlabel, maximum, decimals) in zip(axes, TIMING):
        style_axes(ax, title, xlabel, maximum)
        rendered.extend(paired_bars(ax, records, metric, maximum, decimals))
    footers = (
        "333 attempts · 332 completed timings · 262 first-text observations · one capped call retained separately.",
        "First text is not first tool output or audible speech. Preflight, simulator, judge, STT and TTS excluded.",
        "Different tokenizers, cache behavior, reply lengths and debrief order: not a controlled speed ranking.",
        "p95 is a percentile, not a confidence interval; decode p95 is upper throughput, not a slow-tail rate.",
        "Completed-call distributions · 10 selected cases per suite/model · 1 trial · saved data only, no reruns.",
    )
    for i, footer in enumerate(footers):
        fig.text(.06, .103 - i * .019, footer, fontsize=9.6, color="#566170")
    save_chart(fig, "qwen-nanbeige-performance", "Qwen vs Nanbeige: saved first-text, prompt-time, decode and wall-time p50/p95",
               "Four zero-based paired-bar panels, Qwen then Nanbeige within EQ and retail. "
               "First-text and server prompt seconds, native tokens/second, and complete-call seconds. "
               "Sample counts shown per metric; tool-only first text stays missing, capped failure is retained separately. "
               "Non-matched tokenizers/cache/order; no audible latency or new inference measured.")
    return rendered


def counters_chart(cohorts):
    fig, axes = plt.subplots(4, 1, figsize=(10.4, 12.0))
    fig.subplots_adjust(left=.25, right=.94, top=.79, bottom=.20, hspace=1.2)
    fig.text(.06, .955, "Prompt counters · not a prefill race", fontsize=23, weight="bold", color="#142536")
    fig.text(.06, .918, "Qwen3.5 9B Q4_K_M / Ollama   ·   Nanbeige4.3-33B IQ2_M / llama.cpp", fontsize=10.8, color="#4d5966")
    add_legend(fig, .897)
    panels = (
        ("qwen", "reported_prompt_tokens", "Qwen · reported prompt_eval_count", 14000),
        ("nanbeige", "reported_prompt_tokens", "Nanbeige · reported input tokens", 14000),
        ("nanbeige", "cached_prompt_tokens", "Nanbeige · cache_n (cached prompt tokens)", 14000),
        ("nanbeige", "fresh_prompt_tokens", "Nanbeige · prompt_n (newly processed tokens)", 1000),
    )
    rendered = []
    for ax, (model, metric, title, maximum) in zip(axes, panels):
        style_axes(ax, title, "Backend-native tokens · count, not tokens/second", maximum)
        records = [(model, suite, LABELS[suite], cohorts[(model, suite)]) for suite in ("eq", "retail")]
        rendered.extend(paired_bars(ax, records, metric, maximum, 1))
    footers = (
        "Qwen fresh/cache split: UNAVAILABLE, not zero. Its reported count is not a matched fresh-token metric.",
        "Nanbeige per call: input = cached + fresh. Independent p50/p95 values are NOT additive; no stacked bars.",
        "Separate zero-based axes: fresh-token panel uses 0–1,000; other panels use 0–14,000.",
        "Counts depend on tokenizer, history and cache/order. No comparable prefill tokens/second ranking.",
        "7 Nov 2026 text pilot · completed calls only · one capped attempt retained separately · no inference rerun.",
    )
    for i, footer in enumerate(footers):
        fig.text(.06, .128 - i * .023, footer, fontsize=9.7, color="#566170")
    save_chart(fig, "qwen-nanbeige-prompt-counters", "Qwen and Nanbeige: saved prompt token-counter p50/p95",
               "Separate zero-based panels for Qwen reported prompt counts and Nanbeige total input, cached and fresh counts. "
               "Qwen fresh/cache split unavailable, not zero. Independent percentiles are not additive. "
               "Fresh panel axis 0 to 1000, others 0 to 14000; no cross-runtime prefill throughput ranking.")
    return rendered


def main():
    plt.rcParams.update({"font.family": "DejaVu Sans", "font.size": 11,
                         "svg.fonttype": "none", "svg.hashsalt": "voice-pilot-performance-2026-11-07"})
    cohorts = read_evidence()
    OUT.mkdir(exist_ok=True)
    bars = timing_chart(cohorts) + counters_chart(cohorts)
    assert len(bars) == 48
    sources = ("performance/candidate-call-metrics.csv", "performance/summary.json", "performance-evidence.py")
    data = {
        "benchmark_date": "2026-11-07", "scope": "Retained custom text pilot; not voice latency or controlled speed ranking",
        "first_text_definition": "Candidate HTTP request start to first nonempty text-content chunk; tool-only nulls excluded",
        "quantiles": "p50 median; p95 linear interpolation at .95*(n-1); each metric excludes its own nulls",
        "models": {"qwen": "Qwen3.5 9B Q4_K_M / Ollama", "nanbeige": "Nanbeige4.3-33B IQ2_M / llama.cpp"},
        "coverage": {"attempts": 333, "completed_timing": 332, "first_text": 262},
        "qwen_cache_split_available": False,
        "counter_caveat": "Independent percentiles are not additive. Cross-backend counters/tokenizers/cache/order differ.",
        "bars": bars,
        "source_sha256": {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in sources},
    }
    (OUT / "qwen-nanbeige-performance.json").write_text(json.dumps(data, indent=2, allow_nan=False) + "\n")
    print("Rendered 48 validated bars across timing and prompt-counter charts; SVG + PNG + source-hashed data.")


if __name__ == "__main__":
    main()
