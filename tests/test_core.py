"""Calculator, workout parsing, retrieval, LLM layer and agent (all with mock clients)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from conftest_helpers import BrokenClient, FakeClient, resp, tool_use  # noqa: E402

from core import agent, calc, db, llm, rag, workout  # noqa: E402


def no_sleep():
    original, llm.time.sleep = llm.time.sleep, lambda s: None
    return original


# ---------- calculator ----------
def test_bmr_known_values():
    assert calc.bmr("Male", 80, 180, 30) == 1780
    assert calc.bmr("Female", 60, 165, 25) == 1345.25


def test_targets_goal_protein_and_floor():
    cut = calc.compute_targets("Male", 30, 180, 80, "Light movement", "Lose fat", 400, True)
    keep = calc.compute_targets("Male", 30, 180, 80, "Light movement", "Maintain", 400, True)
    assert cut.calories < keep.calories and (cut.protein_low, cut.protein_high) == (144, 176)
    low = calc.compute_targets("Female", 40, 150, 40, "Desk job / mostly sitting", "Lose fat", 0)
    assert low.calories >= 1200 and low.notes


def test_kcal_burned_uses_met():
    assert round(workout.kcal_burned(workout.Session("run", "running", 30, "moderate"), 70)) == 315


# ---------- workout parsing ----------
def test_regex_workout_parser():
    out = workout.regex_parse("45 min heavy squats, then 20 min easy jog and 1.5 hours cycling")
    assert [(s.category, s.duration_min, s.intensity) for s in out] == [
        ("strength", 45, "hard"), ("running", 20, "light"), ("cycling", 90, "moderate")]
    assert workout.regex_parse("rest day") == []


def test_session_validation():
    for bad in ({"category": "dancing", "duration_min": 10, "intensity": "light"},
                {"category": "yoga", "duration_min": -5, "intensity": "light"},
                {"category": "yoga", "duration_min": 10, "intensity": "extreme"}, {"category": "yoga"}):
        try:
            workout.make_session(bad)
            assert False, bad
        except ValueError:
            pass


# ---------- retrieval ----------
def test_knowledge_base_and_retrieval():
    kb = rag.load_kb()
    assert len(kb) >= 10 and next(c for c in kb if c.id == "S1").status.startswith("checked")
    bm = rag.BM25(kb)
    assert bm.search("how is BMR calculated")[0][1].id == "S4"
    assert "S1" in [c.id for _, c in bm.search("How much protein per day for building muscle?")]
    assert bm.search("zzzz qqqq") == []


# ---------- LLM layer ----------
def test_workout_extraction_retries_bad_output():
    good = '```json\n[{"activity":"squats","category":"strength","duration_min":45,"intensity":"hard"}]\n```'
    out = llm.extract_workouts(FakeClient(["not json at all", good]), "squats 45 min")
    assert out[0].category == "strength" and out[0].duration_min == 45


def test_meal_extraction_validates_items():
    good = '[{"name": "Roti", "quantity": 2, "unit": null}, {"name": "dal", "quantity": 1, "unit": "katoris"}]'
    items = llm.extract_meals(FakeClient([good]), "2 roti and 1 katori dal")
    assert items == [{"name": "roti", "quantity": 2.0, "unit": None}, {"name": "dal", "quantity": 1.0, "unit": "katori"}]
    bad_then_good = FakeClient(['[{"name": "", "quantity": 1}]', good])
    assert len(llm.extract_meals(bad_then_good, "x")) == 2
    try:
        llm.extract_meals(FakeClient(['[{"name": "x", "quantity": -3}]', "nope"]), "x")
        assert False
    except llm.LLMError:
        pass


def test_unknown_units_are_dropped_not_trusted():
    items = llm.extract_meals(FakeClient(['[{"name": "rice", "quantity": 1, "unit": "swimming pool"}]']), "x")
    assert items[0]["unit"] is None


def test_api_errors_are_not_retried_as_bad_output():
    c = BrokenClient(401)
    try:
        llm.extract_workouts(c, "45 min squats")
        assert False
    except llm.LLMAPIError as e:
        assert "API key" in str(e)
    assert c.calls == 1


def test_rate_limit_backs_off_then_gives_clear_error():
    original = no_sleep()
    try:
        c = BrokenClient(429)
        try:
            llm.extract_meals(c, "x")
            assert False
        except llm.LLMAPIError as e:
            assert "rate limit" in str(e).lower()
        assert c.calls == len(llm.RETRY_DELAYS) + 1
    finally:
        llm.time.sleep = original


# ---------- agent ----------
def toolbox(diet="Vegetarian", summary=None):
    return agent.Toolbox(db.memory_db(), rag.BM25(rag.load_kb()), {"diet": diet, "cook_minutes": 30}, summary or (lambda: {"protein_left": 35}))


def test_agent_loop_uses_tools_and_validates_citations():
    tb = toolbox()
    client = FakeClient([tool_use("search_knowledge", {"query": "protein per meal"}),
                         tool_use("lookup_food", {"name": "paneer", "grams": 200}, "t2"),
                         resp("About 0.25 g/kg per meal [S8]. 200 g paneer has 36 g protein [S99].")])
    out = agent.chat(client, [], "q", {"weight_kg": 70}, tb)
    assert [t["tool"] for t in out["trace"]] == ["search_knowledge", "lookup_food"]
    assert out["trace"][1]["output"]["protein"] == 36.0 and out["unsupported_citations"] == ["S99"]


def test_agent_tools_enforce_diet_and_survive_bad_input():
    tb = toolbox("Vegan")
    assert "error" in tb.call("lookup_food", {"name": "chicken"})           # the model cannot bypass the diet
    assert tb.call("lookup_food", {"name": "tofu", "grams": 100})["protein"] == 8.0
    assert tb.call("lookup_food", {"name": "roti", "quantity": 2})["grams"] == 80
    assert "error" in tb.call("compute_targets", {"sex": "Male"})            # missing arguments become an error result
    assert "error" in tb.call("no_such_tool", {})
    names = [r["name"] for r in tb.call("suggest_recipes", {"count": 3})]
    assert len(names) == 3
    assert tb.call("today_summary", {}) == {"protein_left": 35}


def test_agent_survives_bad_tool_arguments_and_step_limit():
    bad = resp(None, [__import__("types").SimpleNamespace(id="t1", function=__import__("types").SimpleNamespace(name="lookup_food", arguments="{not json"))])
    out = agent.chat(FakeClient([bad, resp("ok")]), [], "q", {}, toolbox())
    assert "error" in out["trace"][0]["output"] and out["text"] == "ok"
    loop = FakeClient([tool_use("search_knowledge", {"query": "x"}, f"t{i}") for i in range(10)])
    out = agent.chat(loop, [], "q", {}, toolbox(), max_steps=3)
    assert "step limit" in out["text"] and len(out["trace"]) == 3


if __name__ == "__main__":
    for name, fn in [(k, v) for k, v in dict(globals()).items() if k.startswith("test_")]:
        fn()
        print("PASS", name)
