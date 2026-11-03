"""Evaluation. Offline by default; add --llm to also score the Groq-based parts (needs GROQ_API_KEY).
  python eval/run_eval.py          workout + meal extraction (rule-based baselines) and retrieval
  python eval/run_eval.py --llm    also the LLM extractors and the coach agent (takes a few minutes, free tier is rate-limited)

Meal gold grams come from the same unit table the app uses, so the meal eval tests parsing and matching,
not whether a 'katori' really weighs 150 g."""
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT.parent))
try:
    from dotenv import load_dotenv
    load_dotenv(ROOT.parent / ".env")
except ImportError:
    pass
from core import agent, db, llm, mealparse, rag, workout  # noqa: E402


def prf(tp, fp, fn):
    p, r = tp / max(tp + fp, 1), tp / max(tp + fn, 1)
    return p, r, 2 * p * r / max(p + r, 1e-9)


# ---------------- workouts ----------------
def key(s):
    return (s["category"], round(s["duration_min"]), s["intensity"])


def score_workouts(predict, cases):
    tp = fp = fn = exact = scored = 0
    misses = []
    for c in cases:
        gold = {key(s) for s in c["sessions"]}
        out = predict(c["text"])
        if out is None:
            continue
        scored += 1
        pred = {key(s.__dict__) for s in out}
        tp, fp, fn, exact = tp + len(gold & pred), fp + len(pred - gold), fn + len(gold - pred), exact + (gold == pred)
        if gold != pred:
            misses.append((c["text"], sorted(gold), sorted(pred)))
    p, r, f = prf(tp, fp, fn)
    return {"exact": exact / max(scored, 1), "p": p, "r": r, "f1": f, "scored": scored, "total": len(cases)}, misses


# ---------------- meals ----------------
def score_meals(conn, extract, cases):
    tp = fp = fn = grams_ok = matched_pairs = exact = scored = 0
    misses = []
    for c in cases:
        parsed = extract(c["text"])
        if parsed is None:
            continue
        scored += 1
        items = [i for i in mealparse.resolve(conn, parsed) if i.get("food_id")]
        pred = {}
        for i in items:
            pred[i["food_id"]] = pred.get(i["food_id"], 0) + i["grams"]
        gold = {}
        for g in c["items"]:
            gold[g["food"]] = gold.get(g["food"], 0) + g["grams"]
        both = set(pred) & set(gold)
        tp, fp, fn = tp + len(both), fp + len(set(pred) - set(gold)), fn + len(set(gold) - set(pred))
        ok = [f for f in both if abs(pred[f] - gold[f]) <= 0.25 * gold[f]]
        grams_ok, matched_pairs = grams_ok + len(ok), matched_pairs + len(both)
        if set(pred) == set(gold) and len(ok) == len(gold):
            exact += 1
        else:
            misses.append((c["text"], gold, pred))
    p, r, f = prf(tp, fp, fn)
    return {"exact": exact / max(scored, 1), "p": p, "r": r, "f1": f, "grams_within_25pct": grams_ok / max(matched_pairs, 1),
            "scored": scored, "total": len(cases)}, misses


def show(name, res):
    extra = f"  grams within 25%={res['grams_within_25pct']:.0%}" if "grams_within_25pct" in res else ""
    print(f"{name:<20} exact={res['exact']:.0%}  precision={res['p']:.0%}  recall={res['r']:.0%}  F1={res['f1']:.0%}{extra}  (scored {res['scored']}/{res['total']})")


def main():
    use_llm = "--llm" in sys.argv
    client = None
    if use_llm:
        client = llm.make_client(os.getenv("GROQ_API_KEY"))
        if client is None:
            sys.exit("--llm needs GROQ_API_KEY (export it, or put it in .env).")
        llm.RETRY_DELAYS = (10, 20, 40)        # free tier is rate-limited: wait longer than the web app does

    def guarded(fn):
        def run(text):
            try:
                return fn(text)
            except llm.LLMAPIError as exc:
                print(f"  API error, case skipped: {exc}")
                return None
            except llm.LLMError:
                return []                       # unusable model output counts as a miss
        return run

    # workouts
    wcases = json.loads((ROOT / "workout_cases.json").read_text())
    res, misses = score_workouts(workout.regex_parse, wcases)
    print(f"--- Workout extraction ({len(wcases)} cases) ---")
    show("regex baseline", res)
    if client:
        lres, lmiss = score_workouts(guarded(lambda t: llm.extract_workouts(client, t)), wcases)
        show("LLM extractor", lres)
        print("LLM misses:", *lmiss, sep="\n  ")
    else:
        print("regex misses:", *misses[:8], sep="\n  ")

    # meals: reported separately for clean and messy phrasing
    conn = db.memory_db()
    allm = json.loads((ROOT / "meal_cases.json").read_text())
    for label in ("clean", "messy"):
        mcases = [c for c in allm if c["set"] == label]
        print(f"\n--- Meal logging, {label} phrasing ({len(mcases)} cases; matching + grams) ---")
        mres, mmiss = score_meals(conn, mealparse.regex_parse_meal, mcases)
        show("regex baseline", mres)
        if client:
            lm, lmm = score_meals(conn, guarded(lambda t: llm.extract_meals(client, t)), mcases)
            show("LLM extractor", lm)
            print("LLM misses:", *lmm, sep="\n  ")
        else:
            print("regex misses:", *mmiss[:6], sep="\n  ")

    # retrieval
    bm25 = rag.BM25(rag.load_kb())
    rcases = json.loads((ROOT / "retrieval_cases.json").read_text())
    h1 = h3 = 0
    for c in rcases:
        ids = [ch.id for _, ch in bm25.search(c["question"], 3)]
        h1, h3 = h1 + (ids[:1] == [c["expected"]]), h3 + (c["expected"] in ids)
    print(f"\n--- Retrieval ({len(rcases)} questions) ---\nhit@1={h1 / len(rcases):.0%}  hit@3={h3 / len(rcases):.0%}")

    if client:
        print("\n--- Coach agent checks ---")
        profile = {"sex": "Male", "age": 25, "height_cm": 175, "weight_kg": 70, "lifestyle": "Light movement", "goal": "Build muscle", "diet": "Vegetarian"}
        summary = lambda: {"protein_left": 40, "protein_target_low": 112, "kcal_target": 2400}  # noqa: E731
        for q, must in [("How much protein should I eat per day?", "search_knowledge"),
                        ("What are my targets if I do 60 min of heavy lifting?", "compute_targets"),
                        ("How much protein is in 150 g of paneer?", "lookup_food"),
                        ("What should I eat next?", "suggest_recipes")]:
            box = agent.Toolbox(conn, bm25, profile, summary)
            try:
                out = agent.chat(client, [], q, profile, box)
            except llm.LLMAPIError as exc:
                print(f"SKIP  {q}  ({exc})")
                continue
            used = {t["tool"] for t in out["trace"]}
            ok = must in used and not out["unsupported_citations"]
            print(f"{'PASS' if ok else 'FAIL'}  {q}  tools={sorted(used)}  bad_citations={out['unsupported_citations']}")


if __name__ == "__main__":
    main()
