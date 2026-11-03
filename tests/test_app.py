"""Flask route tests against a throwaway database. No network: Groq is stubbed where needed."""
import os
import sys
import tempfile
from pathlib import Path

os.environ.pop("GROQ_API_KEY", None)
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from conftest_helpers import BrokenClient  # noqa: E402

import app as A  # noqa: E402
from core import db, store, suggest  # noqa: E402


class Env:
    """A fresh client + database. Use as a context manager so env vars and stubs are always restored."""
    def __enter__(self):
        self.path = tempfile.mktemp(suffix=".db")
        A.app.config["DB_PATH"] = self.path
        self.client = A.app.test_client()
        self.client.get("/")
        with self.client.session_transaction() as s:
            self.token = s["csrf"]
        self.saved = (A.llm.make_client, A.agent.chat, os.environ.get("GROQ_API_KEY"))
        return self

    def __exit__(self, *exc):
        A.llm.make_client, A.agent.chat = self.saved[0], self.saved[1]
        os.environ.pop("GROQ_API_KEY", None)
        if self.saved[2]:
            os.environ["GROQ_API_KEY"] = self.saved[2]

    def post(self, name, ajax=True, **data):
        headers = {"X-CSRF-Token": self.token}
        if ajax:
            headers["X-Requested-With"] = "fetch"
        return self.client.post(f"/act/{name}", data=data, headers=headers)

    def db(self):
        return db.connect(self.path)


def test_fresh_page_invents_nothing():
    with Env() as e:
        html = e.client.get("/").get_data(as_text=True)
        assert "Nothing logged yet" in html and "What will you eat next?" in html and "0%" in html
        assert e.db().execute("SELECT COUNT(*) FROM meal_log").fetchone()[0] == 0


def test_csrf_is_required_and_secret_key_is_random():
    with Env() as e:
        assert e.client.post("/act/clear_chat", data={}).status_code == 400
        assert e.client.post("/act/clear_chat", data={"csrf_token": "wrong"}).status_code == 400
        assert e.post("clear_chat").status_code == 200
    assert A.app.secret_key and "change" not in A.app.secret_key


def test_ajax_returns_panels_and_plain_post_redirects():
    with Env() as e:
        d = e.post("clear_chat").get_json()
        assert set(d["panels"]) == set(A.PANELS) and d["ok"] is True
        r = e.post("clear_chat", ajax=False)
        assert r.status_code == 302 and r.headers["Location"].endswith("/#you")
        r = e.post("parse_meal", ajax=False, meal_text="", tab="//evil.example")        # error path echoes a user-supplied tab
        assert r.status_code == 302 and r.headers["Location"].endswith("/#today") and "evil" not in r.headers["Location"]


def test_profile_validation_never_clamps():
    with Env() as e:
        d = e.post("save_profile", sex="Female", age="17", height_cm="160", weight_kg="55", lifestyle="Light movement",
                   goal="Maintain", diet="Vegetarian", cook_minutes="30", dislikes="").get_json()
        assert d["ok"] is False and "Age must be between 18 and 80" in d["flash"][1]
        assert store.get_profile(e.db())["age"] == 25
        d = e.post("save_profile", sex="Male", age="30", height_cm="180", weight_kg="80", lifestyle="Light movement",
                   goal="Build muscle", diet="Vegan", cook_minutes="20", dislikes="tofu").get_json()
        assert d["ok"] and store.get_profile(e.db())["diet"] == "Vegan"


def test_log_recipe_uses_database_numbers():
    with Env() as e:
        r = suggest.get_recipe(e.db(), "oats_milk_pb")
        e.post("log_recipe", recipe_id="oats_milk_pb", servings="1.5")
        row = store.log_rows(e.db())[0]
        assert row["protein"] == round(r.protein * 1.5, 1) and row["kcal"] == round(r.kcal * 1.5, 1) and row["err"] == r.err
        assert e.post("log_recipe", recipe_id="nope", servings="1").get_json()["ok"] is False
        assert e.post("log_recipe", recipe_id="oats_milk_pb", servings="99").get_json()["ok"] is False


def test_meal_confirm_flow_ignores_client_supplied_numbers():
    with Env() as e:
        d = e.post("parse_meal", meal_text="2 roti, 1 katori dal, chutney").get_json()
        assert d["sheet"] and "Roti (chapati)" in d["sheet"] and "chutney" in d["sheet"] and "check what i found" in d["sheet"].lower()
        assert d["panels"] == {}                       # nothing is logged until the user confirms
        assert e.db().execute("SELECT COUNT(*) FROM meal_log").fetchone()[0] == 0
        d = e.post("save_meal", n="3", inc_0="1", food_0="roti", qty_0="2", unit_0="", inc_1="1", food_1="dal_toor", qty_1="1",
                   unit_1="katori", food_2="", protein_0="9999", kcal_0="9999").get_json()
        assert d["ok"] and d["close"]
        rows = store.log_rows(e.db())
        assert [r["protein"] for r in rows] == [7.8, 10.2]                    # computed from the table, "9999" ignored
        assert e.post("save_meal", n="1", food_0="roti", qty_0="abc", inc_0="1").get_json()["ok"] is False
        assert e.post("save_meal", n="1", inc_0="", food_0="roti").get_json()["ok"] is False       # nothing selected


def test_meal_parse_falls_back_when_the_api_fails():
    with Env() as e:
        os.environ["GROQ_API_KEY"] = "x"
        A.llm.make_client = lambda key: BrokenClient(401)
        d = e.post("parse_meal", meal_text="2 roti").get_json()
        assert d["ok"] and "rejected the API key" in d["sheet"] and "basic parser" in d["sheet"]


