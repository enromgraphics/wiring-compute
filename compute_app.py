from flask import Flask, request, jsonify
import base64, os, tempfile
from centerline_core import compute_lengths

app = Flask(__name__)

@app.get("/")
def health():
    return "OK"

@app.post("/run")
def run():
    data = request.get_json(force=True)
    text = (data.get("text") or "").strip()
    height_mm = float(data.get("height_mm") or 100)
    font_b64 = data.get("font_b64") or ""
    if not text or not font_b64:
        return jsonify({"ok": False, "error": "Missing text or font"}), 400

    try:
        font_bytes = base64.b64decode(font_b64)
        per_letter, total_len = compute_lengths(font_bytes, text, height_mm)
        return jsonify({"ok": True, "per_letter": per_letter, "total_length_mm": total_len})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500
