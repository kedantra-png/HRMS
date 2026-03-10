import os
import io
import re
from typing import List, Dict, Tuple, Optional

import fitz  # PyMuPDF
from PIL import Image
import pytesseract
from difflib import get_close_matches

try:
    import google.generativeai as genai
except ImportError:  # pragma: no cover - optional dependency
    genai = None


def _configure_tesseract() -> None:
    """
    Optional Windows-friendly override.
    If Tesseract is installed but not in PATH, set env var:
      TESSERACT_CMD=C:\\Program Files\\Tesseract-OCR\\tesseract.exe
    """
    cmd = (os.getenv("TESSERACT_CMD") or "").strip()
    if cmd:
        pytesseract.pytesseract.tesseract_cmd = cmd


def _extract_faculty_name(text: str) -> str | None:
    """
    Try to extract faculty name from OCR text.
    Looks for lines like: 'FACULTY: Mr. MAHESH KUMAR'
    and intentionally ignores header lines such as
    'FACULTY INDIVIDUAL TIME TABLE: 2025-26 (II TERM)'.
    """
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]

    def _cleanup_name(raw: str) -> str:
        """
        OCR sometimes captures extra fields on the same line, e.g.
        'FACULTY: Mr. RAGHURAM SHETTY MENTOR: ... TOTAL: 17 Hrs'
        We only want the actual faculty name.
        """
        raw = re.sub(r"\s+", " ", (raw or "")).strip()
        if not raw:
            return ""
        # Remove anything after common keywords that appear after the name
        raw_upper = raw.upper()
        cut_keywords = ["MENTOR", "TOTAL", "DEPARTMENT", "DEPT", "PRINCIPAL", "TIME TABLE"]
        cut_idx = None
        for kw in cut_keywords:
            m = re.search(rf"\b{re.escape(kw)}\b", raw_upper)
            if m:
                cut_idx = m.start() if cut_idx is None else min(cut_idx, m.start())
        if cut_idx is not None:
            raw = raw[:cut_idx].strip()
        # Remove trailing punctuation/dashes/colons
        raw = re.sub(r"[\s:\-–—]+$", "", raw).strip()
        return raw

    # First, prefer lines that explicitly start with 'FACULTY:'
    for line in lines:
        norm = re.sub(r"\s+", " ", line.upper())
        # Skip header like 'FACULTY INDIVIDUAL TIME TABLE: 2025-26 (II TERM)'
        if "TIME TABLE" in norm:
            continue
        if norm.startswith("FACULTY:"):
            parts = line.split(":", 1)
            if len(parts) == 2:
                name = _cleanup_name(parts[1])
                # Ignore if this is clearly just a year / term (mostly digits)
                if re.fullmatch(r"[0-9\s\-\(\)IVX]+", name.upper()):
                    continue
                if name:
                    return name

    # Fallback: any line that begins with 'FACULTY' but not 'TIME TABLE'
    for line in lines:
        norm = re.sub(r"\s+", " ", line.upper())
        if "TIME TABLE" in norm:
            continue
        if norm.startswith("FACULTY"):
            parts = re.split(r"[:\-]", line, maxsplit=1)
            if len(parts) == 2:
                name = _cleanup_name(parts[1])
                if re.fullmatch(r"[0-9\s\-\(\)IVX]+", name.upper()):
                    continue
                if name:
                    return name
    return None


