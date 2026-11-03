"""Database seeding and data quality, food matching, meal parsing, recommendations, store, SVG safety, importer."""
import csv
import sys
import tempfile
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core import calc, db, foods, mealparse, store, suggest  # noqa: E402
from core.svgsafe import sanitize_svg  # noqa: E402
from scripts import import_csv  # noqa: E402

ANIMAL = {"egg", "egg white", "omelette", "paneer", "curd", "milk", "whey protein powder", "chicken breast", "tuna", "prawns",
          "white fish", "salmon", "butter", "ghee", "cheese", "greek yogurt", "buttermilk"}   # exact names, so "Peanut butter" is not flagged


# ---------- seed data ----------
def test_seed_sizes_and_integrity():
    c = db.memory_db()
    assert c.execute("SELECT COUNT(*) FROM foods").fetchone()[0] >= 80
    assert c.execute("SELECT COUNT(*) FROM recipes").fetchone()[0] >= 30
    assert c.execute("PRAGMA foreign_key_check").fetchall() == []
    assert all(r["steps"] != "[]" for r in c.execute("SELECT steps FROM recipes"))
    for r in c.execute("SELECT id, pantry_key FROM foods WHERE pantry_key IS NOT NULL"):
        assert foods.get_food(c, r["pantry_key"]) is not None, r["id"]          # cooked foods point at a real pantry item


def test_seed_numbers_are_internally_consistent():
    """kcal should roughly equal 4P + 4(C - fibre) + 2 fibre + 9F. Catches typos; does NOT prove agreement with a source."""
    c = db.memory_db()
    for r in c.execute("SELECT * FROM foods"):
        est = 4 * r["protein"] + 4 * (r["carbs"] - r["fibre"]) + 2 * r["fibre"] + 9 * r["fat"]
        assert abs(r["kcal"] - est) <= 0.15 * max(r["kcal"], 1), (r["id"], r["kcal"], round(est))


def test_reseeding_never_overwrites_user_data():
    c = db.memory_db()
    c.execute("UPDATE foods SET protein = 99 WHERE id = 'paneer'")
    db.init(c)
    assert foods.get_food(c, "paneer")["protein"] == 99


def test_alias_collisions_are_rejected():
    c = db.memory_db()
    row = dict(foods.get_food(c, "roti"))
    row["id"] = "roti2"
    try:
        db.add_food(c, row, ["chapati"])
        assert False
    except ValueError as e:
        assert "chapati" in str(e)


# ---------- matching & units ----------
def test_find_food_aliases_plurals_partials():
    c = db.memory_db()
    cases = {"rotis": ("alias", "roti"), "dal": ("alias", "dal_toor"), "boiled eggs": ("alias", "egg"), "rice": ("alias", "rice_cooked"),
             "raw rice": ("alias", "rice_raw"), "tomatoes": ("alias", "tomato"), "masala dosa": ("partial", "dosa"),
             "paneer butter masala": ("alias", "paneer_butter_masala")}
    for q, (how, fid) in cases.items():
        r = foods.find_food(c, q)
        assert (r["how"], r["food"]["id"]) == (how, fid), q
    assert foods.find_food(c, "chutney")["food"] is None


def test_unit_conversion():
    c = db.memory_db()
    g = lambda fid, q, u=None: foods.grams_for(c, foods.get_food(c, fid), q, u)  # noqa: E731
    assert g("roti", 2) == (80, False)
    assert g("almonds", 10) == (12, False)                  # a bare count of 5+ means pieces, not handfuls
    assert g("almonds", 1, "handful") == (30, False)
    assert g("milk", 250, "ml") == (250, False)
    assert g("dal_toor", 1, "bowl") == (200, True)           # generic bowl size is an assumption
    assert g("roti", None) == (40, True)                     # no quantity given: assumed one roti


def test_macros_and_error_margins():
    c = db.memory_db()
    paneer = foods.get_food(c, "paneer")
    assert foods.macros(paneer, 100)["protein"] == 18.0
    assert foods.err_fraction(foods.get_food(c, "egg")) == 0.10 and foods.err_fraction(foods.get_food(c, "dosa"), assumed=True) == 0.25


