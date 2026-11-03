"""Free-text meal -> matched foods -> grams -> macros. The LLM (or the regex fallback) only extracts
names, quantities and units; matching, unit conversion and every number come from the database."""
import re

from . import foods as F

NUMBER_WORDS = {"a": 1, "an": 1, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "half": 0.5}
UNIT_WORDS = "|".join(sorted({u for u in F.UNIT_SYNONYMS if u not in ("g",)} | {"g"}, key=len, reverse=True))
SPLIT = re.compile(r"\s*(?:,|;|\n|\+|&|\band\b|\bwith\b)\s*", re.I)
ITEM = re.compile(
    rf"^(?:(?P<qty>\d+/\d+|\d+(?:\.\d+)?)|(?P<word>a|an|one|two|three|four|five|half)\b)?\s*"
    rf"(?:(?P<unit>{UNIT_WORDS})\b\s*)?(?:of\s+)?(?P<name>.+)$", re.I)


def regex_parse_meal(text):
    """Fallback parser used without an API key, or when the model output is unusable."""
    items = []
    for seg in SPLIT.split(text.strip()):
        seg = seg.strip(" .")
        m = ITEM.match(seg) if seg else None
        if not m or not re.search(r"[a-z]", m.group("name"), re.I):     # needs at least one letter
            continue
        qty = None
        if m.group("qty"):
            q = m.group("qty")
            qty = float(q.split("/")[0]) / float(q.split("/")[1]) if "/" in q else float(q)
        elif m.group("word"):
            qty = NUMBER_WORDS[m.group("word").lower()]
        items.append({"name": m.group("name").strip().lower(), "quantity": qty, "unit": m.group("unit")})
    return items


def build_item(conn, food, qty, unit, name="", how="alias"):
    """Compute grams and macros for one food. Used both when parsing and when saving the confirmed sheet."""
    grams, assumed = F.grams_for(conn, food, qty, unit)
    m = F.macros(food, grams)
    return {"name": name or food["name"], "food_id": food["id"], "food_name": food["name"], "grams": round(grams),
            "qty": qty, "unit": F.normalise_unit(unit), "how": how, "assumed": assumed,
            "err": round(F.err_fraction(food, assumed), 2), "confidence": food["confidence"], **m}


def resolve(conn, parsed):
    """Match parsed items to foods. Unmatched items come back with candidates and are never given numbers."""
    out = []
    for p in parsed:
        found = F.find_food(conn, p["name"])
        if found["food"] is None:
            out.append({"name": p["name"], "food_id": None, "how": "none", "qty": p.get("quantity"), "unit": p.get("unit"),
                        "candidates": [{"id": c["id"], "name": c["name"]} for c in found["candidates"]]})
        else:
            out.append(build_item(conn, found["food"], p.get("quantity"), p.get("unit"), p["name"], found["how"]))
    return out


def totals(items):
    """Sum matched items. protein_lo/hi use each item's own error margin."""
    t = {"kcal": 0.0, "protein": 0.0, "carbs": 0.0, "fat": 0.0, "protein_lo": 0.0, "protein_hi": 0.0}
    for i in items:
        if not i.get("food_id"):
            continue
        for k in ("kcal", "protein", "carbs", "fat"):
            t[k] += i[k]
        t["protein_lo"] += i["protein"] * (1 - i["err"])
        t["protein_hi"] += i["protein"] * (1 + i["err"])
    return {k: round(v, 1) for k, v in t.items()}
