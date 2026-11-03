"""Import nutrition data from any CSV (a USDA FoodData Central, INDB or IFCT export) into the foods table.

Two modes:
  add     insert new foods (ids are prefix + slug of the name). Existing ids are skipped.
  update  replace the nutrition values of foods you already have, matched by name/alias, and mark them confidence=high.

Column names differ per dataset, so you pass them in. Example (names are illustrative, check your file's header):
  python scripts/import_csv.py indb.csv --mode update --name-col food_name --kcal-col energy_kcal \
      --protein-col protein_g --carbs-col carb_g --fat-col fat_g --fibre-col fibre_g --source "INDB 2024"

Imported foods default to diet level 3 (non-vegetarian), so they stay hidden from vegetarian users until you review the tag.
Check each dataset's licence before redistributing the resulting database."""
import argparse
import csv
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from core import db, foods  # noqa: E402


def slug(text):
    return re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")[:60]


def number(row, col):
    try:
        return float(row[col])
    except (KeyError, ValueError, TypeError):
        return None


def import_rows(conn, rows, cols, mode="add", diet=3, state="prepared", category="imported", source="import",
                confidence="high", per=100.0, prefix="imp_"):
    added = updated = skipped = 0
    scale = 100.0 / per
    for row in rows:
        name = (row.get(cols["name"]) or "").strip()
        vals = [number(row, cols[k]) for k in ("kcal", "protein", "carbs", "fat")]
        fibre = number(row, cols["fibre"]) if cols.get("fibre") else 0.0
        if not name or any(v is None for v in vals):
            skipped += 1
            continue
        kcal, protein, carbs, fat = (v * scale for v in vals)
        fibre = (fibre or 0.0) * scale
        if mode == "update":
            found = foods.find_food(conn, name)
            if found["food"] is None or found["how"] != "alias":
                skipped += 1          # only exact name/alias matches are updated, never guesses
                continue
            conn.execute("UPDATE foods SET kcal=?, protein=?, carbs=?, fat=?, fibre=?, confidence=?, source=? WHERE id=?",
                         (kcal, protein, carbs, fat, fibre, confidence, source, found["food"]["id"]))
            updated += 1
        else:
            fid = prefix + slug(name)
            if foods.get_food(conn, fid):
                skipped += 1
                continue
            try:
                db.add_food(conn, {"id": fid, "name": name, "state": state, "category": category, "diet_level": diet,
                                   "kcal": kcal, "protein": protein, "carbs": carbs, "fat": fat, "fibre": fibre,
                                   "confidence": confidence, "pantry": 0, "staple": 0, "pantry_key": None,
                                   "default_unit": "serving", "source": source, "source_id": None},
                            units={"serving": 100})
                added += 1
            except ValueError:        # alias already owned by another food
                skipped += 1
    conn.commit()
    return added, updated, skipped


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("file")
    ap.add_argument("--mode", choices=["add", "update"], default="add")
    for k in ("name", "kcal", "protein", "carbs", "fat"):
        ap.add_argument(f"--{k}-col", required=True)
    ap.add_argument("--fibre-col")
    ap.add_argument("--per", type=float, default=100.0, help="values are per this many grams (default 100)")
    ap.add_argument("--diet", type=int, default=3, choices=[0, 1, 2, 3])
    ap.add_argument("--state", default="prepared")
    ap.add_argument("--category", default="imported")
    ap.add_argument("--source", default="import")
    ap.add_argument("--confidence", default="high", choices=["high", "medium", "low"])
    ap.add_argument("--prefix", default="imp_")
    a = ap.parse_args()
    with open(a.file, newline="", encoding="utf-8-sig") as fh:
        reader = csv.DictReader(fh)
        cols = {"name": a.name_col, "kcal": a.kcal_col, "protein": a.protein_col, "carbs": a.carbs_col, "fat": a.fat_col, "fibre": a.fibre_col}
        missing = [c for c in cols.values() if c and c not in (reader.fieldnames or [])]
        if missing:
            sys.exit(f"Columns not found: {missing}\nYour file has: {reader.fieldnames}")
        conn = db.init(db.connect())
        added, updated, skipped = import_rows(conn, reader, cols, a.mode, a.diet, a.state, a.category, a.source, a.confidence, a.per, a.prefix)
    print(f"added {added}, updated {updated}, skipped {skipped}")


if __name__ == "__main__":
    main()
