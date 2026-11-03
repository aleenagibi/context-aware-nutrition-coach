"""Deterministic calorie/protein maths. No LLM touches these numbers."""
from dataclasses import dataclass, field

LIFESTYLE = {  # non-exercise daily activity multiplier on BMR
    "Desk job / mostly sitting": 1.2,
    "Light movement": 1.3,
    "On my feet most of the day": 1.4,
}
GOALS = {"Lose fat": -0.15, "Maintain": 0.0, "Build muscle": 0.10}
PROTEIN_G_PER_KG = {"Lose fat": (1.8, 2.2), "Maintain": (1.4, 1.8), "Build muscle": (1.6, 2.2)}


@dataclass
class Targets:
    bmr: int
    maintenance: int
    workout_kcal: int
    calories: int
    protein_low: int
    protein_high: int
    notes: list = field(default_factory=list)


def bmr(sex: str, weight_kg: float, height_cm: float, age: int) -> float:
    """Mifflin-St Jeor equation."""
    base = 10 * weight_kg + 6.25 * height_cm - 5 * age
    return base + (5 if sex == "Male" else -161)


def compute_targets(sex, age, height_cm, weight_kg, lifestyle, goal,
                    workout_kcal=0.0, trained=False) -> Targets:
    notes = []
    b = bmr(sex, weight_kg, height_cm, age)
    maintenance = b * LIFESTYLE[lifestyle] + workout_kcal
    target = maintenance * (1 + GOALS[goal])

    floor = max(b, 1200 if sex == "Female" else 1500)
    if target < floor:
        target = floor
        notes.append("Calories raised to a safety floor. Deeper deficits need professional supervision.")

    low, high = PROTEIN_G_PER_KG[goal]
    if not trained:  # no workout today -> lower protein need
        low, high = max(0.8, low - 0.4), max(1.2, high - 0.4)
    if age < 18:
        notes.append("Formulas are for adults. Under 18, use a doctor or dietitian instead.")

    return Targets(
        bmr=round(b), maintenance=round(maintenance), workout_kcal=round(workout_kcal),
        calories=round(target), protein_low=round(low * weight_kg),
        protein_high=round(high * weight_kg), notes=notes,
    )