# ---------- meal parsing ----------
def test_regex_meal_parser_and_resolution():
    c = db.memory_db()
    items = mealparse.resolve(c, mealparse.regex_parse_meal("2 roti and a katori of dal, chutney, 100 g paneer"))
    by = {i["name"]: i for i in items}
    assert by["roti"]["grams"] == 80 and by["dal"]["grams"] == 150 and by["paneer"]["protein"] == 18.0
    assert by["chutney"]["food_id"] is None and "protein" not in by["chutney"]     # unmatched foods never get numbers
    t = mealparse.totals(items)
    assert t["protein"] == round(7.8 + 10.2 + 18.0, 1) and t["protein_lo"] < t["protein"] < t["protein_hi"]


def test_number_words_fractions_and_units():
    parsed = mealparse.regex_parse_meal("half katori rajma, 1/2 glass milk, three eggs, 200gm rice")
    assert [(p["name"], p["quantity"], p["unit"]) for p in parsed] == [
        ("rajma", 0.5, "katori"), ("milk", 0.5, "glass"), ("eggs", 3.0, None), ("rice", 200.0, "gm")]
    assert mealparse.regex_parse_meal("apple")[0]["quantity"] is None          # "a" inside a word is not a quantity


# ---------- recommendations ----------
def profile(**kw):
    return {"diet": "Vegetarian", "dislikes": "", "cook_minutes": 30, **kw}


def test_recipe_nutrition_is_computed_from_ingredients():
    r = suggest.get_recipe(db.memory_db(), "oats_milk_pb")
    assert r.protein == 18.6 and r.kcal == 405 and r.level == 1 and 0.05 <= r.err <= 0.3


def test_diet_and_dislike_filters():
    c = db.memory_db()
    vegan = suggest.rank(c, profile(diet="Vegan"), set(), 30, n=50)
    assert vegan and all(d["recipe"].level == 0 for d in vegan)
    for d in vegan:
        names = {i["name"].lower() for i in d["recipe"].items}
        assert not names & ANIMAL, (d["recipe"].name, names & ANIMAL)
    liked = {d["recipe"].id for d in suggest.rank(c, profile(), set(), 30, n=50)}
    disliked = {d["recipe"].id for d in suggest.rank(c, profile(dislikes="tofu, paneer"), set(), 30, n=50)}
    assert "paneer_roti_spinach" in liked and "paneer_roti_spinach" not in disliked and "tofu_scramble_toast" not in disliked


def test_pantry_changes_ranking_and_coverage():
    c = db.memory_db()
    morning = datetime(2026, 1, 1, 8)
    pantry = {"oats", "milk", "peanut_butter"}
    top = suggest.rank(c, profile(), pantry, 40, 1500, n=1, now=morning)[0]
    assert top["recipe"].id == "oats_milk_pb" and top["need"] == [] and top["cov"] == 1.0
    r = suggest.get_recipe(c, "dal_roti_curd")
    have, need, _ = suggest.coverage(r, {"toor_dal_raw", "atta"})           # cooked dal/roti are covered by raw dal/atta
    assert sorted(have) == ["Roti", "Toor dal"] and need == ["Curd"]
    _, _, cov = suggest.coverage(suggest.get_recipe(c, "egg_curry_rice"), set())
    assert cov == 0.0                                                         # oil is a staple and is ignored


def test_build_plan_meets_targets():
    c = db.memory_db()
    t = calc.compute_targets("Female", 25, 165, 60, "Light movement", "Maintain", 0, False)
    plan = suggest.build_plan(c, profile(), t, set())
    ids = [i["recipe_id"] for i in plan["items"]]
    assert len(ids) == 4 and len(set(ids)) == 4
    assert abs(plan["kcal"] - t.calories) <= 0.1 * t.calories and plan["meets_protein"]
    assert all(i["servings"] in suggest.MULTIPLIERS for i in plan["items"])


def test_video_link_is_a_search_not_a_scraped_url():
    url = suggest.video_url("Dal, roti & curd")
    assert url.startswith("https://www.youtube.com/results?search_query=") and "%26" in url


# ---------- store ----------
def test_log_intake_range_and_plan_ticking():
    c = db.memory_db()
    store.add_log(c, "a", 300, 20, 30, 10, 0.1, "text")
    store.add_log(c, "b", 200, 10, 20, 5, 0.25, "text")
    t = store.intake(c)
    assert t["protein"] == 30 and t["count"] == 2 and t["protein_lo"] == round(18 + 7.5, 1) and t["protein_hi"] == round(22 + 12.5, 1)
    store.save_plan(c, [{"slot": "lunch", "recipe_id": "dal_roti_curd", "servings": 1.0}])
    item = store.plan_rows(c)[0]
    r = suggest.get_recipe(c, "dal_roti_curd")
    store.set_plan_done(c, item["id"], True, store.recipe_macros(r, 1.0), r.name, r.err)
    assert store.intake(c)["count"] == 3 and store.plan_rows(c)[0]["log_id"]
    store.set_plan_done(c, item["id"], False)
    assert store.intake(c)["count"] == 2 and store.plan_rows(c)[0]["log_id"] is None


