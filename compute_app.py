from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS
import base64, os, uuid, tempfile
from io import BytesIO
from fontTools.ttLib import TTFont

# QA + exports (no Shapely)
from PIL import Image, ImageDraw, ImageFont
import numpy as np
from skimage.morphology import skeletonize
from skimage.measure import find_contours
import svgwrite
import pandas as pd

app = Flask(__name__)
_ALLOWED_ORIGINS = [
    "https://staging2.enromgraphics.co.za",
    "http://staging2.enromgraphics.co.za",
]
CORS(app, resources={
    r"/run":     {"origins": _ALLOWED_ORIGINS},
    r"/media/*": {"origins": _ALLOWED_ORIGINS},
})

@app.get("/")
def health():
    return "OK"

# ---------- stable lengths (fallback-first) ----------
try:
    from centerline_core import compute_lengths as center_compute_lengths
except Exception:
    center_compute_lengths = None

def compute_lengths_fallback(font_bytes: bytes, text: str, height_mm: float):
    font = TTFont(BytesIO(font_bytes))
    try:
        units_per_em = float(font["head"].unitsPerEm)
        cmap = font.getBestCmap() or {}
        hmtx = font["hmtx"].metrics
        scale = float(height_mm) / units_per_em
        per_letter, total_units = {}, 0
        for ch in text:
            glyph = cmap.get(ord(ch), ".notdef")
            adv = hmtx.get(glyph, (units_per_em, 0))[0]
            per_letter[ch] = float(adv * scale)
            total_units += adv
        return per_letter, float(total_units * scale)
    finally:
        try: font.close()
        except Exception: pass

USE_SIMPLE_METRICS = os.getenv("USE_SIMPLE_METRICS", "1") == "1"

def compute_lengths_dispatch(font_bytes: bytes, text: str, height_mm: float):
    if not USE_SIMPLE_METRICS and center_compute_lengths is not None:
        try:
            return center_compute_lengths(font_bytes, text, height_mm)
        except Exception:
            pass
    return compute_lengths_fallback(font_bytes, text, height_mm)

# ---------- SVG QA (outline + skeleton) ----------
EXPORT_DIR = os.getenv("EXPORT_DIR", "/home/vpsenrom/wiring/exports")
os.makedirs(EXPORT_DIR, exist_ok=True)
PX_PER_MM = float(os.getenv("PX_PER_MM", "10"))

def _safe_piece(s: str) -> str:
    s = "".join(ch if ch.isalnum() or ch in "-_." else "_" for ch in str(s))
    return s.strip("_") or "file"

@app.get("/media/<job_id>/<path:filename>")
def media(job_id, filename):
    from flask import request  # ensure imported at top if not already
    job_id = _safe_piece(job_id)
    filename = _safe_piece(filename)
    job_path = os.path.join(EXPORT_DIR, job_id)

    ext = os.path.splitext(filename)[1].lower()
    force_dl = request.args.get("dl") == "1"
    # CSV/XLS(X) download by default; SVG downloads only when ?dl=1
    as_attachment = force_dl or (ext in {".csv", ".xlsx", ".xls"})

    return send_from_directory(
        job_path,
        filename,
        as_attachment=as_attachment,
        download_name=filename
    )


def _render_text_mask(font_bytes: bytes, text: str, height_mm: float):
    height_px = max(64, int(height_mm * PX_PER_MM))
    with tempfile.NamedTemporaryFile(delete=False, suffix=".ttf") as tf:
        tf.write(font_bytes); tmp_path = tf.name
    try:
        font = ImageFont.truetype(tmp_path, size=height_px)
        dummy = Image.new("L", (1, 1), 0)
        d0 = ImageDraw.Draw(dummy)
        x0, y0, x1, y1 = d0.textbbox((0, 0), text, font=font)
        w, h = max(1, x1 - x0), max(1, y1 - y0)
        pad = int(max(6, height_px * 0.06))
        W, H = w + 2 * pad, h + 2 * pad
        img = Image.new("L", (W, H), 0)
        ImageDraw.Draw(img).text((pad - x0, pad - y0), text, font=font, fill=255)
        arr = (np.array(img) > 0)
        mm_per_px = height_mm / float(height_px)
        return arr, (W, H), mm_per_px
    finally:
        try: os.remove(tmp_path)
        except Exception: pass

