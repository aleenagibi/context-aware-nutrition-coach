"""Clean and import the supplied INDB/recipe/food-composition workbooks into Fuel.

Usage:
  python scripts/import_nutrition_datasets.py --source-dir ./source_datasets

The importer is idempotent for dataset_* recipes and fct_* foods. Existing curated
recipes are retained. Unmatched ingredients are reported and excluded from macro
sums rather than assigned invented nutrition values.
"""
from __future__ import annotations
import argparse, json, re, sys, unicodedata
from pathlib import Path
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from core import db  # noqa: E402

FILES = {
    "INDB.xlsx": "INDB.xlsx", "recipes.xlsx": "recipes.xlsx",
    "recipes_names.xlsx": "recipes_names.xlsx", "recipes_servingsize.xlsx": "recipes_servingsize.xlsx",
    "recipe_links.xlsx": "recipe_links.xlsx", "UK_fct.xlsx": "UK_fct.xlsx",
    "US_fct.xlsx": "US_fct.xlsx", "Units.xlsx": "Units.xlsx", "USDA_nrf.xlsx": "USDA_nrf.xlsx",
}
MACROS = ("energy_kcal", "protein_g", "carb_g", "fat_g", "fibre_g")


def clean_name(value):
    if pd.isna(value): return ""
    value = unicodedata.normalize("NFKD", str(value)).encode("ascii", "ignore").decode().lower()
    value = re.sub(r"\([^)]*\)", " ", value)
    value = re.sub(r"[^a-z0-9]+", " ", value)
    return " ".join(value.split())


def num(value):
    if pd.isna(value): return None
    if isinstance(value, (int, float)): return float(value)
    s = str(value).strip().lower()
    if s in {"", "n", "tr", "trace", "na", "n/a", "-", "—"}: return 0.0 if s in {"n", "tr", "trace"} else None
    try: return float(s.replace(",", ""))
    except ValueError: return None


def load_sheet(folder, name):
    path = folder / name
    if not path.exists(): raise FileNotFoundError(f"Missing workbook: {path}")
    return pd.read_excel(path, sheet_name=0)


