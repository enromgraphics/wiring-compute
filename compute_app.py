from flask import Flask, request, jsonify

app = Flask(__name__)

@app.get("/")
def health():
    return "OK"

@app.post("/run")
def run():
    data = request.get_json(force=True)
    # Echo back so we can test end-to-end from your cPanel form
    return jsonify({
        "ok": True,
        "echo": {
            "text": data.get("text"),
            "height_mm": data.get("height_mm"),
            "font_filename": data.get("font_filename"),
            "font_b64_len": len(data.get("font_b64",""))
        }
    })
