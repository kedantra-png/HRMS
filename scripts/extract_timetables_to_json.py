import os
import json
import sys
from pathlib import Path
from io import BytesIO

from PIL import Image

# Load GOOGLE_API_KEY from .env in project root (D:\HRMS)
try:
    from dotenv import load_dotenv
    ROOT_DIR = Path(__file__).resolve().parent.parent  # D:\HRMS
    env_path = ROOT_DIR / ".env"
    if env_path.exists():
        load_dotenv(env_path)
except Exception:
    pass

try:
    from google import genai  # new Gemini SDK
except ImportError:
    genai = None


def extract_timetable_structure(image: Image.Image):
    """
    Call Google Gemini (new google-genai SDK) to extract a structured timetable JSON
    from a timetable image.
    """
    api_key = (os.getenv("GOOGLE_API_KEY") or "").strip()
    if not api_key or genai is None:
        print("  [WARN] GOOGLE_API_KEY not set or google-genai not installed.")
        return None

    try:
        client = genai.Client(api_key=api_key)

        buf = BytesIO()
        image.save(buf, format="PNG")
        img_bytes = buf.getvalue()

        prompt = """
You are reading a college faculty timetable image.
Extract the timetable into pure JSON. Return ONLY JSON, no explanation.

Use this schema:
{
  "faculty_name": "<string>",
  "total_hours": <int>,  // total teaching hours per week if shown, else 0
  "slots": [
    {
      "day": "MONDAY" | "TUESDAY" | "WEDNESDAY" | "THURSDAY" | "FRIDAY" | "SATURDAY",
      "session": "0" | "I" | "II" | "III" | "IV" | "V" | "VI" | "VII",
      "time": "<time-range as text, e.g. '8:50-9:40'>",
      "subject": "<subject / class text from the cell>",
      "notes": "<any extra text in that cell or ''>"
    }
  ]
}

Include one slot for each non-empty cell in the main timetable grid.
"""

        resp = client.models.generate_content(
            model="gemini-1.5-flash",
            contents=[
                {
                    "role": "user",
                    "parts": [
                        {"text": prompt},
                        {
                            "inline_data": {
                                "mime_type": "image/png",
                                "data": img_bytes,
                            }
                        },
                    ],
                }
            ],
        )

        text = (resp.text or "").strip()
        if text.startswith("```"):
            text = text.strip("`")
            if "\n" in text:
                text = "\n".join(text.split("\n")[1:])

        data = json.loads(text)
        if not isinstance(data, dict):
            return None
        if "slots" in data and not isinstance(data["slots"], list):
            data["slots"] = []
        return data
    except Exception as e:
        print(f"  [ERROR] AI extraction failed: {e}")
        return None


def process_folder(input_dir: Path, output_dir: Path) -> None:
    """Read all images in input_dir, extract timetable JSON, save to output_dir."""
    if not input_dir.is_dir():
        print(f"[ERROR] Input folder does not exist: {input_dir}")
        return

    output_dir.mkdir(parents=True, exist_ok=True)

    exts = {".png", ".jpg", ".jpeg", ".webp"}
    images = sorted(
        [p for p in input_dir.iterdir() if p.suffix.lower() in exts and p.is_file()]
    )

    if not images:
        print(f"[INFO] No images found in: {input_dir}")
        return

    print(f"[INFO] Found {len(images)} image(s) in {input_dir}")
    for img_path in images:
        print(f"  -> Processing {img_path.name} ...", end=" ", flush=True)
        try:
            with Image.open(img_path) as img:
                img = img.convert("RGB")
                data = extract_timetable_structure(img)
        except Exception as e:
            print(f"ERROR (opening image): {e}")
            continue

        if not data:
            print("NO DATA")
            continue

        out_name = img_path.stem + ".json"
        out_path = output_dir / out_name
        try:
            with open(out_path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            print(f"OK -> {out_path.name}")
        except Exception as e:
            print(f"ERROR (saving JSON): {e}")


def main(argv=None):
    if argv is None:
        argv = sys.argv

    if len(argv) < 3:
        print("Usage:")
        print("  python scripts\\extract_timetables_to_json.py static\\timetables static\\timetable_json")
        print("  or (for cropped per-faculty images)")
        print("  python scripts\\extract_timetables_to_json.py static\\timetable_splits static\\timetable_json")
        return

    input_dir = Path(argv[1]).resolve()
    output_dir = Path(argv[2]).resolve()
    process_folder(input_dir, output_dir)


if __name__ == "__main__":
    main()