def _length_from_skeleton_bool(A: np.ndarray, mm_per_px: float) -> float:
    e_down  = (A & np.roll(A, -1, axis=0)).sum()
    e_right = (A & np.roll(A, -1, axis=1)).sum()
    e_dr    = (A & np.roll(np.roll(A, -1, axis=0), -1, axis=1)).sum()
    e_dl    = (A & np.roll(np.roll(A, -1, axis=0),  1, axis=1)).sum()
    length_px = e_down + e_right + (np.sqrt(2.0) * (e_dr + e_dl))
    return float(length_px * mm_per_px)

def _draw_outline_paths(dwg, mask: np.ndarray):
    for c in find_contours(mask.astype(float), 0.5):
        if len(c) < 2: continue
        pts = [(float(x), float(y)) for y, x in c]
        path = "M " + " L ".join(f"{x:.2f},{y:.2f}" for x, y in pts)
        dwg.add(dwg.path(d=path, fill="none", stroke="black", stroke_width=1))

def _draw_skeleton_points(dwg, skel: np.ndarray):
    ys, xs = np.where(skel)
    for x, y in zip(xs, ys):
        dwg.add(dwg.circle(center=(float(x), float(y)), r=0.35, fill="red"))

def export_letter_svgs(font_bytes: bytes, text: str, height_mm: float, job_dir: str):
    files, lengths_mm = [], []
    for i, ch in enumerate(text, start=1):
        if ch == " ":
            lengths_mm.append(0.0); continue
        mask, (W, H), mm_per_px = _render_text_mask(font_bytes, ch, height_mm)
        skel = skeletonize(mask)
        Lmm = _length_from_skeleton_bool(skel, mm_per_px)
        svg_name = f"{i:02d}_{_safe_piece(ch)}.svg"
        svg_path = os.path.join(job_dir, svg_name)
        dwg = svgwrite.Drawing(svg_path, size=(f"{W}px", f"{H}px"), viewBox=f"0 0 {W} {H}")
        _draw_outline_paths(dwg, mask)
        _draw_skeleton_points(dwg, skel)
        dwg.save()
        files.append((ch, svg_name)); lengths_mm.append(Lmm)
    return files, lengths_mm

def export_word_svg(font_bytes: bytes, text: str, height_mm: float, job_dir: str):
    mask, (W, H), mm_per_px = _render_text_mask(font_bytes, text, height_mm)
    skel = skeletonize(mask)
    Lmm = _length_from_skeleton_bool(skel, mm_per_px)
    svg_name = f"{_safe_piece(text)}_word.svg"
    svg_path = os.path.join(job_dir, svg_name)
    dwg = svgwrite.Drawing(svg_path, size=(f"{W}px", f"{H}px"), viewBox=f"0 0 {W} {H}")
    _draw_outline_paths(dwg, mask)
    _draw_skeleton_points(dwg, skel)
    dwg.save()
    return svg_name, Lmm

