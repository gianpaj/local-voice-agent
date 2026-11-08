#!/usr/bin/env python3
"""Render saved pilot results; no model calls. Requires matplotlib."""

import csv
import hashlib
import json
import math
from pathlib import Path
import statistics
import xml.etree.ElementTree as ET

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt


ROOT = Path(__file__).resolve().parent
OUT = ROOT / "assets"
CRITERIA = (
    "demonstrated_empathy", "pragmatic_ei", "depth_of_insight",
    "social_dexterity", "emotional_reasoning", "message_tailoring",
)
EQ_IDS = {"1", "2", "3", "6", "9", "13", "131", "133", "137", "302"}


def read_json(relative):
    return json.loads((ROOT / relative).read_text())


def eq_mean(relative, summary):
    with (ROOT / relative).open(newline="") as source:
        rows = list(csv.DictReader(source))
    assert len(rows) == 10 and {row["scenario_id"] for row in rows} == EQ_IDS
    assert summary["judged_scenarios"] == summary["total_scenarios"] == 10
    assert summary["grading_errors"] == 0
    scores = []
    for row in rows:
        score = statistics.mean(float(row[key]) for key in CRITERIA)
        assert math.isclose(score, float(row["qualitative_mean_0_20"]))
        scores.append(score)
    score = statistics.mean(scores)
    assert math.isclose(score, summary["quality_score"])
    return score


