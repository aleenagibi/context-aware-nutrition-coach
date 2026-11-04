"""SQLite schema and seeding. One local database file; single-user by design."""
import csv
import json
import os
import re
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
DB_PATH = Path("postgresql")

from database.engine import Connection, initialize_catalogue, initialize_user_schema

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
    """Return a PostgreSQL connection in the authenticated user's isolated schema."""
    schema = "public"
    if path and str(path) not in (":memory:", "postgresql"):
        uid = Path(str(path)).stem
        if uid and uid != "fuel": schema = "u_" + re.sub(r"[^a-f0-9]", "", uid.lower())
    return Connection(schema)


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
    """Idempotently upsert catalogue food, aliases and serving units."""
    existing = conn.execute("SELECT id FROM foods WHERE id=?", (row["id"],)).fetchone()
    if existing:
        conn.execute("""UPDATE foods SET name=:name,state=:state,category=:category,diet_level=:diet_level,
          kcal=:kcal,protein=:protein,carbs=:carbs,fat=:fat,fibre=:fibre,confidence=:confidence,
          pantry=:pantry,staple=:staple,pantry_key=:pantry_key,default_unit=:default_unit,source=:source,source_id=:source_id
          WHERE id=:id""", row)
    else:
        conn.execute("""INSERT INTO foods(id,name,state,category,diet_level,kcal,protein,carbs,fat,fibre,confidence,
          pantry,staple,pantry_key,default_unit,source,source_id)
          VALUES(:id,:name,:state,:category,:diet_level,:kcal,:protein,:carbs,:fat,:fibre,:confidence,
          :pantry,:staple,:pantry_key,:default_unit,:source,:source_id)""", row)
    conn.execute("DELETE FROM food_aliases WHERE food_id=?", (row["id"],))
    full_name = row["name"].replace("(", " ").replace(")", " ")
    for alias in {norm(a) for a in [full_name, *aliases] if norm(a)}:
        owner = conn.execute("SELECT food_id FROM food_aliases WHERE alias=?", (alias,)).fetchone()
        if owner and owner["food_id"] != row["id"]:
            raise ValueError(f"alias {alias!r} already belongs to another food")
        conn.execute("INSERT INTO food_aliases(alias,food_id) VALUES(?,?) ON CONFLICT DO NOTHING", (alias,row["id"]))
    for unit, grams in (units or {}).items():
        conn.execute("""INSERT INTO food_units(food_id,unit,grams) VALUES(?,?,?)
          ON CONFLICT(food_id,unit) DO UPDATE SET grams=EXCLUDED.grams""", (row["id"],unit,float(grams)))


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
        conn.execute(
            """
            INSERT INTO recipes
            (id, name, slot, minutes, art, steps, extras)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET name=EXCLUDED.name, slot=EXCLUDED.slot, minutes=EXCLUDED.minutes,
              art=EXCLUDED.art, steps=EXCLUDED.steps, extras=EXCLUDED.extras
            """,
            (
                r["id"],
                r["name"],
                r["slot"],
                r["minutes"],
                r["art"],
                json.dumps(r["steps"]),
                json.dumps(r["extras"])
            )
        )

        conn.execute(
            "DELETE FROM recipe_items WHERE recipe_id=?",
            (r["id"],)
        )

        conn.executemany(
            "INSERT INTO recipe_items VALUES(?,?,?)",
            [(r["id"], f, g) for f, g in r["items"]]
        )
def init(conn):
    """Initialize shared catalogue and the current user's personal schema."""
    initialize_catalogue()
    # Personal tables are created by initialize_user_schema at account provisioning.
    if not conn.execute("SELECT 1 FROM foods LIMIT 1").fetchone():
        seed_foods(conn)
    if not conn.execute("SELECT 1 FROM recipes LIMIT 1").fetchone():
        seed_recipes(conn)
    if conn.schema != "public" and not conn.execute("SELECT 1 FROM profile LIMIT 1").fetchone():
        conn.execute("INSERT INTO profile VALUES(1,?,?,?,?,?,?,?,?,?)", DEFAULT_PROFILE)
    conn.commit()
    return conn


def memory_db():
    """Create an isolated PostgreSQL schema for tests (no SQLite fallback)."""
    import uuid
    uid = uuid.uuid4().hex
    initialize_user_schema(uid)
    return init(connect(uid + ".db"))


if __name__ == "__main__":
    conn = connect()
    init(conn)
    print(f"PostgreSQL: {conn.execute('SELECT COUNT(*) FROM foods').fetchone()[0]} foods, "
          f"{conn.execute('SELECT COUNT(*) FROM recipes').fetchone()[0]} recipes")
    conn.close()
