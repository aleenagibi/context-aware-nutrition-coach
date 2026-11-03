"""Recipes, pantry matching, ranking and the daily plan. All scoring is plain Python; weights are heuristics, not tuned."""
import itertools
import json
from dataclasses import dataclass, field
from datetime import datetime
from urllib.parse import quote_plus

from . import foods as F
from .db import norm
from .store import dislike_list


@dataclass
class Recipe:
    id: str
    name: str
    slot: str
    minutes: int
    art: str
    steps: list
    extras: list
    items: list = field(default_factory=list)   # dicts: food_id, name, grams, pantry_key, staple, diet_level
    kcal: float = 0
    protein: float = 0
    carbs: float = 0
    fat: float = 0
    level: int = 0
    err: float = 0.15
    source_url: str = ""
    nutrition_source: str = "Matched food-composition ingredients"
    steps_source: str = "manual"


def load_recipes(conn, only=None):
    rows = conn.execute("SELECT * FROM recipes" + (" WHERE id=?" if only else ""), (only,) if only else ()).fetchall()
    out = []
    for r in rows:
        rec = Recipe(r["id"], r["name"], r["slot"], r["minutes"], r["art"], json.loads(r["steps"]), json.loads(r["extras"]),
                     steps_source=(r["steps_source"] if "steps_source" in r.keys() else "manual"))
        ref = conn.execute("SELECT * FROM recipe_nutrition_reference WHERE recipe_id=?", (r["id"],)).fetchone()
        source = conn.execute("SELECT source_url FROM recipe_sources WHERE recipe_id=?", (r["id"],)).fetchone()
        if source:
            rec.source_url = source["source_url"]
        weighted_err = 0.0
        for it in conn.execute("""SELECT f.*, ri.grams FROM recipe_items ri JOIN foods f ON f.id = ri.food_id
                                  WHERE ri.recipe_id=?""", (r["id"],)):
            m = F.macros(it, it["grams"])
            rec.kcal += m["kcal"]; rec.protein += m["protein"]; rec.carbs += m["carbs"]; rec.fat += m["fat"]
            rec.level = max(rec.level, it["diet_level"])
            weighted_err += m["kcal"] * F.err_fraction(it)
            rec.items.append({"food_id": it["id"], "name": F.pantry_label(it["name"]), "grams": it["grams"],
                              "pantry_key": it["pantry_key"] or it["id"], "staple": bool(it["staple"]),
                              "pantry": bool(it["pantry"]) or bool(it["pantry_key"]), "diet_level": it["diet_level"]})
        if ref and all(ref[k] is not None for k in ("kcal", "protein", "carbs", "fat")):
            # INDB's published per-serving values take precedence over incomplete
            # ingredient reconstruction; ingredient-level estimates remain available.
            rec.kcal, rec.protein, rec.carbs, rec.fat = (ref["kcal"], ref["protein"], ref["carbs"], ref["fat"])
            rec.nutrition_source = ref["source"] or "INDB per-serving values"
            rec.err = 0.10
        else:
            rec.err = round(weighted_err / max(rec.kcal, 1), 2)
        rec.kcal, rec.protein, rec.carbs, rec.fat = (round(v) if i == 0 else round(v, 1)
                                                      for i, v in enumerate((rec.kcal, rec.protein, rec.carbs, rec.fat)))
        out.append(rec)
    return out


def get_recipe(conn, recipe_id):
    found = load_recipes(conn, recipe_id)
    return found[0] if found else None


def video_url(name):
    """A search link, never a scraped or model-written URL, so it cannot be fabricated or go stale."""
    return "https://www.youtube.com/results?search_query=" + quote_plus(name + " recipe")


def coverage(recipe, pantry):
    """Which ingredients the user has / needs. Staples (oil) count as always available."""
    have, need = [], []
    for it in recipe.items:
        if it["staple"]:
            continue
        (have if it["pantry_key"] in pantry else need).append(it["name"])
    total = len(have) + len(need)
    return have, need, (len(have) / total if total else 1.0)


