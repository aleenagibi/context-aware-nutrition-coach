"""Food lookup against the database: alias matching, unit conversion, macros."""

import difflib
import re

from .db import norm

DIETS = {"Vegan": 0, "Vegetarian": 1, "Eggetarian": 2, "Non-vegetarian": 3}
CONF_ERR = {"high": 0.10, "medium": 0.15, "low": 0.25}
ASSUMED_EXTRA_ERR = 0.10

UNIT_SYNONYMS = {
    "g": "g",
    "gm": "g",
    "gms": "g",
    "gram": "g",
    "grams": "g",
    "ml": "ml",
    "millilitre": "ml",
    "milliliter": "ml",
    "cup": "cup",
    "cups": "cup",
    "katori": "katori",
    "katoris": "katori",
    "bowl": "bowl",
    "bowls": "bowl",
    "glass": "glass",
    "glasses": "glass",
    "piece": "piece",
    "pieces": "piece",
    "pcs": "piece",
    "slice": "slice",
    "slices": "slice",
    "tbsp": "tbsp",
    "tablespoon": "tbsp",
    "tablespoons": "tbsp",
    "tsp": "tsp",
    "teaspoon": "tsp",
    "teaspoons": "tsp",
    "handful": "handful",
    "handfuls": "handful",
    "scoop": "scoop",
    "scoops": "scoop",
    "plate": "plate",
    "plates": "plate",
    "serving": "serving",
    "servings": "serving",
}
GENERIC_UNITS = {
    "cup": 200,
    "katori": 150,
    "bowl": 200,
    "glass": 250,
    "tbsp": 15,
    "tsp": 5,
    "plate": 250,
}


def normalise_unit(unit):
    if not unit:
        return None
    u = str(unit).strip().lower()
    if u in UNIT_SYNONYMS:
        return UNIT_SYNONYMS[u]
    return u[:-1] if u.endswith("s") and len(u) > 3 else u


def get_food(conn, food_id):
    return conn.execute("SELECT * FROM foods WHERE id=?", (food_id,)).fetchone()


def food_units(conn, food_id):
    return {
        r["unit"]: r["grams"]
        for r in conn.execute(
            "SELECT unit, grams FROM food_units WHERE food_id=?", (food_id,)
        )
    }


def aliases(conn):
    return {
        r["alias"]: r["food_id"]
        for r in conn.execute("SELECT alias, food_id FROM food_aliases")
    }


def find_food(conn, name):
    q = norm(name)
    amap = aliases(conn)
    if q in amap:
        return {"food": get_food(conn, amap[q]), "how": "alias", "candidates": []}
    words = f" {q} "
    inside = [a for a in amap if f" {a} " in words]
    if inside:
        best = max(inside, key=lambda a: (len(a.split()), len(a)))
        return {"food": get_food(conn, amap[best]), "how": "partial", "candidates": []}
    close = difflib.get_close_matches(q, list(amap), n=1, cutoff=0.82)
    if close:
        return {
            "food": get_food(conn, amap[close[0]]),
            "how": "fuzzy",
            "candidates": [],
        }
    seen, cands = set(), []
    for a in difflib.get_close_matches(q, list(amap), n=8, cutoff=0.6):
        if amap[a] not in seen:
            seen.add(amap[a])
            cands.append(get_food(conn, amap[a]))
    return {"food": None, "how": "none", "candidates": cands[:5]}


def grams_for(conn, food, qty, unit):
    qty_given = qty is not None
    qty = float(qty) if qty_given else 1.0
    unit = normalise_unit(unit)
    units = food_units(conn, food["id"])
    assumed = not qty_given
    if unit in ("g", "ml"):
        return qty, False
    if unit is None:
        unit = (
            "piece"
            if (
                qty_given
                and qty >= 5
                and "piece" in units
                and food["default_unit"] != "piece"
            )
            else food["default_unit"]
        )
    if unit in units:
        return qty * units[unit], assumed
    if unit in GENERIC_UNITS:
        return qty * GENERIC_UNITS[unit], True
    if food["default_unit"] in units:
        return qty * units[food["default_unit"]], True
    return qty * 100, True


def macros(food, grams):
    k = grams / 100
    return {
        "kcal": round(food["kcal"] * k, 1),
        "protein": round(food["protein"] * k, 1),
        "carbs": round(food["carbs"] * k, 1),
        "fat": round(food["fat"] * k, 1),
    }