def test_empty_and_unreadable_meals_give_clear_errors():
    with Env() as e:
        assert "Type what you ate" in e.post("parse_meal", meal_text="  ").get_json()["flash"][1]
        assert e.post("parse_meal", meal_text="!!!").get_json()["ok"] is False


def test_pantry_changes_suggestions_and_ignores_unknown_ids():
    with Env() as e:
        e.post("set_pantry", food=["oats", "milk", "peanut_butter", "roti", "bogus"])
        assert store.pantry_ids(e.db()) == {"oats", "milk", "peanut_butter"}
        html = e.post("set_pantry", food=["oats", "milk", "peanut_butter"]).get_json()["panels"]["today"]
        assert "in pantry" in html and "add your pantry" not in html


def test_plan_ticks_log_meals_and_untick_removes_them():
    with Env() as e:
        assert e.post("build_plan").get_json()["ok"]
        items = store.plan_rows(e.db())
        assert len(items) == 4
        e.post("tick_plan", id=str(items[0]["id"]), done="1")
        assert store.intake(e.db())["count"] == 1 and store.plan_rows(e.db())[0]["log_id"]
        e.post("tick_plan", id=str(items[0]["id"]), done="1")                  # ticking twice does not double-log
        assert store.intake(e.db())["count"] == 1
        e.post("tick_plan", id=str(items[0]["id"]))                            # unchecked boxes send no value
        assert store.intake(e.db())["count"] == 0
        e.post("tick_plan", id=str(items[1]["id"]), done="1")
        e.post("build_plan")                                                   # rebuilding clears the old plan's log entries
        assert store.intake(e.db())["count"] == 0


def test_workouts_change_targets():
    with Env() as e:
        base = A.build_ctx(e.db())["targets"].calories
        e.post("add_workouts", cat=["strength", "walking"], min_strength="60", int_strength="hard", min_walking="30", int_walking="light")
        ctx = A.build_ctx(e.db())
        assert ctx["targets"].calories > base and ctx["minutes"] == 90 and ctx["burn"] > 0
        e.post("parse_workout", workout_text="45 min easy jog")
        assert len(store.workout_rows(e.db())) == 3
        assert e.post("add_workouts").get_json()["ok"] is False
        e.post("delete_workout", id=str(store.workout_rows(e.db())[0]["id"]))
        assert len(store.workout_rows(e.db())) == 2


def test_yesterdays_log_does_not_count_today():
    with Env() as e:
        store.add_log(e.db(), "old", 500, 30, 1, 1, 0.1, "text", day="2000-01-01")
        assert store.intake(e.db())["count"] == 0
        assert "Nothing logged yet" in e.client.get("/").get_data(as_text=True)


def test_coach_needs_key_then_stores_history():
    with Env() as e:
        assert "Groq key" in e.post("chat", question="hi").get_json()["flash"][1]
        os.environ["GROQ_API_KEY"] = "x"
        A.llm.make_client = lambda key: object()
        seen = {}

        def fake_chat(client, history, q, profile, toolbox, model=None):
            seen["tools"] = sorted(toolbox.call("today_summary", {}))
            seen["diet"] = toolbox.profile["diet"]
            return {"text": "Aim for about 0.25 g/kg per meal [S8].", "trace": [{"tool": "t", "input": {}, "output": "o"}], "sources": [], "unsupported_citations": []}
        A.agent.chat = fake_chat
        d = e.post("chat", question="How much protein per meal?").get_json()
        assert d["ok"] and "0.25 g/kg" in d["panels"]["you"]
        assert [m["role"] for m in store.chat_history(e.db())] == ["user", "assistant"] and "protein_left" in seen["tools"]


def test_wipe_export_and_health():
    with Env() as e:
        e.post("log_recipe", recipe_id="pb_toast", servings="1")
        assert e.post("wipe", confirm="nope").get_json()["ok"] is False
        data = e.client.get("/export.json").get_json()
        assert len(data["meal_log"]) == 1 and "profile" in data
        assert e.post("wipe", confirm="DELETE").get_json()["ok"]
        assert store.intake(e.db())["count"] == 0
        assert e.client.get("/health").get_json()["status"] == "ok"


def test_recipe_detail_sheet():
    with Env() as e:
        html = e.client.get("/recipe/dal_roti_curd?servings=1.5").get_data(as_text=True)
        assert "Dal, roti &amp; curd" in html and "1.5 serving" in html and "youtube.com/results" in html and "I ate this" in html
        assert e.client.get("/recipe/nope").status_code == 404
        assert "4 serving" in e.client.get("/recipe/dal_roti_curd?servings=99").get_data(as_text=True)      # clamped
        e.post("set_pantry", food=["toor_dal_raw", "atta"])
        html = e.client.get("/recipe/dal_roti_curd").get_data(as_text=True)
        assert "have" in html and "need" in html


def test_session_cookie_stays_tiny():
    with Env() as e:
        for _ in range(5):
            r = e.post("log_recipe", recipe_id="pb_toast", servings="1")
        cookie = [h for h in r.headers.getlist("Set-Cookie") if h.startswith("session=")]
        assert not cookie or len(cookie[0].split(";")[0]) < 400          # only a CSRF token lives in the cookie


if __name__ == "__main__":
    for name, fn in [(k, v) for k, v in dict(globals()).items() if k.startswith("test_")]:
        fn()
        print("PASS", name)