def violates(recipe, diet_level, dislikes):
    if recipe.level > diet_level:
        return True
    text = norm(recipe.name + " " + " ".join(i["name"] for i in recipe.items))
    return any(norm(d) and f" {norm(d)} " in f" {text} " for d in dislikes)


def current_slot(now=None):
    h = (now or datetime.now()).hour
    return "breakfast" if h < 11 else "main" if h < 15 else "snack" if h < 18 else "main"


def _score(recipe, cov, pantry_empty, want_slot, target_p, kcal_left, cook_min):
    pfit = 1 - min(1.0, abs(recipe.protein - target_p) / max(target_p, 1))
    if kcal_left is not None and kcal_left > 0 and recipe.kcal > kcal_left:
        pfit *= 0.6
    cover = 0.5 if pantry_empty else cov
    slot_fit = 1.0 if recipe.slot == want_slot else 0.4
    tfit = 1.0 if recipe.minutes <= cook_min else max(0.0, 1 - (recipe.minutes - cook_min) / 30)
    return 0.40 * pfit + 0.35 * cover + 0.15 * slot_fit + 0.10 * tfit


def rank(conn, profile, pantry, protein_left, kcal_left=None, n=6, slot=None, meals_left=2, exclude=(), now=None):
    """Best recipes for the next meal: fits the remaining protein, uses what's in the pantry, suits the time of day."""
    level = F.DIETS[profile["diet"]]
    dislikes = dislike_list(profile)
    want = slot or current_slot(now)
    target_p = min(40, max(15, (protein_left or 0) / max(meals_left, 1)))
    scored = []
    for r in load_recipes(conn):
        if r.id in exclude or violates(r, level, dislikes) or (slot and r.slot != slot):
            continue
        have, need, cov = coverage(r, pantry)
        scored.append({"recipe": r, "have": have, "need": need, "cov": cov,
                       "score": round(_score(r, cov, not pantry, want, target_p, kcal_left, profile.get("cook_minutes", 30)), 3)})
    scored.sort(key=lambda d: (-d["score"], d["recipe"].name))
    return scored[:n]


SLOT_PLAN = [("breakfast", "breakfast", 0.25), ("lunch", "main", 0.30), ("snack", "snack", 0.15), ("dinner", "main", 0.30)]
MULTIPLIERS = (0.75, 1.0, 1.25, 1.5)


def build_plan(conn, profile, targets, pantry):
    """One recipe per slot, then the servings (0.75-1.5x) that bring the day closest to the targets."""
    chosen, used = [], set()
    for slot, kind, share in SLOT_PLAN:
        top = rank(conn, profile, pantry, targets.protein_low * share * 2, None, n=1, slot=kind, meals_left=2, exclude=used)
        if top:
            used.add(top[0]["recipe"].id)
            chosen.append((slot, top[0]["recipe"]))
    if not chosen:
        return {"items": [], "kcal": 0, "protein": 0, "meets_protein": False}
    best, best_cost = None, float("inf")
    for combo in itertools.product(MULTIPLIERS, repeat=len(chosen)):
        kcal = sum(r.kcal * m for (_, r), m in zip(chosen, combo))
        protein = sum(r.protein * m for (_, r), m in zip(chosen, combo))
        cost = 2 * abs(kcal - targets.calories) / targets.calories + max(0, targets.protein_low - protein) / targets.protein_low
        if cost < best_cost:
            best, best_cost = combo, cost
    items = [{"slot": s, "recipe_id": r.id, "servings": m} for (s, r), m in zip(chosen, best)]
    kcal = round(sum(r.kcal * m for (_, r), m in zip(chosen, best)))
    protein = round(sum(r.protein * m for (_, r), m in zip(chosen, best)), 1)
    return {"items": items, "kcal": kcal, "protein": protein, "meets_protein": protein >= targets.protein_low}
