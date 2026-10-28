"""Evaluate the fit and resume agents against a small labeled dataset.

    python -m evals.run_evals                       # uses OPENROUTER_API_KEY / OPENROUTER_MODEL
    python -m evals.run_evals --model <model-id>    # compare models: run once per model, diff the JSON
    python -m evals.run_evals --dry-run             # offline stub, checks the harness itself

Metrics
  fit:     band accuracy (does the score land in the labeled band?), Spearman rank correlation between
           score and label, and mean score per label (strong should be well above weak).
  resume:  invented-fact rate. Numbers and mid-line capitalized terms in the tailored resume that never
           appear in the source resume. A heuristic: it flags things to inspect, it does not prove fabrication.
Results are written to evals/results/<model>.json so runs can be compared.
"""
import argparse
import json
import re
import statistics
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT.parent))

BANDS = {"strong": (70, 100), "medium": (40, 69), "weak": (0, 39)}
ORDINAL = {"weak": 0, "medium": 1, "strong": 2}

NUMBER = re.compile(r"\$?\d[\d,.]*[%kKmMbB+]?")
CAP_WORD = re.compile(r"\b[A-Z][A-Za-z0-9+#.]{2,}\b")
# Section headings and filler the tailoring prompt legitimately adds.
ALLOWED = {"Experience", "Education", "Skills", "Summary", "Projects", "Certifications", "Contact"}


def load_dataset(path: Path = ROOT / "dataset.jsonl") -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def _ranks(values: list[float]) -> list[float]:
    """Average ranks, so ties (common with LLM scores like 80, 80) are handled correctly."""
    order = sorted(range(len(values)), key=lambda i: values[i])
    ranks = [0.0] * len(values)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and values[order[j + 1]] == values[order[i]]:
            j += 1
        for k in range(i, j + 1):
            ranks[order[k]] = (i + j) / 2 + 1
        i = j + 1
    return ranks


def spearman(xs: list[float], ys: list[float]) -> float | None:
    if len(xs) < 3:
        return None
    rx, ry = _ranks(xs), _ranks(ys)
    mx, my = statistics.fmean(rx), statistics.fmean(ry)
    num = sum((a - mx) * (b - my) for a, b in zip(rx, ry))
    den = (sum((a - mx) ** 2 for a in rx) * sum((b - my) ** 2 for b in ry)) ** 0.5
    return num / den if den else None


def _norm_num(token: str) -> str:
    return re.sub(r"[^\d]", "", token)


def invented_facts(source: str, output: str) -> list[str]:
    """Numbers and mid-line capitalized terms present in `output` but absent from `source`."""
    src_lower = source.lower()
    src_nums = {_norm_num(t) for t in NUMBER.findall(source)}
    found: list[str] = []
    for line in output.splitlines():
        if line.lstrip().startswith("#"):          # headings are structure, not claims
            continue
        for tok in NUMBER.findall(line):
            if _norm_num(tok) and _norm_num(tok) not in src_nums:
                found.append(tok)
        words = line.split()
        for pos, word in enumerate(words):
            clean = word.strip(".,;:()[]*-")
            if pos == 0 or not CAP_WORD.fullmatch(clean) or clean in ALLOWED:
                continue                            # skip sentence/bullet-initial words (verbs like "Built")
            if clean.lower() not in src_lower:
                found.append(clean)
    return sorted(set(found))


def eval_fit(cases: list[dict], analyze) -> dict:
    rows = []
    for c in cases:
        score = analyze(c["resume"], c["job"])["score"]
        lo, hi = BANDS[c["label"]]
        rows.append({"id": c["id"], "label": c["label"], "score": score, "in_band": lo <= score <= hi})
    by_label = {lab: [r["score"] for r in rows if r["label"] == lab] for lab in BANDS}
    return {
        "band_accuracy": sum(r["in_band"] for r in rows) / len(rows),
        "spearman": spearman([r["score"] for r in rows], [ORDINAL[r["label"]] for r in rows]),
        "mean_score_by_label": {k: round(statistics.fmean(v), 1) for k, v in by_label.items() if v},
        "rows": rows,
    }


def eval_resume(cases: list[dict], tailor) -> dict:
    rows = []
    for c in cases:
        out = tailor(c["resume"], c["job"])["resume"]
        flagged = invented_facts(c["resume"], out)
        rows.append({"id": c["id"], "flagged": flagged, "clean": not flagged})
    return {"clean_rate": sum(r["clean"] for r in rows) / len(rows), "rows": rows}


def _dry_run_stubs():
    """Offline stand-ins: score by word overlap, 'tailor' by echoing the resume. Verifies the harness only."""
    def analyze(resume, job):
        r, j = set(re.findall(r"[a-z]+", resume.lower())), set(re.findall(r"[a-z]+", job.lower()))
        return {"score": min(100, int(300 * len(r & j) / max(len(j), 1)))}
    return analyze, lambda resume, job: {"resume": resume}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", help="override OPENROUTER_MODEL for this run")
    ap.add_argument("--dry-run", action="store_true", help="offline stubs; no API calls")
    args = ap.parse_args()

    if args.dry_run:
        analyze, tailor = _dry_run_stubs()
        name = "dry-run"
    else:
        from src.agents.fit_agent import analyze_fit as analyze
        from src.agents.resume_agent import tailor_resume as tailor
        from src.services import llm_client
        if args.model:
            llm_client.OPENROUTER_MODEL = args.model
        name = llm_client.OPENROUTER_MODEL

    cases = load_dataset()
    report = {"model": name, "n": len(cases), "fit": eval_fit(cases, analyze), "resume": eval_resume(cases, tailor)}

    out = ROOT / "results"
    out.mkdir(exist_ok=True)
    (out / f"{re.sub(r'[^A-Za-z0-9._-]', '_', name)}.json").write_text(json.dumps(report, indent=2))

    f, r = report["fit"], report["resume"]
    print(f"model: {name}   cases: {len(cases)}")
    print(f"fit    band accuracy {f['band_accuracy']:.0%}   spearman {f['spearman']}   mean by label {f['mean_score_by_label']}")
    print(f"resume clean (no invented facts flagged) {r['clean_rate']:.0%}")
    for row in r["rows"]:
        if row["flagged"]:
            print(f"  {row['id']}: review -> {row['flagged']}")


if __name__ == "__main__":
    main()