def test_pantry_only_accepts_pantry_foods_and_wipe_resets():
    c = db.memory_db()
    store.set_pantry(c, ["oats", "roti", "not_a_food"])          # roti (cooked) is not a pantry item
    assert store.pantry_ids(c) == {"oats"}
    store.add_log(c, "x", 1, 1, 1, 1, 0.1, "text")
    store.wipe_user_data(c)
    assert store.intake(c)["count"] == 0 and store.pantry_ids(c) == set() and store.get_profile(c)["diet"] == "Vegetarian"
    assert foods.get_food(c, "paneer") is not None               # food data survives


# ---------- SVG safety ----------
def test_svg_sanitiser():
    good = '<svg viewBox="0 0 100 100"><circle cx="50" cy="50" r="30" fill="#fb3" onclick="alert(1)"/><path d="M0 0L10 10"/></svg>'
    out = sanitize_svg("```svg\n" + good + "\n```")
    assert out and "onclick" not in out and "<circle" in out and "<path" in out
    assert sanitize_svg('<svg><script>alert(1)</script></svg>') is None
    assert sanitize_svg('<svg><foreignObject/></svg>') is None
    assert sanitize_svg('<svg><image href="http://evil.example/x.png"/></svg>') is None
    external = sanitize_svg('<svg><rect width="5" height="5" fill="url(http://evil.example/x)"/></svg>')
    assert "evil" not in external and "fill" not in external          # the external reference is stripped
    assert 'fill="url(#g1)"' in sanitize_svg('<svg><defs><linearGradient id="g1"/></defs><rect fill="url(#g1)"/></svg>')   # internal refs are fine
    assert sanitize_svg("not svg") is None and sanitize_svg("<svg><circle r='1'/>") is None


# ---------- importer ----------
def write_csv(rows):
    path = Path(tempfile.mkstemp(suffix=".csv")[1])
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["food_name", "kcal", "protein_g", "carb_g", "fat_g", "fibre_g"])
        w.writerows(rows)
    return path


def test_import_add_and_update_modes():
    c = db.memory_db()
    cols = {"name": "food_name", "kcal": "kcal", "protein": "protein_g", "carbs": "carb_g", "fat": "fat_g", "fibre": "fibre_g"}
    path = write_csv([["Banana bread", 326, 4.5, 55, 9, 2], ["Bad row", "n/a", 1, 1, 1, 1], ["Roti", 250, 8.5, 45, 3, 4]])
    with open(path, newline="", encoding="utf-8") as fh:
        added, updated, skipped = import_csv.import_rows(c, csv.DictReader(fh), cols, "add", source="TEST")
    assert (added, updated, skipped) == (1, 0, 2)                  # "Roti" already exists, the bad row is skipped
    new = foods.get_food(c, "imp_banana_bread")
    assert new["diet_level"] == 3 and new["source"] == "TEST" and foods.find_food(c, "banana bread")["food"]["id"] == "imp_banana_bread"
    with open(path, newline="", encoding="utf-8") as fh:
        added, updated, skipped = import_csv.import_rows(c, csv.DictReader(fh), cols, "update", source="TEST")
    roti = foods.get_food(c, "roti")
    assert updated == 2 and skipped == 1 and roti["kcal"] == 250    # banana bread (added above) and roti; the bad row is skipped
    assert roti["kcal"] == 250 and roti["confidence"] == "high" and roti["source"] == "TEST"


def test_import_update_never_guesses():
    c = db.memory_db()
    cols = {"name": "food_name", "kcal": "kcal", "protein": "protein_g", "carbs": "carb_g", "fat": "fat_g", "fibre": None}
    path = write_csv([["Masala dosa with chutney", 200, 5, 30, 6, 1]])        # only a partial match exists
    before = dict(foods.get_food(c, "dosa"))
    with open(path, newline="", encoding="utf-8") as fh:
        _, updated, skipped = import_csv.import_rows(c, csv.DictReader(fh), cols, "update")
    assert updated == 0 and skipped == 1 and dict(foods.get_food(c, "dosa")) == before


if __name__ == "__main__":
    for name, fn in [(k, v) for k, v in dict(globals()).items() if k.startswith("test_")]:
        fn()
        print("PASS", name)