def _normalize_name(text: str) -> str:
    """
    Lightweight normalization used when matching OCR text against a list
    of known faculty names (for pages that don't have an explicit FACULTY line).
    """
    text = (text or "").upper()
    # Remove common trailing fields that are not part of the name
    text = re.split(r"\b(MENTOR|TOTAL|DEPARTMENT|DEPT)\b", text, maxsplit=1)[0]
    text = re.sub(r"\b(MR|MRS|MS|MISS|DR|PROF|PROFESSOR)\.?\b", "", text)
    text = re.sub(r"[^A-Z\s]", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _fallback_detect_faculty_from_page_text(
    page_text: str, known_faculty_names: List[str]
) -> Optional[str]:
    """
    When no explicit 'FACULTY:' line is present, attempt to infer the faculty
    name by matching the page OCR text against a list of known names.
    """
    if not page_text or not known_faculty_names:
        return None

    norm_page = _normalize_name(page_text)
    if not norm_page:
        return None

    # Token set of the full page text
    page_tokens = set(norm_page.split())

    best_name = None
    best_score = 0.0

    for name in known_faculty_names:
        norm_name = _normalize_name(name)
        if not norm_name:
            continue

        name_tokens = set(norm_name.split())
        if not name_tokens:
            continue

        # Overlap score (0..1) based on common tokens
        common = page_tokens & name_tokens
        if not common:
            continue

        score = len(common) / len(name_tokens)
        if score > best_score:
            best_score = score
            best_name = name

    # Require at least 60% of the name tokens to appear in the page text
    if best_score >= 0.6:
        return best_name

    # Fallback: fuzzy string similarity on normalized strings
    norm_known = [_normalize_name(n) for n in known_faculty_names]
    best = get_close_matches(norm_page, norm_known, n=1, cutoff=0.8)
    if best:
        idx = norm_known.index(best[0])
        return known_faculty_names[idx]

    return None


def extract_timetable_structure(image: Image.Image) -> Optional[Dict]:
    """
    Optional AI-powered extraction of structured timetable data from a cropped
    faculty timetable image using Google Gemini (vision model).

    Returns a dictionary like:
    {
        "faculty_name": "...",
        "total_hours": 18,
        "slots": [
            {
                "day": "MONDAY",
                "session": "I",
                "time": "9:45-10:35",
                "subject": "II BCA B Python",
                "notes": ""
            },
            ...
        ]
    }

    If the GOOGLE_API_KEY is not configured or google-generativeai is missing,
    this function returns None and the rest of the system continues to work
    without structured data.
    """
    api_key = (os.getenv("GOOGLE_API_KEY") or "").strip()
    if not api_key or genai is None:
        return None

    try:
        # Use stable v1 API so models like gemini-1.5-flash are available
        genai.configure(api_key=api_key, api_version="v1")
        model = genai.GenerativeModel("gemini-1.5-flash")

        buf = io.BytesIO()
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

        resp = model.generate_content(
            [
                prompt,
                {"mime_type": "image/png", "data": img_bytes},
            ]
        )

        text = (resp.text or "").strip()
        # Sometimes Gemini wraps JSON in markdown code fences
        if text.startswith("```"):
            text = text.strip("`")
            # remove possible language hint like ```json
            if "\n" in text:
                text = "\n".join(text.split("\n")[1:])

        import json as _json

        data = _json.loads(text)
        # Basic shape validation
        if not isinstance(data, dict):
            return None
        if "slots" in data and not isinstance(data["slots"], list):
            data["slots"] = []
        return data
    except Exception:
        # Fail silently – structured extraction is an enhancement only
        return None


def pdf_to_faculty_images(
    pdf_bytes: bytes,
    known_faculty_names: Optional[List[str]] = None,
) -> Tuple[List[Dict], List[Dict]]:
    """
    Convert each page in the PDF into an image, OCR it,
    and extract the faculty name.

    Returns (pages_with_name, pages_without_name)
    where each entry is:
      {
        "page_index": int,
        "faculty_name": str | None,
        "image": PIL.Image.Image,
        "ocr_text": str,
      }
    """
    with io.BytesIO(pdf_bytes) as pdf_stream:
        doc = fitz.open(stream=pdf_stream.read(), filetype="pdf")

    # Prepare debug/visualisation folders:
    #  - static/timetable_images: full page renders (page_1.png, page_2.png, ...)
    #  - static/timetable_splits: per-page timetable crops (page_1_top.png, page_1_bottom.png, ...)
    try:
        project_root = os.path.dirname(os.path.abspath(os.path.join(__file__, os.pardir)))
        static_root = os.path.join(project_root, "static")
        timetable_images_dir = os.path.join(static_root, "timetable_images")
        timetable_splits_dir = os.path.join(static_root, "timetable_splits")
        os.makedirs(timetable_images_dir, exist_ok=True)
        os.makedirs(timetable_splits_dir, exist_ok=True)
    except Exception:
        timetable_images_dir = None
        timetable_splits_dir = None

    pages_with_name: List[Dict] = []
    pages_without_name: List[Dict] = []

    _configure_tesseract()

    for page_index in range(len(doc)):
        page = doc.load_page(page_index)
        page_number = page_index + 1

        # Save the whole page as an image (timetable_images/page_N.png)
        if timetable_images_dir:
            try:
                full_pix = page.get_pixmap(dpi=200)
                full_mode = "RGBA" if full_pix.alpha else "RGB"
                full_img = Image.frombytes(full_mode, (full_pix.width, full_pix.height), full_pix.samples)
                full_img.save(os.path.join(timetable_images_dir, f"page_{page_number:02d}.png"), format="PNG")
            except Exception:
                pass

        # Use text blocks so we can locate ALL FACULTY / PRINCIPAL sections
        # on the page, allowing multiple timetables per page.
        blocks = page.get_text("blocks") or []
        embedded_text = (page.get_text("text") or "").strip()

        page_rect = page.rect

        # First pass: find all (faculty_name, top, bottom) segments.
        segments: List[Dict] = []
        current: Dict | None = None
        last_heading_y: float | None = None

        for b in blocks:
            if len(b) < 5:
                continue
            x0, y0, x1, y1, text = b[0], b[1], b[2], b[3], b[4]
            if not isinstance(text, str):
                continue
            norm = re.sub(r"\s+", " ", text.upper())

            # Capture the main college heading so that each timetable crop
            # can start slightly above it (as in your visual examples).
            if "DR. B. B. HEGDE FIRST GRADE COLLEGE" in norm:
                last_heading_y = y0

            # New FACULTY header (not the 'FACULTY INDIVIDUAL TIME TABLE' title)
            if "FACULTY" in norm and "TIME TABLE" not in norm:
                name_candidate = _extract_faculty_name(text)
                if name_candidate:
                    # If a previous segment is still open without an explicit bottom,
                    # close it at this new header.
                    if current and current.get("bottom") is None:
                        current["bottom"] = y0
                        segments.append(current)

                    # Use the last seen college heading (if any) as the top of
                    # this segment so that the saved image goes from the header
                    # down to PRINCIPAL.
                    seg_top = last_heading_y if last_heading_y is not None else y0
                    current = {
                        "faculty_name": name_candidate,
                        "top": seg_top,
                        "bottom": None,
                    }
                    last_heading_y = None
                    continue

            # PRINCIPAL line closes the current segment (if any).
            # Now we include the PRINCIPAL text itself in the crop, so the final
            # image runs visually from the college header down to PRINCIPAL.
            if current and "PRINCIPAL" in norm and y0 > (current.get("top", page_rect.y0) + 80):
                # Use the block bottom (y1) plus a small margin so PRINCIPAL
                # is clearly visible, but never go past the page bottom.
                principal_bottom = min(page_rect.y1, y1 + 10)
                current["bottom"] = max(current.get("top", page_rect.y0) + 100, principal_bottom)
                segments.append(current)
                current = None

        # If a segment is still open at end of page, close it at page bottom
        if current:
            current["bottom"] = page_rect.y1
            segments.append(current)

        # If we didn't detect any segments but we have known faculty names,
        # fall back to OCR-based detection from full page text.
        # Counter of segments for this page (used when saving visual split images)
        seg_counter = 0

        if not segments:
            # OCR once for the page if needed
            ocr_text = ""
            try:
                pix_full = page.get_pixmap(dpi=200)
                mode_full = "RGBA" if pix_full.alpha else "RGB"
                image_full = Image.frombytes(mode_full, (pix_full.width, pix_full.height), pix_full.samples)
                ocr_text = pytesseract.image_to_string(image_full)
            except pytesseract.TesseractNotFoundError as e:
                raise RuntimeError(
                    "Tesseract OCR is required for scanned PDFs, but it was not found. "
                    "Install Tesseract and add it to PATH, or set TESSERACT_CMD "
                    "to the full path of tesseract.exe."
                ) from e
            except Exception:
                image_full = None

            # If this is a scanned PDF, embedded_text is often empty and there may be
            # multiple timetables on one page. Use OCR word boxes to find multiple
            # "FACULTY ... PRINCIPAL" segments and crop each one.
            if image_full is not None:
                try:
                    data = pytesseract.image_to_data(image_full, output_type=pytesseract.Output.DICT)
                    lines = {}
                    n = len(data.get("text", []))
                    for i in range(n):
                        word = (data["text"][i] or "").strip()
                        if not word:
                            continue
                        key = (
                            data.get("block_num", [0])[i],
                            data.get("par_num", [0])[i],
                            data.get("line_num", [0])[i],
                        )
                        top = int(data.get("top", [0])[i] or 0)
                        lines.setdefault(key, {"top": top, "words": []})
                        lines[key]["top"] = min(lines[key]["top"], top)
                        lines[key]["words"].append(word)

                    line_items = []
                    for v in lines.values():
                        txt = re.sub(r"\s+", " ", " ".join(v["words"]).strip())
                        if txt:
                            line_items.append({"top": v["top"], "text": txt})
                    line_items.sort(key=lambda x: x["top"])

                    faculty_lines = []
                    header_top = None
                    principal_tops = []
                    for item in line_items:
                        norm = re.sub(r"\s+", " ", item["text"].upper())
                        # Capture the college heading position so each cropped
                        # timetable starts from the same header line.
                        if "DR. B. B. HEGDE FIRST GRADE COLLEGE" in norm:
                            if header_top is None:
                                header_top = item["top"]
                        if "PRINCIPAL" in norm:
                            principal_tops.append(item["top"])
                        if "FACULTY" in norm and "TIME TABLE" not in norm:
                            nm = _extract_faculty_name(item["text"])
                            if nm:
                                faculty_lines.append({"top": item["top"], "faculty_name": nm})

                    principal_tops.sort()
                    faculty_lines.sort(key=lambda x: x["top"])

                    if faculty_lines:
                        # A timetable crop should include the grid down to SATURDAY.
                        # Use a minimum vertical gap so we don't accidentally crop just the header.
                        min_gap_px = max(180, int(image_full.height * 0.10))

                        for idx, fline in enumerate(faculty_lines):
                            # If we detected the main college header, start the
                            # crop from there so the image runs from header to
                            # PRINCIPAL for every faculty on this page.
                            if header_top is not None:
                                top_px = max(0, int(header_top) - 5)
                            else:
                                top_px = max(0, int(fline["top"]) - 10)

                            next_faculty_top = None
                            if idx + 1 < len(faculty_lines):
                                next_faculty_top = int(faculty_lines[idx + 1]["top"])

                            bottom_px = None

                            # Prefer the first PRINCIPAL line sufficiently below this FACULTY line
                            for ptop in principal_tops:
                                if ptop > top_px + min_gap_px:
                                    # Extend the crop slightly *below* the PRINCIPAL
                                    # baseline so that the PRINCIPAL text is visible.
                                    bottom_px = int(ptop) + 40
                                    break

                            # If no PRINCIPAL found, fall back to next FACULTY header
                            if bottom_px is None and next_faculty_top is not None and next_faculty_top > top_px + min_gap_px:
                                bottom_px = next_faculty_top - 8

                            if bottom_px is None:
                                bottom_px = image_full.height

                            bottom_px = max(top_px + min_gap_px, min(image_full.height, bottom_px))

                            cropped = image_full.crop((0, top_px, image_full.width, bottom_px))

                            # Save visual split for this page (page_XX_top.png, page_XX_bottom.png, ...)
                            if timetable_splits_dir:
                                try:
                                    suffix = "top" if seg_counter == 0 else ("bottom" if seg_counter == 1 else f"part_{seg_counter+1}")
                                    split_name = f"page_{page_number:02d}_{suffix}.png"
                                    cropped.save(os.path.join(timetable_splits_dir, split_name), format="PNG")
                                except Exception:
                                    pass
                            seg_counter += 1
                            entry = {
                                "page_index": page_index,
                                "faculty_name": fline["faculty_name"],
                                "image": cropped,
                                "ocr_text": ocr_text or embedded_text,
                            }
                            pages_with_name.append(entry)
                        continue
                except Exception:
                    # If multi-segment OCR fails, fall back to single-faculty mode below
                    pass

            faculty_name = _extract_faculty_name(ocr_text) or _extract_faculty_name(embedded_text)

            if not faculty_name and known_faculty_names:
                combined_text = (embedded_text or "") + "\n" + (ocr_text or "")
                faculty_name = _fallback_detect_faculty_from_page_text(combined_text, known_faculty_names)

            entry = {
                "page_index": page_index,
                "faculty_name": faculty_name,
                "image": image_full,
                "ocr_text": ocr_text or embedded_text,
            }
            if faculty_name:
                pages_with_name.append(entry)
            else:
                pages_without_name.append(entry)
            continue

        # For each detected faculty segment on this page, render and store separately.
        # First compute a unified height so all timetable crops on this page share
        # the same dimensions (uniform look).
        max_seg_height = 0
        for seg in segments:
            top = max(page_rect.y0, seg.get("top", page_rect.y0))
            bottom = min(page_rect.y1, seg.get("bottom", page_rect.y1))
            h = max(0, int(bottom - top))
            if h > max_seg_height:
                max_seg_height = h

        for seg in segments:
            faculty_name = seg.get("faculty_name")
            top = max(page_rect.y0, seg.get("top", page_rect.y0))
            bottom = min(page_rect.y1, seg.get("bottom", page_rect.y1))

            rect = fitz.Rect(page_rect.x0, top, page_rect.x1, bottom)
            pix = page.get_pixmap(dpi=200, clip=rect)
            mode = "RGBA" if pix.alpha else "RGB"
            image = Image.frombytes(mode, (pix.width, pix.height), pix.samples)

            # Pad to a unified height per page so all faculty timetable images
            # for this page have the same width and height.
            if max_seg_height and image.height < max_seg_height:
                canvas = Image.new(mode, (image.width, max_seg_height), "white")
                canvas.paste(image, (0, 0))
                image = canvas

            # Save visual split for this page (page_XX_top.png, page_XX_bottom.png, ...)
            if timetable_splits_dir:
                try:
                    suffix = "top" if seg_counter == 0 else ("bottom" if seg_counter == 1 else f"part_{seg_counter+1}")
                    split_name = f"page_{page_number:02d}_{suffix}.png"
                    image.save(os.path.join(timetable_splits_dir, split_name), format="PNG")
                except Exception:
                    pass
            seg_counter += 1

            # For per-segment entries we can reuse embedded_text; OCR is not required
            # because we already extracted the faculty name from the FACULTY line.
            entry = {
                "page_index": page_index,
                "faculty_name": faculty_name,
                "image": image,
                "ocr_text": embedded_text,
            }
            if faculty_name:
                pages_with_name.append(entry)
            else:
                pages_without_name.append(entry)

    return pages_with_name, pages_without_name

