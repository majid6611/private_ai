"""Score a local model on exported retrieval cases and (only if it passes) write the record.

  python evaluate.py /work/cases.json /work/results.json /work/evaluation.json

Runs the real orchestrator generate() path against MODEL_URL, so prompt, labels, JSON mode and
output validation are exactly what users get. Gates follow architecture section 20:
correct answers, citations that include the true source passage, zero fabricated figures
and declining when evidence is missing. Scores come from string checks plus the printed
answers for human review; they are a smoke gate, not a substitute for human review.
"""

import json
import os
import re
import statistics
import sys
import time
from typing import Any

import main

DECLINE = re.compile(
    r"insufficient|not (?:specified|stated|mentioned|provided|available|found|contain|address|include|clear)"
    r"|no (?:information|evidence|mention|details|relevant|specific)|cannot (?:be )?(?:determined|found|answer)"
    r"|can't|unable to|does not (?:say|specify|contain|mention|provide|include)|doesn't|not enough"
    r"|there is no|isn't|unknown|n/a",
    re.I,
)
NUMBER = re.compile(r"\d[\d,]*(?:\.\d+)?")
THRESHOLDS = {"correct": 0.90, "citation": 0.95, "decline": 0.9, "fabricated": 0}


def norm(text: str) -> str:
    return re.sub(r"\s+", " ", text.lower().replace(",", "")).strip()


def has_fact(answer: str, fact: str) -> bool:
    return any(norm(alt) in norm(answer) for alt in fact.split("|") if alt)


def fabricated_figures(answer: str, sources: list[dict[str, Any]], question: str) -> list[str]:
    haystack = norm(question + " " + " ".join(s["text"] for s in sources))
    found = []
    for number in NUMBER.findall(answer):
        cleaned = number.replace(",", "").rstrip(".")
        if cleaned and cleaned not in haystack:
            found.append(cleaned)
    return found


def percentile(values: list[float], q: float) -> float:
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(round(q * (len(ordered) - 1))))]


def main_eval(cases_path: str, results_path: str, record_path: str) -> int:
    cases = json.load(open(cases_path))
    limit = int(os.environ.get("EVAL_LIMIT", "0"))
    if limit:
        cases = cases[:limit]
    rows: list[dict[str, Any]] = []
    for number, case in enumerate(cases, 1):
        task = {
            "id": "00000000-0000-4000-8000-000000000000",
            "lease_token": "x" * 32,
            "question": case["question"],
            "sources": [{"id": s["id"], "text": s["text"]} for s in case["sources"]],
        }
        started = time.time()
        row: dict[str, Any] = {"question": case["question"], "kind": case["kind"], "answerable": case["answerable"]}
        try:
            result = main.generate(task)
            row.update(ok=True, answer=result["answer"], cited=result["citation_ids"], model_declined=bool(result.get("declined")))
        except Exception as error:  # noqa: BLE001 - record any failure as a failed case
            row.update(ok=False, error=f"{type(error).__name__}: {error}")
        row["seconds"] = round(time.time() - started, 1)
        gold = {s["id"] for s in case["sources"] if s["gold"]}
        row["gold_retrieved"] = bool(gold) or not case["answerable"]
        if row["ok"]:
            if case["answerable"]:
                row["correct"] = has_fact(row["answer"], case["fact"])
                row["citation_ok"] = bool(gold & set(row["cited"]))
            else:
                row["declined"] = bool(row.get("model_declined") or DECLINE.search(row["answer"]))
            row["fabricated"] = fabricated_figures(row["answer"], case["sources"], case["question"])
        rows.append(row)
        print(f"[{number}/{len(cases)}] {row['seconds']:>5}s {row['kind']:<10} "
              f"{'OK ' if row.get('correct') or row.get('declined') else 'BAD'} {case['question'][:70]!r} -> "
              f"{(row.get('answer') or row.get('error') or '')[:110]!r}", flush=True)
        json.dump(rows, open(results_path, "w"), indent=1)

    answerable = [r for r in rows if r["answerable"] and r["gold_retrieved"]]
    skipped = [r for r in rows if r["answerable"] and not r["gold_retrieved"]]
    unanswerable = [r for r in rows if not r["answerable"]]
    correct = sum(bool(r.get("correct")) for r in answerable)
    citation = sum(bool(r.get("citation_ok")) for r in answerable)
    declined = sum(bool(r.get("declined")) for r in unanswerable)
    fabricated = sum(1 for r in rows if r.get("fabricated"))
    failed = sum(1 for r in rows if not r["ok"])
    seconds = [r["seconds"] for r in rows]
    summary = {
        "model_id": os.environ.get("MODEL_ID", ""),
        "cases": len(rows),
        "answerable_evaluated": len(answerable),
        "answerable_skipped_retrieval_miss": len(skipped),
        "correct": f"{correct}/{len(answerable)}",
        "correct_rate": round(correct / max(1, len(answerable)), 3),
        "citation_valid": f"{citation}/{len(answerable)}",
        "citation_rate": round(citation / max(1, len(answerable)), 3),
        "no_answer_declined": f"{declined}/{len(unanswerable)}",
        "decline_rate": round(declined / max(1, len(unanswerable)), 3),
        "answers_with_fabricated_figures": fabricated,
        "model_call_failures": failed,
        "latency_p50_s": round(statistics.median(seconds), 1) if seconds else None,
        "latency_p95_s": round(percentile(seconds, 0.95), 1) if seconds else None,
        "thresholds": THRESHOLDS,
    }
    passed = (
        len(rows) >= 40
        and summary["correct_rate"] >= THRESHOLDS["correct"]
        and summary["citation_rate"] >= THRESHOLDS["citation"]
        and summary["decline_rate"] >= THRESHOLDS["decline"]
        and fabricated <= THRESHOLDS["fabricated"]
        and failed == 0
    )
    summary["passed"] = passed
    print("\nSUMMARY", json.dumps(summary, indent=1))
    json.dump({"summary": summary, "cases": rows}, open(results_path, "w"), indent=1)
    json.dump({**summary, "model_id": summary["model_id"], "passed": passed}, open(record_path, "w"), indent=1)
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main_eval(*sys.argv[1:4]))
