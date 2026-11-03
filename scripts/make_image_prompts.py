"""Write data/image_prompts.csv: one consistent prompt per recipe for the image generator you choose.
Generate the pictures with that tool, then save each as static/img/foods/<recipe id>.webp (about 512 px wide).
The app uses the file automatically; recipes without a picture keep their built-in icon.
Check the generator's licence terms if the project will ever be public or commercial."""
import csv
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from core import db  # noqa: E402

VESSEL = {"bowl": "a ceramic bowl", "plate": "a plate", "glass": "a tall glass", "toast": "a plate", "egg": "a plate", "pan": "a shallow dish"}
TEMPLATE = ("Overhead photo of {name}, served in {vessel}, a realistic home-cooked portion, natural window light, "
            "dark slate table, soft shadows, shallow depth of field, no text, no people, no hands, no logos")


def main():
    conn = db.init(db.connect())
    out = Path(__file__).resolve().parent.parent / "data" / "image_prompts.csv"
    with open(out, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["id", "prompt", "save_as"])
        for r in conn.execute("SELECT id, name, art FROM recipes ORDER BY id"):
            w.writerow([r["id"], TEMPLATE.format(name=r["name"], vessel=VESSEL.get(r["art"], "a plate")), f"static/img/foods/{r['id']}.webp"])
    print(f"wrote {out}")


if __name__ == "__main__":
    main()