@app.post("/run")
def run():
    data = request.get_json(force=True) or {}
    text = (data.get("text") or "").strip()
    font_b64 = data.get("font_b64") or ""
    export_svg       = bool(data.get("export_svg") or False)
    export_word_svg_ = bool(data.get("export_word_svg") or False)
    export_report    = bool(data.get("export_report") or False)

    report_cfg = data.get("report") or {}
    currency = report_cfg.get("currency")
    try:
        led_pm = float(report_cfg.get("led_cost_per_meter")) if report_cfg.get("led_cost_per_meter") not in (None, "") else None
        fab_mm = float(report_cfg.get("fab_cost_per_mm"))    if report_cfg.get("fab_cost_per_mm")    not in (None, "") else None
        setup  = float(report_cfg.get("setup_cost"))         if report_cfg.get("setup_cost")         not in (None, "") else None
    except Exception:
        led_pm = fab_mm = setup = None

    try:
        height_mm = float(data.get("height_mm") or 100)
    except (TypeError, ValueError):
        height_mm = 100.0

    if not text or not font_b64:
        return jsonify({"ok": False, "error": "Missing text or font"}), 400

    try:
        font_bytes = base64.b64decode(font_b64)
    except Exception as e:
        return jsonify({"ok": False, "error": f"Invalid font_b64: {e}"}), 400

    try:
        per_letter, total_len = compute_lengths_dispatch(font_bytes, text, height_mm)
        per_letter = {k: round(float(v), 2) for k, v in per_letter.items()}
        total_len = round(float(total_len), 2)
        resp = {"ok": True, "per_letter": per_letter, "total_length_mm": total_len}

        need_job = export_svg or export_word_svg_ or export_report
        if need_job:
            job_id = uuid.uuid4().hex[:12]
            job_dir = os.path.join(EXPORT_DIR, job_id)
            os.makedirs(job_dir, exist_ok=True)

            if export_svg:
                files, letter_visual_lengths = export_letter_svgs(font_bytes, text, height_mm, job_dir)
                resp["job_id"] = job_id
                resp["svg_urls"] = [{"letter": ch, "url": f"/media/{job_id}/{name}"} for ch, name in files]
                resp["visual_per_letter"] = [round(v, 2) for v in letter_visual_lengths]

            if export_word_svg_:
                word_name, word_visual_mm = export_word_svg(font_bytes, text, height_mm, job_dir)
                resp["job_id"] = job_id
                resp["word_svg_url"] = f"/media/{job_id}/{word_name}"
                resp["word_visual_length_mm"] = round(float(word_visual_mm), 2)

            if export_report:
                letters = []
                lengths = []
                for ch in text:
                    letters.append("␣" if ch == " " else ch)
                    lengths.append(float(per_letter.get(ch, 0.0)))

                df_cols = {"Letter": letters, "Length (mm)": lengths}
                total_word_len = float(sum(lengths))

                if (led_pm is not None) and (fab_mm is not None):
                    led_costs = [(L / 1000.0) * led_pm for L in lengths]
                    fab_costs = [L * fab_mm for L in lengths]
                    tot_costs = [lc + fc for lc, fc in zip(led_costs, fab_costs)]
                    df_cols[f"LED Cost ({currency or ''})"] = led_costs
                    df_cols[f"Total Cost ({currency or ''})"] = tot_costs
                    total_led = sum(led_costs)
                    total_fab = sum(fab_costs)
                    total_word_cost = total_led + total_fab
                    job_total = total_word_cost + (setup or 0.0)
                else:
                    total_led = total_fab = total_word_cost = job_total = None

                df = pd.DataFrame(df_cols)
                tw_row = {"Letter": "TOTAL WORD", "Length (mm)": total_word_len}
                if export_word_svg_:
                    tw_row["Word SVG"] = f"/media/{job_id}/{_safe_piece(text)}_word.svg"
                if total_led is not None:
                    tw_row[f"LED Cost ({currency or ''})"] = total_led
                    tw_row[f"Total Cost ({currency or ''})"] = total_word_cost
                df.loc[len(df.index)] = tw_row
                if job_total is not None:
                    jt_row = {"Letter": "JOB TOTAL", f"Total Cost ({currency or ''})": job_total}
                    df.loc[len(df.index)] = jt_row

                csv_name  = f"{_safe_piece(text)}_lengths.csv"
                xlsx_name = f"{_safe_piece(text)}_lengths.xlsx"
                csv_path  = os.path.join(job_dir, csv_name)
                xlsx_path = os.path.join(job_dir, xlsx_name)
                df.to_csv(csv_path, index=False)
                try: df.to_excel(xlsx_path, index=False)
                except Exception: xlsx_name = None

                urls = {"csv": f"/media/{job_id}/{csv_name}"}
                if xlsx_name:
                    urls["xlsx"] = f"/media/{job_id}/{xlsx_name}"
                resp["job_id"] = job_id
                resp["report_urls"] = urls

        return jsonify(resp)
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500