def main():
    qwen_eq_path = "results/qwen-eq10/codex-judge/summary.json"
    nan_eq_path = "results/nanbeige-iq2m/eq10/codex-judge/summary.json"
    qwen_csv = "results/qwen-eq10/codex-judge/scores.csv"
    nan_csv = "results/nanbeige-iq2m/eq10/codex-judge/scores.csv"
    qwen_retail_path = "results/qwen-retail10/summary.json"
    nan_path = "results/nanbeige-iq2m/summary.json"
    qwen_eq, nan_eq = read_json(qwen_eq_path), read_json(nan_eq_path)
    qwen_retail, nan = read_json(qwen_retail_path), read_json(nan_path)
    assert qwen_retail["finished"] == qwen_retail["expected"] == 10
    assert nan["retail_cases_finished"] == nan["retail_expected"] == 10
    assert qwen_retail["passed"] == nan["retail_passed"] == 8
    assert not nan["voice_latency_measured"]
    assert qwen_eq["judge"] == nan_eq["judge"] == "gpt-6.1-sol"
    assert qwen_eq["reasoning_effort"] == nan_eq["reasoning_effort"] == "xhigh"
    eq = [eq_mean(qwen_csv, qwen_eq), eq_mean(nan_csv, nan_eq)]
    passed = [qwen_retail["passed"], nan["retail_passed"]]
    sources = [qwen_eq_path, nan_eq_path, qwen_csv, nan_csv, qwen_retail_path, nan_path]
    data = {
        "benchmark_date": "2026-11-07",
        "scope": "Custom matched text pilot; not official leaderboard scores",
        "eq": {"metric": "Mean of six quality criteria, then mean of ten scenarios",
               "scale": [0, 20], "cases_per_model": 10},
        "retail": {"metric": "Verified passed task count", "scale": [0, 10],
                   "cases_per_model": 10, "failures_retained": True},
        "models": [
            {"name": "Qwen3.5 9B", "quantization": "Q4_K_M", "runtime": "Ollama",
             "eq_mean": eq[0], "retail_passed": passed[0]},
            {"name": "Nanbeige4.3-33B", "quantization": "IQ2_M", "runtime": "llama.cpp",
             "eq_mean": eq[1], "retail_passed": passed[1]},
        ],
        "judge": "gpt-6.1-sol / xhigh via Codex CLI",
        "trials_per_case": 1,
        "voice_latency_measured": False,
        "source_sha256": {path: hashlib.sha256((ROOT / path).read_bytes()).hexdigest()
                          for path in sources},
    }
    OUT.mkdir(exist_ok=True)
    (OUT / "qwen-nanbeige-comparison.json").write_text(json.dumps(data, indent=2) + "\n")

    plt.rcParams.update({"font.family": "DejaVu Sans", "font.size": 12,
                         "svg.fonttype": "none", "svg.hashsalt": "voice-pilot-2026-11-07"})
    fig, axes = plt.subplots(2, 1, figsize=(10.4, 8.0))
    fig.patch.set_facecolor("white")
    fig.subplots_adjust(left=0.30, right=0.92, top=0.78, bottom=0.25, hspace=0.95)
    fig.text(0.06, 0.94, "Qwen vs Nanbeige", fontsize=25, weight="bold", color="#142536")
    fig.text(0.06, 0.895, "Companion + customer-support text pilot  /  7 Nov 2026",
             fontsize=12, color="#4d5966")
    colors = ["#2968AD", "#087E75"]
    labels = ["Qwen3.5 9B\nQ4_K_M · Ollama", "Nanbeige4.3-33B\nIQ2_M · llama.cpp"]
    panels = [
        (eq, 20, [0, 5, 10, 15, 20], "Companion conversation · average EQ rubric",
         "Mean score out of 20 · higher is better", [f"{value:.2f} / 20" for value in eq]),
        (passed, 10, [0, 2, 4, 6, 8, 10], "Customer support · verified retail outcomes",
         "Passed tasks out of 10 · higher is better", [f"{value} / 10" for value in passed]),
    ]
    for ax, (values, maximum, ticks, title, xlabel, value_labels) in zip(axes, panels):
        ax.set_facecolor("white")
        for y in range(2):
            ax.barh(y, maximum, height=0.45, color="#EEF1F4", zorder=1)
        bars = ax.barh([0, 1], values, height=0.45, color=colors, zorder=3)
        ax.set_yticks([0, 1], labels)
        ax.invert_yaxis()
        ax.set_ylim(1.55, -0.55)
        ax.set_xlim(0, maximum)
        ax.set_xticks(ticks)
        ax.xaxis.grid(True, color="#D9DFE5", linewidth=0.8, zorder=2)
        ax.set_axisbelow(True)
        ax.tick_params(axis="both", length=0, labelcolor="#293747", pad=9)
        for spine in ax.spines.values():
            spine.set_visible(False)
        ax.set_title(title, loc="left", fontsize=14, fontweight="bold", pad=18, color="#142536")
        ax.set_xlabel(xlabel, fontsize=11, labelpad=9, color="#4d5966")
        for bar, label in zip(bars, value_labels):
            ax.text(bar.get_width() + maximum * 0.025,
                    bar.get_y() + bar.get_height() / 2, label,
                    va="center", ha="left", fontsize=13, weight="bold", color="#142536")

    fig.text(0.06, 0.11, "10 cases per task/model · 1 trial · Codex gpt-6.1-sol / xhigh",
             fontsize=11, color="#344658")
    fig.text(0.06, 0.07, "Custom pilot, not official leaderboard scores. All failures retained.",
             fontsize=10.5, color="#566170")
    fig.text(0.06, 0.035, "Different metric scales; no combined score. Voice latency was not measured.",
             fontsize=10.5, color="#566170")
    png = OUT / "qwen-nanbeige-comparison.png"
    svg = OUT / "qwen-nanbeige-comparison.svg"
    fig.savefig(png, dpi=180, facecolor="white", metadata={"Software": "Matplotlib"})
    fig.savefig(svg, facecolor="white", metadata={"Date": None,
                "Title": "Qwen vs Nanbeige: matched companion/support text pilot"})
    plt.close(fig)

    # Retain searchable SVG labels and a screen-reader description.
    namespace = "http://www.w3.org/2000/svg"
    ET.register_namespace("", namespace)
    tree = ET.parse(svg)
    root = tree.getroot()
    root.set("role", "img")
    root.set("aria-labelledby", "chart-title chart-description")
    title = ET.Element(f"{{{namespace}}}title", {"id": "chart-title"})
    title.text = "Qwen and Nanbeige custom text benchmark comparison"
    desc = ET.Element(f"{{{namespace}}}desc", {"id": "chart-description"})
    desc.text = ("EQ rubric mean: Qwen 4.30/20, Nanbeige 5.57/20, ten scenarios each. "
                 "Retail outcomes: both eight passed tasks out of ten. Separate zero-based scales. "
                 "One trial, all failures retained; not official leaderboard scores or an audio benchmark.")
    root.insert(0, desc)
    root.insert(0, title)
    tree.write(svg, encoding="utf-8", xml_declaration=True)
    print(json.dumps({"eq_means": eq, "retail_passed": passed,
                      "png": str(png.relative_to(ROOT)), "svg": str(svg.relative_to(ROOT))}))


if __name__ == "__main__":
    main()
