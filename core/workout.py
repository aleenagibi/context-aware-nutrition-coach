"""Workout sessions: data model, MET-based calorie burn, and a regex fallback parser."""
import re
from dataclasses import dataclass

# MET values (light, moderate, hard), approximated from the Compendium of Physical Activities
MET = {
    "strength": (3.0, 3.5, 6.0),
    "running": (6.0, 9.0, 11.5),
    "cycling": (4.0, 8.0, 10.0),
    "walking": (2.8, 3.5, 5.0),
    "swimming": (5.8, 8.0, 10.0),
    "hiit": (6.0, 8.0, 10.0),
    "yoga": (2.5, 3.0, 4.0),
    "sports": (5.0, 7.0, 9.0),
    "other": (3.0, 4.5, 6.5),
}
CATEGORIES = list(MET)
INTENSITIES = ["light", "moderate", "hard"]


@dataclass
class Session:
    activity: str
    category: str
    duration_min: float
    intensity: str


def make_session(d: dict) -> Session:
    """Validate a dict (e.g. from LLM JSON) into a Session, or raise ValueError."""
    try:
        s = Session(str(d.get("activity") or d["category"]), str(d["category"]).lower(),
                    float(d["duration_min"]), str(d["intensity"]).lower())
    except (KeyError, TypeError) as e:
        raise ValueError(f"missing/invalid field: {e}")
    if s.category not in MET:
        raise ValueError(f"unknown category {s.category!r}")
    if s.intensity not in INTENSITIES:
        raise ValueError(f"unknown intensity {s.intensity!r}")
    if not 0 < s.duration_min <= 480:
        raise ValueError("duration must be between 0 and 480 minutes")
    return s


def kcal_burned(s: Session, weight_kg: float) -> float:
    met = MET[s.category][INTENSITIES.index(s.intensity)]
    return met * weight_kg * s.duration_min / 60  # kcal = MET x kg x hours


# ---------- fallback parser (used when no API key / LLM fails) ----------
_KEYWORDS = {
    "strength": ["squat", "deadlift", "bench", "press", "curl", "weights", "lifting", "gym", "strength", "pull-up", "pullup", "row"],
    "running": ["run", "jog", "sprint"],
    "cycling": ["cycl", "bike", "biking", "spin"],
    "walking": ["walk", "hike"],
    "swimming": ["swim"],
    "hiit": ["hiit", "crossfit", "circuit", "burpee"],
    "yoga": ["yoga", "pilates", "stretch"],
    "sports": ["football", "soccer", "cricket", "badminton", "tennis", "basketball", "volleyball"],
}
_HARD = ["heavy", "hard", "intense", "sprint", "max", "vigorous", "tough"]
_LIGHT = ["easy", "light", "slow", "casual", "gentle", "relaxed"]
_DUR = re.compile(r"(\d+(?:\.\d+)?)\s*(hours?|hrs?|h|minutes?|mins?)\b")


def regex_parse(text: str) -> list:
    sessions = []
    for seg in re.split(r"[,;\n+]| then | and (?=\d)", text.lower()):
        m = _DUR.search(seg)
        if not m:
            continue
        minutes = float(m.group(1)) * (60 if m.group(2).startswith("h") else 1)
        category = next((c for c, kws in _KEYWORDS.items() if any(k in seg for k in kws)), "other")
        intensity = "hard" if any(w in seg for w in _HARD) else "light" if any(w in seg for w in _LIGHT) else "moderate"
        sessions.append(Session(seg.strip(), category, minutes, intensity))
    return sessions