def err_fraction(food, assumed=False):
    return CONF_ERR.get(food["confidence"], 0.25) + (
        ASSUMED_EXTRA_ERR if assumed else 0
    )


def pantry_label(name):
    return re.sub(r"\s*\(.*?\)", "", name).strip()


# Cooked/prepared variants remain in foods for recipes and meal logging, but aren't
# offered as separate pantry staples. This is intentionally conservative.
COOKED_TERMS = re.compile(
    r"\b(cooked|boiled|steamed|fried|roasted|baked|grilled|prepared|soaked)\b", re.I
)
RAW_TERMS = re.compile(r"\b(raw|uncooked|dry|dried)\b", re.I)


def is_cooked_variant(name, state=None):
    state_text = f"{name or ''} {state or ''}"
    return bool(COOKED_TERMS.search(state_text)) and not bool(
        RAW_TERMS.search(state_text)
    )


def _pantry_category(name, existing):
    if existing and str(existing).strip().lower() not in {
        "other",
        "food",
        "foods",
        "misc",
        "miscellaneous",
        "ingredient",
    }:
        return str(existing).title()
    n = name.lower()
    rules = [
        (
            "Grains & cereals",
            (
                "rice",
                "wheat",
                "atta",
                "flour",
                "oat",
                "millet",
                "ragi",
                "poha",
                "quinoa",
                "pasta",
            ),
        ),
        (
            "Pulses & legumes",
            (
                "dal",
                "lentil",
                "chickpea",
                "chana",
                "rajma",
                "bean",
                "pea",
                "moong",
                "urad",
                "toor",
            ),
        ),
        (
            "Vegetables",
            (
                "potato",
                "tomato",
                "onion",
                "spinach",
                "carrot",
                "cabbage",
                "cauliflower",
                "brinjal",
                "okra",
                "capsicum",
                "vegetable",
            ),
        ),
        (
            "Fruits",
            ("apple", "banana", "mango", "orange", "grape", "papaya", "guava", "fruit"),
        ),
        (
            "Dairy & alternatives",
            ("milk", "paneer", "curd", "yogurt", "cheese", "tofu", "butter"),
        ),
        (
            "Eggs & protein",
            ("egg", "chicken", "fish", "prawn", "mutton", "meat", "soy"),
        ),
        (
            "Nuts & seeds",
            ("almond", "cashew", "peanut", "walnut", "seed", "chia", "flax"),
        ),
        ("Oils & fats", ("oil", "ghee", "margarine")),
        (
            "Spices & condiments",
            (
                "salt",
                "pepper",
                "spice",
                "masala",
                "chilli",
                "turmeric",
                "cumin",
                "coriander",
            ),
        ),
    ]
    for cat, terms in rules:
        if any(t in n for t in terms):
            return cat
    return "Other"


def pantry_foods(conn):
    rows = conn.execute(
        "SELECT id,name,state,category,pantry_key,source FROM foods ORDER BY name"
    ).fetchall()
    # Hide a cooked/prepared row only when its explicit pantry_key points to a
    # raw/basic catalogue record. If no raw counterpart exists (e.g. the seeded
    # boiled egg record), keep one representative choice so the ingredient is
    # not accidentally missing from Pantry.
    raw_ids = {r["id"] for r in rows if not is_cooked_variant(r["name"], r["state"])}
    groups = {}
    seen = set()
    for r in rows:
        if is_cooked_variant(r["name"], r["state"]) and r["pantry_key"] in raw_ids:
            continue
        label = pantry_label(r["name"])
        # Remove duplicate display names while retaining a stable, preferred record.
        key = norm(label)
        if not key or key in seen:
            continue
        seen.add(key)
        cat = _pantry_category(label, r["category"])
        groups.setdefault(cat, []).append(
            {"id": r["id"], "label": label, "source": r["source"] or "catalogue"}
        )
    return dict(sorted(groups.items()))


def protein_dense(conn, diet, n=10):
    return [
        dict(r)
        for r in conn.execute(
            """SELECT
                name,
                kcal,
                protein,
                ROUND((protein / NULLIF(kcal, 0) * 100)::numeric, 1) AS per100kcal
            FROM foods
            WHERE diet_level <= ?
                AND category != 'fats'
                AND kcal > 0
            ORDER BY protein / kcal DESC
            LIMIT ?""",
            (DIETS[diet], n),
        )
    ]