def safe_text(v, default=""):
    return default if pd.isna(v) else str(v).strip()


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--source-dir", type=Path, required=True, help="Folder containing the 9 uploaded XLSX files")
    ap.add_argument("--db", type=Path, default=None, help="Optional SQLite database path")
    args = ap.parse_args(); src = args.source_dir
    data = {k: load_sheet(src, v) for k, v in FILES.items()}
    indb, ingredients = data["INDB.xlsx"], data["recipes.xlsx"]
    names, servings = data["recipes_names.xlsx"], data["recipes_servingsize.xlsx"]
    links, uk, us = data["recipe_links.xlsx"], data["UK_fct.xlsx"], data["US_fct.xlsx"]

    # Build a canonical ingredient table from food-composition sheets. INDB contains
    # recipe-level records; UK/US FCT sheets provide the ingredient food-code namespace.
    fct = pd.concat([uk.assign(_source="UK FCT"), us.assign(_source="US FCT")], ignore_index=True)
    fct = fct.dropna(subset=["food_code"]).copy()
    fct["food_code"] = fct.food_code.astype(str).str.strip()
    fct["_norm"] = fct.food_name.map(clean_name)
    by_code = {}
    by_name = {}
    for _, row in fct.iterrows():
        code = row.food_code
        vals = {k: num(row.get(k)) for k in MACROS}
        # Skip incomplete macro profiles; zero is a valid nutrient value.
        if any(vals[k] is None for k in MACROS[:4]): continue
        item = {"code": code, "name": safe_text(row.food_name), "source": safe_text(row.get("_source")), **vals}
        by_code.setdefault(code, item)
        if row["_norm"]: by_name.setdefault(row["_norm"], item)

    # A recipe ingredient code may be from the older source namespace. Prefer exact
    # food code, then exact normalized food name; never fuzzy-match silently.
    matched, unmatched = {}, []
    for _, row in ingredients.iterrows():
        code = safe_text(row.get("food_code")); name = safe_text(row.get("food_name")) or safe_text(row.get("food_name_org"))
        item = by_code.get(code) or by_name.get(clean_name(name))
        if item: matched[(code, clean_name(name))] = item
        elif name: unmatched.append({"food_code": code, "food_name": name})

    # Save cleaned, auditable exports next to the app. Keep raw workbooks outside the app.
    outdir = ROOT / "data" / "nutrition"
    outdir.mkdir(parents=True, exist_ok=True)
    fct_clean = pd.DataFrame(list({x["code"]: x for x in by_code.values()}.values()))
    fct_clean.to_csv(outdir / "food_composition_clean.csv", index=False)
    # Standardized ingredient rows: trim identifiers/names, numeric amounts, normalized units.
    clean_ingredients = ingredients.copy()
    for col in ("recipe_code", "food_code", "unit"):
        if col in clean_ingredients: clean_ingredients[col] = clean_ingredients[col].map(safe_text)
    for col in ("recipe_name", "food_name", "ingredient_name_org"):
        if col in clean_ingredients: clean_ingredients[col] = clean_ingredients[col].map(safe_text)
    clean_ingredients["amount"] = clean_ingredients["amount"].map(num)
    clean_ingredients.to_csv(outdir / "recipe_ingredients_clean.csv", index=False)
    clean_indb = indb.copy()
    for col in clean_indb.columns:
        if col not in {"food_code", "food_name", "primarysource", "servings_unit"}:
            clean_indb[col] = clean_indb[col].map(num)
    clean_indb.to_csv(outdir / "indb_recipe_nutrition_clean.csv", index=False)
    clean_uk = uk.copy(); clean_us = us.copy()
    for frame in (clean_uk, clean_us):
        for col in frame.columns:
            if col not in {"food_code", "food_code_org", "food_name", "primarysource"}:
                frame[col] = frame[col].map(num)
    clean_uk.to_csv(outdir / "uk_food_composition_clean.csv", index=False)
    clean_us.to_csv(outdir / "us_food_composition_clean.csv", index=False)
    data["Units.xlsx"].dropna(how="all").to_csv(outdir / "unit_conversion_reference.csv", index=False)
    clean_nrf = data["USDA_nrf.xlsx"].dropna(how="all").copy()
    for col in clean_nrf.columns:
        if col not in {"Retention Description"}: clean_nrf[col] = clean_nrf[col].map(num)
    clean_nrf.to_csv(outdir / "usda_nutrient_retention_reference.csv", index=False)
    names.to_csv(outdir / "recipe_names_clean.csv", index=False)
    servings.to_csv(outdir / "recipe_serving_sizes_clean.csv", index=False)
    links.to_csv(outdir / "recipe_links_clean.csv", index=False)
    (outdir / "unmatched_ingredients.json").write_text(json.dumps(sorted({(x['food_code'],x['food_name']) for x in unmatched}), ensure_ascii=False, indent=2), encoding="utf-8")

    # Recipe lookup tables.
    name_map = {safe_text(r.recipe_code): safe_text(r.recipe_name) for r in names.itertuples()}
    serve_map = {safe_text(r.recipe_code): r for r in servings.itertuples()}
    link_map = {safe_text(r.get("Food Code")): safe_text(r.get("Link")) for _, r in links.iterrows()}
    recipe_groups = ingredients.groupby("recipe_code", sort=False)

    conn = db.init(db.connect(args.db))
    food_inserted = food_updated = recipe_count = ingredient_count = partial_count = 0
    unmatched_by_recipe = []
    try:
        conn.execute("BEGIN")
        for item in by_code.values():
            fid = "fct_" + re.sub(r"[^A-Za-z0-9_-]", "_", item["code"])
            row = {"id": fid, "name": item["name"], "state": "as listed", "category": "food composition",
                   "diet_level": 3, "kcal": item["energy_kcal"], "protein": item["protein_g"],
                   "carbs": item["carb_g"], "fat": item["fat_g"], "fibre": item["fibre_g"] or 0,
                   "confidence": "high", "pantry": 0, "staple": 0, "pantry_key": None,
                   "default_unit": "g", "source": item["source"], "source_id": item["code"]}
            exists = conn.execute("SELECT 1 FROM foods WHERE id=?", (fid,)).fetchone()
            if exists:
                conn.execute("UPDATE foods SET name=:name,kcal=:kcal,protein=:protein,carbs=:carbs,fat=:fat,fibre=:fibre,confidence=:confidence,source=:source,source_id=:source_id WHERE id=:id", row)
                food_updated += 1
            else:
                conn.execute("INSERT INTO foods(id,name,state,category,diet_level,kcal,protein,carbs,fat,fibre,confidence,pantry,staple,pantry_key,default_unit,source,source_id) VALUES(:id,:name,:state,:category,:diet_level,:kcal,:protein,:carbs,:fat,:fibre,:confidence,:pantry,:staple,:pantry_key,:default_unit,:source,:source_id)", row)
                food_inserted += 1
            conn.execute("INSERT OR IGNORE INTO food_aliases(alias,food_id) VALUES(?,?)", (db.norm(item["name"]), fid))
            conn.execute("INSERT OR REPLACE INTO food_units(food_id,unit,grams) VALUES(?,?,?)", (fid, "g", 1.0))

        # Approximate kitchen-volume conversions are explicitly medium confidence.
        # Units with unknown mass (sprig, pinch, drops, nos, sheet) remain excluded.
        grams_per_unit = {"g":1.0,"gram":1.0,"grams":1.0,"ml":1.0,"tsp":5.0,"tbsp":15.0,"C":240.0,"cup":240.0}
        for code, group in recipe_groups:
            rid = "dataset_" + re.sub(r"[^A-Za-z0-9_-]", "_", safe_text(code))
            rname = name_map.get(safe_text(code)) or safe_text(group.iloc[0].get("recipe_name")) or f"Recipe {code}"
            sv = serve_map.get(safe_text(code)); servings_n = num(getattr(sv, "no_of_servings", 1)) if sv else 1
            servings_n = max(1.0, servings_n or 1.0)
            mapped_rows, missing_rows = [], []
            for _, ing in group.iterrows():
                code_food = safe_text(ing.get("food_code")); food_name = safe_text(ing.get("food_name")) or safe_text(ing.get("food_name_org"))
                comp = by_code.get(code_food) or by_name.get(clean_name(food_name))
                unit = safe_text(ing.get("unit"))
                amount = num(ing.get("amount"))
                factor = grams_per_unit.get(unit)
                if not comp or amount is None or factor is None:
                    missing_rows.append(food_name or code_food); continue
                fid = "fct_" + re.sub(r"[^A-Za-z0-9_-]", "_", comp["code"])
                grams = amount * factor / servings_n
                if grams > 0: mapped_rows.append((fid, grams))
            if not mapped_rows:
                # Do not create an empty recipe that appears to have zero nutrition.
                continue
            extras = sorted(set(missing_rows))
            slot = "breakfast" if any(x in clean_name(rname) for x in ["tea", "coffee", "breakfast", "porridge", "idli", "dosa", "upma", "poha"]) else "snack" if any(x in clean_name(rname) for x in ["drink", "juice", "shake", "snack", "chutney"]) else "main"
            conn.execute("INSERT OR REPLACE INTO recipes(id,name,slot,minutes,art,steps,extras,steps_source) VALUES(?,?,?,?,?,?,?,?)",
                         (rid, rname, slot, 20, "bowl", json.dumps([]), json.dumps(extras), "pending"))
            conn.execute("DELETE FROM recipe_items WHERE recipe_id=?", (rid,))
            conn.executemany("INSERT INTO recipe_items(recipe_id,food_id,grams) VALUES(?,?,?)", [(rid, f, g) for f,g in mapped_rows])
            # Use INDB's directly reported per-serving nutrients as the primary recipe
            # estimate when present; ingredient reconstruction remains the fallback.
            refrow = indb[indb.food_code.astype(str).str.strip() == safe_text(code)]
            if not refrow.empty:
                ref = refrow.iloc[0]
                values = [num(ref.get("unit_serving_energy_kcal")), num(ref.get("unit_serving_protein_g")),
                          num(ref.get("unit_serving_carb_g")), num(ref.get("unit_serving_fat_g")), num(ref.get("unit_serving_fibre_g"))]
                if all(v is not None for v in values[:4]):
                    conn.execute("INSERT OR REPLACE INTO recipe_nutrition_reference(recipe_id,kcal,protein,carbs,fat,fibre,serving_unit,source) VALUES(?,?,?,?,?,?,?,?)",
                                 (rid, *values, safe_text(ref.get("servings_unit")), "INDB per-serving values"))
            source_url = link_map.get(safe_text(code), "")
            if source_url.startswith(("https://", "http://")):
                conn.execute("INSERT OR REPLACE INTO recipe_sources(recipe_id,source_url) VALUES(?,?)", (rid, source_url))
            recipe_count += 1; ingredient_count += len(mapped_rows)
            if extras: partial_count += 1; unmatched_by_recipe.append({"recipe_code":safe_text(code),"recipe_name":rname,"unmatched_ingredients":extras})
        conn.commit()
    except Exception:
        conn.rollback(); raise
    finally: conn.close()

    (outdir / "recipe_ingredient_coverage.json").write_text(json.dumps(unmatched_by_recipe, ensure_ascii=False, indent=2), encoding="utf-8")
    report = {"food_composition_records":len(by_code),"foods_inserted":food_inserted,"foods_updated":food_updated,
              "dataset_recipes_imported":recipe_count,"recipe_ingredient_links":ingredient_count,
              "recipes_with_unmatched_or_unconvertible_ingredients":partial_count,
              "unique_unmatched_ingredient_names":len({x['food_name'] for x in unmatched}),
              "unit_reference_rows":int(data["Units.xlsx"].dropna(how="all").shape[0]),
              "usda_retention_reference_rows":int(data["USDA_nrf.xlsx"].dropna(how="all").shape[0]),
              "note":"INDB per-serving values are preferred for recipe macros. Volume-to-mass conversions are estimates; unknown units are excluded. USDA retention factors are retained as reference data and are not applied a second time to already-reported nutrient values. See coverage JSON."}
    (outdir / "import_report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))

if __name__ == "__main__": main()
