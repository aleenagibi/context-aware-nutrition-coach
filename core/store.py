"""Reads and writes for the user's own data: profile, pantry, meal log, workouts, plan, chat."""
import json
from datetime import date, datetime

from . import foods as F
from .workout import Session


def today():
    return date.today().isoformat()


# ---- profile & preferences ----
def get_profile(conn):
    return dict(conn.execute("SELECT * FROM profile WHERE id=1").fetchone())


def save_profile(conn, p):
    conn.execute("""UPDATE profile SET sex=?, age=?, height_cm=?, weight_kg=?, lifestyle=?, goal=?, diet=?,
                    dislikes=?, cook_minutes=? WHERE id=1""",
                 (p["sex"], p["age"], p["height_cm"], p["weight_kg"], p["lifestyle"], p["goal"], p["diet"],
                  p.get("dislikes", ""), p.get("cook_minutes", 30)))
    conn.commit()


def dislike_list(profile):
    return [d.strip().lower() for d in (profile.get("dislikes") or "").split(",") if d.strip()]


# ---- pantry ----
def pantry_ids(conn):
    ids = {r["food_id"] for r in conn.execute("SELECT food_id FROM pantry")}
    # Custom foods linked to a catalogue food can participate in recipe matching.
    try:
        ids.update(r["mapped_food_id"] for r in conn.execute(
            "SELECT mapped_food_id FROM custom_foods WHERE in_pantry=1 AND mapped_food_id IS NOT NULL"))
    except Exception:
        pass
    return ids


def set_pantry(conn, ids):
    valid = {r["id"] for r in conn.execute("SELECT id FROM foods")}
    conn.execute("DELETE FROM pantry")
    conn.executemany("INSERT INTO pantry VALUES(?)", [(i,) for i in sorted(set(ids) & valid)])
    conn.commit()


def custom_pantry_foods(conn):
    try:
        return [dict(r) for r in conn.execute(
            "SELECT id,name,mapped_food_id FROM custom_foods WHERE in_pantry=1 ORDER BY name")]
    except Exception:
        return []


def add_custom_food(conn, name, kcal=None, protein=None, carbs=None, fat=None, mapped_food_id=None):
    name = " ".join((name or "").strip().split())
    if not name or len(name) > 80:
        raise ValueError("Enter a food name up to 80 characters.")
    if mapped_food_id and not conn.execute("SELECT 1 FROM foods WHERE id=?", (mapped_food_id,)).fetchone():
        mapped_food_id = None
    vals = [kcal, protein, carbs, fat]
    if any(v is not None and (v < 0 or v > 1000) for v in vals):
        raise ValueError("Nutrition values must be between 0 and 1000 per 100 g.")
    cur = conn.execute(
        """INSERT INTO custom_foods(name,kcal,protein,carbs,fat,mapped_food_id,in_pantry,created_at)
           VALUES(?,?,?,?,?,?,1,CURRENT_TIMESTAMP)""",
        (name, kcal, protein, carbs, fat, mapped_food_id))
    conn.commit()
    return cur.lastrowid


def set_custom_pantry(conn, custom_ids):
    valid = {r["id"] for r in conn.execute("SELECT id FROM custom_foods")}
    conn.execute("UPDATE custom_foods SET in_pantry=0")
    conn.executemany("UPDATE custom_foods SET in_pantry=1 WHERE id=?",
                     [(int(i),) for i in set(custom_ids) if str(i).isdigit() and int(i) in valid])
    conn.commit()


# ---- meal log ----
def add_log(conn, label, kcal, protein, carbs, fat, err, source, ref_id=None, servings=1.0, day=None):
    cur = conn.execute(
        "INSERT INTO meal_log(day, ts, label, kcal, protein, carbs, fat, err, source, ref_id, servings) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
        (day or today(), datetime.now().isoformat(timespec="seconds"), label, kcal, protein, carbs, fat, err, source, ref_id, servings))
    conn.commit()
    return cur.lastrowid


def delete_log(conn, log_id):
    conn.execute("DELETE FROM meal_log WHERE id=?", (log_id,))
    conn.commit()


def log_rows(conn, day=None):
    return [dict(r) for r in conn.execute("SELECT * FROM meal_log WHERE day=? ORDER BY ts, id", (day or today(),))]


def intake(conn, day=None):
    """Totals for the day. protein_lo/hi apply each entry's own error margin."""
    rows = log_rows(conn, day)
    t = {"kcal": 0.0, "protein": 0.0, "carbs": 0.0, "fat": 0.0, "protein_lo": 0.0, "protein_hi": 0.0, "count": len(rows)}
    for r in rows:
        for k in ("kcal", "protein", "carbs", "fat"):
            t[k] += r[k] or 0
        t["protein_lo"] += (r["protein"] or 0) * (1 - r["err"])
        t["protein_hi"] += (r["protein"] or 0) * (1 + r["err"])
    return {k: (round(v, 1) if isinstance(v, float) else v) for k, v in t.items()}


