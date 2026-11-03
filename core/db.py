"""SQLite schema and seeding. One local database file; single-user by design."""
import csv
import json
import os
import re
import sqlite3
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
DB_PATH = Path(os.getenv("FUEL_DB", BASE / "data" / "fuel.db"))

SCHEMA = """
CREATE TABLE IF NOT EXISTS foods(
  id TEXT PRIMARY KEY, name TEXT NOT NULL, state TEXT, category TEXT,
  diet_level INTEGER NOT NULL DEFAULT 3,            -- 0 vegan, 1 vegetarian, 2 eggetarian, 3 non-vegetarian
  kcal REAL NOT NULL, protein REAL NOT NULL, carbs REAL NOT NULL, fat REAL NOT NULL, fibre REAL DEFAULT 0,  -- per 100 g
  confidence TEXT DEFAULT 'low',                    -- high / medium / low: how much to trust the numbers
  pantry INTEGER DEFAULT 0, staple INTEGER DEFAULT 0, pantry_key TEXT,
  default_unit TEXT DEFAULT 'serving', source TEXT, source_id TEXT);
CREATE TABLE IF NOT EXISTS food_aliases(
  alias TEXT NOT NULL, food_id TEXT NOT NULL REFERENCES foods(id) ON DELETE CASCADE, PRIMARY KEY(alias, food_id));
CREATE TABLE IF NOT EXISTS food_units(
  food_id TEXT NOT NULL REFERENCES foods(id) ON DELETE CASCADE, unit TEXT NOT NULL, grams REAL NOT NULL, PRIMARY KEY(food_id, unit));
CREATE TABLE IF NOT EXISTS recipes(
  id TEXT PRIMARY KEY, name TEXT NOT NULL, slot TEXT NOT NULL, minutes INTEGER, art TEXT, steps TEXT, extras TEXT,\n  steps_source TEXT NOT NULL DEFAULT 'manual');
CREATE TABLE IF NOT EXISTS recipe_items(
  recipe_id TEXT NOT NULL REFERENCES recipes(id) ON DELETE CASCADE, food_id TEXT NOT NULL REFERENCES foods(id), grams REAL NOT NULL);
CREATE TABLE IF NOT EXISTS recipe_nutrition_reference(
  recipe_id TEXT PRIMARY KEY REFERENCES recipes(id) ON DELETE CASCADE,
  kcal REAL, protein REAL, carbs REAL, fat REAL, fibre REAL, serving_unit TEXT, source TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS recipe_sources(
  recipe_id TEXT PRIMARY KEY REFERENCES recipes(id) ON DELETE CASCADE, source_url TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS profile(
  id INTEGER PRIMARY KEY CHECK(id = 1), sex TEXT, age INTEGER, height_cm REAL, weight_kg REAL,
  lifestyle TEXT, goal TEXT, diet TEXT, dislikes TEXT DEFAULT '', cook_minutes INTEGER DEFAULT 30);
CREATE TABLE IF NOT EXISTS pantry(food_id TEXT PRIMARY KEY REFERENCES foods(id) ON DELETE CASCADE);
CREATE TABLE IF NOT EXISTS meal_log(
  id INTEGER PRIMARY KEY AUTOINCREMENT, day TEXT NOT NULL, ts TEXT NOT NULL, label TEXT NOT NULL,
  kcal REAL, protein REAL, carbs REAL, fat REAL, err REAL DEFAULT 0.15, source TEXT, ref_id TEXT, servings REAL DEFAULT 1);
CREATE TABLE IF NOT EXISTS workouts(
  id INTEGER PRIMARY KEY AUTOINCREMENT, day TEXT NOT NULL, activity TEXT, category TEXT NOT NULL, minutes REAL NOT NULL, intensity TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS plan_items(
  id INTEGER PRIMARY KEY AUTOINCREMENT, day TEXT NOT NULL, slot TEXT NOT NULL,
  recipe_id TEXT NOT NULL REFERENCES recipes(id), servings REAL NOT NULL,
  log_id INTEGER REFERENCES meal_log(id) ON DELETE SET NULL);
CREATE TABLE IF NOT EXISTS chat(id INTEGER PRIMARY KEY AUTOINCREMENT, role TEXT NOT NULL, content TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS kv(key TEXT PRIMARY KEY, value TEXT);
"""

DEFAULT_PROFILE = ("Female", 25, 165.0, 60.0, "Light movement", "Maintain", "Vegetarian", "", 30)


def connect(path=None):
    conn = sqlite3.connect(
        str(path or DB_PATH),
        timeout=30
    )
    conn.row_factory = sqlite3.Row

    conn.execute("PRAGMA busy_timeout = 30000")
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")

    return conn


def norm(text):
    """Normalise a food name for matching: lowercase, no punctuation or brackets, crude singular."""
    text = re.sub(r"\(.*?\)", " ", text.lower())
    text = re.sub(r"[^a-z0-9 ]", " ", text)
    out = []
    for tok in text.split():
        if tok in ("a", "an", "the", "some", "of", "my"):
            continue
        if len(tok) > 3 and tok.endswith("oes"):
            tok = tok[:-2]
        elif len(tok) > 3 and tok.endswith("s") and not tok.endswith(("ss", "us")):
            tok = tok[:-1]
        out.append(tok)
    return " ".join(out)