def recent_meals(conn, n=6):
    """Most recent distinct meals, so they can be logged again with one tap."""
    rows = conn.execute("""SELECT label, kcal, protein, carbs, fat, err, source, ref_id, servings FROM meal_log
                           WHERE id IN (SELECT MAX(id) FROM meal_log GROUP BY label) ORDER BY id DESC LIMIT ?""", (n,))
    return [dict(r) for r in rows]


# ---- workouts ----
def add_workout(conn, s: Session):
    conn.execute("INSERT INTO workouts(day, activity, category, minutes, intensity) VALUES(?,?,?,?,?)",
                 (today(), s.activity, s.category, s.duration_min, s.intensity))
    conn.commit()


def delete_workout(conn, wid):
    conn.execute("DELETE FROM workouts WHERE id=?", (wid,))
    conn.commit()


def workout_rows(conn, day=None):
    return [dict(r) for r in conn.execute("SELECT * FROM workouts WHERE day=? ORDER BY id", (day or today(),))]


def sessions(conn):
    return [Session(r["activity"] or r["category"], r["category"], r["minutes"], r["intensity"]) for r in workout_rows(conn)]


# ---- plan ----
def plan_rows(conn, day=None):
    return [dict(r) for r in conn.execute("SELECT * FROM plan_items WHERE day=? ORDER BY id", (day or today(),))]


def save_plan(conn, items):
    """Replace today's plan. Any log entries created by ticked items are removed with them."""
    for row in plan_rows(conn):
        if row["log_id"]:
            conn.execute("DELETE FROM meal_log WHERE id=?", (row["log_id"],))
    conn.execute("DELETE FROM plan_items WHERE day=?", (today(),))
    conn.executemany("INSERT INTO plan_items(day, slot, recipe_id, servings) VALUES(?,?,?,?)",
                     [(today(), i["slot"], i["recipe_id"], i["servings"]) for i in items])
    conn.commit()


def set_plan_done(conn, item_id, done, macros=None, label=None, err=0.15):
    row = conn.execute("SELECT * FROM plan_items WHERE id=?", (item_id,)).fetchone()
    if not row:
        return
    if done and not row["log_id"]:
        log_id = add_log(conn, label, macros["kcal"], macros["protein"], macros["carbs"], macros["fat"], err,
                         "plan", row["recipe_id"], row["servings"])
        conn.execute("UPDATE plan_items SET log_id=? WHERE id=?", (log_id, item_id))
    elif not done and row["log_id"]:
        conn.execute("DELETE FROM meal_log WHERE id=?", (row["log_id"],))   # FK sets plan_items.log_id to NULL
    conn.commit()


# ---- chat & misc ----
def chat_history(conn, n=12):
    rows = conn.execute("SELECT role, content FROM (SELECT * FROM chat ORDER BY id DESC LIMIT ?) ORDER BY id", (n,))
    return [dict(r) for r in rows]


def add_chat(conn, role, content):
    conn.execute("INSERT INTO chat(role, content) VALUES(?,?)", (role, content))
    conn.commit()


def clear_chat(conn):
    conn.execute("DELETE FROM chat")
    conn.execute("DELETE FROM kv WHERE key='chat_meta'")
    conn.commit()


def kv_get(conn, key, default=None):
    row = conn.execute("SELECT value FROM kv WHERE key=?", (key,)).fetchone()
    return json.loads(row["value"]) if row else default


def kv_set(conn, key, value):
    conn.execute("""INSERT INTO kv(key,value) VALUES(?,?)
                   ON CONFLICT(key) DO UPDATE SET value=EXCLUDED.value""", (key, json.dumps(value)))
    conn.commit()


def export_all(conn):
    tables = ("profile", "pantry", "meal_log", "workouts", "plan_items", "chat", "custom_foods", "kv")
    return {t: [dict(r) for r in conn.execute(f"SELECT * FROM {t}")] for t in tables}


def wipe_user_data(conn):
    """Delete everything the user created. The food and recipe tables are kept."""
    for t in ("plan_items", "meal_log", "workouts", "pantry", "chat", "kv", "custom_foods"):
        conn.execute(f"DELETE FROM {t}")
    conn.execute("DELETE FROM profile")
    conn.commit()
    from .db import DEFAULT_PROFILE
    conn.execute("INSERT INTO profile VALUES(1,?,?,?,?,?,?,?,?,?)", DEFAULT_PROFILE)
    conn.commit()


def recipe_macros(recipe, servings):
    return {k: round(getattr(recipe, k) * servings, 1) for k in ("kcal", "protein", "carbs", "fat")}