def add_food(conn, row, aliases=(), units=None):
    """Insert or replace a food plus its aliases and unit sizes. Raises if an alias already belongs to another food."""
    conn.execute(
        """INSERT OR REPLACE INTO foods(id,name,state,category,diet_level,kcal,protein,carbs,fat,fibre,confidence,
           pantry,staple,pantry_key,default_unit,source,source_id)
           VALUES(:id,:name,:state,:category,:diet_level,:kcal,:protein,:carbs,:fat,:fibre,:confidence,
           :pantry,:staple,:pantry_key,:default_unit,:source,:source_id)""", row)
    conn.execute("DELETE FROM food_aliases WHERE food_id=?", (row["id"],))
    full_name = row["name"].replace("(", " ").replace(")", " ")   # keep "(raw)" / "(cooked)" so states do not collide
    for alias in {norm(a) for a in [full_name, *aliases] if norm(a)}:
        owner = conn.execute("SELECT food_id FROM food_aliases WHERE alias=?", (alias,)).fetchone()
        if owner and owner["food_id"] != row["id"]:
            raise ValueError(f"alias {alias!r} already belongs to {owner['food_id']}, cannot add to {row['id']}")
        conn.execute("INSERT OR IGNORE INTO food_aliases VALUES(?,?)", (alias, row["id"]))
    for unit, grams in (units or {}).items():
        conn.execute("INSERT OR REPLACE INTO food_units VALUES(?,?,?)", (row["id"], unit, float(grams)))


def seed_foods(conn, path=BASE / "data" / "seed_foods.csv"):
    with open(path, newline="", encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            units = dict(u.split("=") for u in r["units"].split(";") if u)
            add_food(conn, {
                "id": r["id"], "name": r["name"], "state": r["state"], "category": r["category"],
                "diet_level": int(r["diet"]), "kcal": float(r["kcal"]), "protein": float(r["protein"]),
                "carbs": float(r["carbs"]), "fat": float(r["fat"]), "fibre": float(r["fibre"] or 0),
                "confidence": r["confidence"], "pantry": int(r["pantry"]), "staple": int(r["staple"]),
                "pantry_key": r["pantry_key"] or None, "default_unit": r["default_unit"],
                "source": r["source"], "source_id": None}, [a for a in r["aliases"].split(";") if a], units)


def seed_recipes(conn, path=BASE / "data" / "seed_recipes.json"):
    for r in json.loads(Path(path).read_text(encoding="utf-8")):
        conn.execute("INSERT OR REPLACE INTO recipes VALUES(?,?,?,?,?,?,?)",
                     (r["id"], r["name"], r["slot"], r["minutes"], r["art"], json.dumps(r["steps"]), json.dumps(r["extras"])))
        conn.execute("DELETE FROM recipe_items WHERE recipe_id=?", (r["id"],))
        conn.executemany("INSERT INTO recipe_items VALUES(?,?,?)", [(r["id"], f, g) for f, g in r["items"]])


def init(conn):
    """Create tables; seed foods/recipes only when empty so imported data is never overwritten."""
    conn.executescript(SCHEMA)
    # Upgrade existing SQLite databases without deleting user data.
    recipe_columns = {row["name"] for row in conn.execute("PRAGMA table_info(recipes)")}
    if "steps_source" not in recipe_columns:
        conn.execute("ALTER TABLE recipes ADD COLUMN steps_source TEXT NOT NULL DEFAULT 'manual'")
    # The previous importer wrote the same generic three-step text to every imported recipe.
    generic_steps = json.dumps([
        "Prepare and measure the listed ingredients.",
        "Cook or combine the ingredients using the method appropriate to this dish.",
        "Adjust seasoning and serve. Nutrition is calculated only from ingredients with verified composition data."
    ])
    conn.execute("UPDATE recipes SET steps_source='pending' WHERE id LIKE 'dataset_%' AND steps=?",
                 (generic_steps,))
    if not conn.execute("SELECT 1 FROM foods LIMIT 1").fetchone():
        seed_foods(conn)
    if not conn.execute("SELECT 1 FROM recipes LIMIT 1").fetchone():
        seed_recipes(conn)
    if not conn.execute("SELECT 1 FROM profile").fetchone():
        conn.execute("INSERT INTO profile VALUES(1,?,?,?,?,?,?,?,?,?)", DEFAULT_PROFILE)
    conn.commit()
    return conn


def memory_db():
    """A fresh in-memory database, used by tests."""
    return init(connect(":memory:"))


if __name__ == "__main__":
    import sys
    if "--reset" in sys.argv and DB_PATH.exists():
        DB_PATH.unlink()
    conn = init(connect())
    n = conn.execute("SELECT COUNT(*) FROM foods").fetchone()[0]
    print(f"{DB_PATH}: {n} foods, {conn.execute('SELECT COUNT(*) FROM recipes').fetchone()[0]} recipes")
