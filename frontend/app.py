from flask import Flask, request, render_template_string, Response, abort, stream_with_context
import os, requests, base64

app = Flask(__name__)

API_URL = os.getenv("API_URL", "http://102.210.146.241/run")
BASE_API = API_URL.rsplit("/", 1)[0]  # e.g. http://102.210.146.241

DEFAULT_CURRENCY = os.getenv("CURRENCY", "R")
DEFAULT_LED_COST_PER_METER = float(os.getenv("LED_COST_PER_METER", "120"))
DEFAULT_FAB_COST_PER_MM    = float(os.getenv("FAB_COST_PER_MM", "0.15"))
DEFAULT_SETUP_COST         = float(os.getenv("SETUP_COST", "250"))

HELP_STYLE = "display:block;color:#666;margin:6px 0 0 0;font-size:0.9rem;line-height:1.3;"

# ---------- HTTPS proxy so downloads aren’t mixed-content-blocked ----------
@app.route("/proxy/<path:rest>")
def proxy_media(rest):
    # Only allow media/ paths (avoid open proxy)
    if not rest.startswith("media/"):
        abort(404)

    upstream = BASE_API.rstrip("/") + "/" + rest
    # pass along query string (so ?dl=1 reaches VPS)
    if request.query_string:
        upstream += "?" + request.query_string.decode()

    try:
        r = requests.get(upstream, stream=True, timeout=300)
    except requests.RequestException as e:
        return Response(f"Upstream error: {e}", status=502)

    headers = {}
    # pass through content type/length
    for h in ("Content-Type", "Content-Length"):
        v = r.headers.get(h)
        if v:
            headers[h] = v

    # Force download if our URL has ?dl=1 (even if VPS forgot)
    filename = rest.rsplit("/", 1)[-1]
    if request.args.get("dl") == "1":
        headers["Content-Disposition"] = f'attachment; filename="{filename}"'
    else:
        # Otherwise respect upstream (lets SVG preview)
        cd = r.headers.get("Content-Disposition")
        if cd:
            headers["Content-Disposition"] = cd

    return Response(stream_with_context(r.iter_content(8192)),
                    status=r.status_code, headers=headers)

def _fmt(x):  # 2dp
    return f"{x:.2f}"

def _parse_decimal(s, default):
    if s is None:
        return float(default)
    s = str(s).strip().replace(" ", "").replace(",", ".")
    try:
        return float(s)
    except ValueError:
        return float(default)

def _proxy(url: str, add_dl: bool = False) -> str:
    """
    Convert API-relative (/media/...) into same-origin HTTPS link via /proxy.
    If add_dl=True, append ?dl=1 to force download.
    """
    if not url:
        return url
    u = str(url)
    # Expecting relative /media/... from the API. If absolute, try to trim BASE_API.
    if u.startswith("http"):
        # Strip BASE_API to get the /media/... piece if possible
        if BASE_API in u:
            u = u.split(BASE_API, 1)[-1]
        # If still absolute and not our BASE_API, just return it (might still work).
    if not u.startswith("/"):
        u = "/" + u
    proxied = request.host_url.rstrip("/") + "/proxy" + u
    if add_dl:
        proxied += ("&" if "?" in proxied else "?") + "dl=1"
    return proxied

FORM_HTML = """
<!doctype html>
<title>Wiring Centreline + Costing</title>
<style>
  html { scroll-behavior: smooth; }
  .notice {
    display:none;margin:.75rem 0;padding:.6rem .8rem;
    background:#fffbe6;border:1px solid #ffe58f;border-radius:8px;color:#663c00;
  }
</style>

<h2>Compute centreline length & cost</h2>

<form id="calcForm" method="post" enctype="multipart/form-data" style="max-width:760px">
  <fieldset style="border:1px solid #ccc; padding:12px; margin-bottom:12px">
    <legend>Input</legend>

    <p>
      <label>Text:
        <input name="text" required value="{{ text or '' }}" placeholder="Enter sign text" style="width:320px">
      </label>
      <small style="{{ help_style }}">The word or phrase you’re quoting for (e.g., the client’s sign text).</small>
    </p>

    <p>
      <label>Letter height (mm):
        <input name="height_mm" type="text" inputmode="decimal"
               value="{{ '' if height is none else height }}" placeholder="e.g. 120" style="width:140px">
      </label>
      <small style="{{ help_style }}">The target uppercase letter height in millimetres. Decimals allowed (e.g. 120 or 120,5).</small>
    </p>

    <p>
      <label>Font (TTF/OTF):
        <input name="font" type="file" accept=".ttf,.otf" required>
      </label>
      {% if fname %}<span style="margin-left:8px;color:#555"><em>Font uploaded: {{ fname }}</em></span>{% endif %}
      <small style="{{ help_style }}">Upload the exact font file to measure accurate widths. Supported: .ttf / .otf.</small>
    </p>
  </fieldset>

  <fieldset style="border:1px solid #ccc; padding:12px; margin-bottom:12px">
    <legend>Costing</legend>

    <p>
      <label>Currency:
        <input name="currency" value="{{ currency or default_currency }}" style="width:100px">
      </label>
      <small style="{{ help_style }}">Currency symbol or code shown in cost totals (e.g. R, ZAR, $, £).</small>
    </p>

    <p>
      <label>LED cost per meter:
        <input name="led_cost_pm" type="text" inputmode="decimal"
               value="{{ led_cost_pm if led_cost_pm is not none else default_led }}" style="width:140px">
      </label>
      <small style="{{ help_style }}">Your price for LED strip per metre. Used to cost the total centreline length.</small>
    </p>

    <p>
      <label>Fabrication cost per mm:
        <input name="fab_cost_mm" type="text" inputmode="decimal"
               value="{{ fab_cost_mm if fab_cost_mm is not none else default_fab }}" style="width:140px">
      </label>
      <small style="{{ help_style }}">Per-millimetre fabrication rate (routing, wiring, etc.). Multiplied by centreline length.</small>
    </p>

    <p>
      <label>Setup cost (flat):
        <input name="setup_cost" type="text" inputmode="decimal"
               value="{{ setup_cost if setup_cost is not none else default_setup }}" style="width:140px">
      </label>
      <small style="{{ help_style }}">Once-off job charge (design, setup, travel, or minimum labour).</small>
    </p>
  </fieldset>

  <fieldset style="border:1px solid #ccc; padding:12px; margin-bottom:12px">
    <legend>QA & Export (Optional)</legend>
    <p>
      <label>
        <input type="checkbox" name="export_svg" {% if export_svg %}checked{% endif %}>
        Export SVG skeletons (per letter)
      </label>
      <small style="{{ help_style }}">Saves one SVG per letter with the black outline and red skeleton dots.</small>
    </p>
    <p>
      <label>
        <input type="checkbox" name="export_word_svg" {% if export_word_svg %}checked{% endif %}>
        Export combined word SVG
      </label>
      <small style="{{ help_style }}">One SVG of the whole word showing outline + skeleton (helpful for routing/LED plan).</small>
    </p>
    <p>
      <label>
        <input type="checkbox" name="export_report" {% if export_report %}checked{% endif %}>
        Export CSV/Excel report
      </label>
      <small style="{{ help_style }}">Generates CSV/XLSX with per-letter lengths and (if costs are filled) totals & job total.</small>
    </p>
  </fieldset>

  <button id="submitBtn" type="submit">Calculate</button>
</form>

<div id="progress" class="notice">Calculating…</div>

<p style="margin-top:1rem;color:#555;">API URL: {{ api_url }}</p>

<!-- anchor to auto-scroll to -->
<div id="results"></div>

{% if result %}
  <hr>
  {% if result.ok %}
    <h3>Results</h3>
    <p><strong>Total centreline (metrics):</strong> {{ result.total_length_mm_str }} mm</p>
    {% if result.word_visual_length_mm_str %}
      <p><strong>Whole word visual (skeleton):</strong> {{ result.word_visual_length_mm_str }} mm</p>
    {% endif %}

    <h4>Per-letter breakdown</h4>
    <table border="1" cellpadding="6" cellspacing="0">
      <thead>
        <tr>
          <th>Letter</th>
          <th>Length (mm)</th>
          <th>LED ({{ currency }})</th>
          <th>Fabrication ({{ currency }})</th>
          <th>Total ({{ currency }})</th>
        </tr>
      </thead>
      <tbody>
      {% for row in result.rows %}
        <tr>
          <td style="text-align:center">{{ row.letter }}</td>
          <td style="text-align:right">{{ row.length_mm_str }}</td>
          <td style="text-align:right">{{ row.led_cost_str }}</td>
          <td style="text-align:right">{{ row.fab_cost_str }}</td>
          <td style="text-align:right"><strong>{{ row.total_cost_str }}</strong></td>
        </tr>
      {% endfor %}
      </tbody>
      <tfoot>
        <tr>
          <th colspan="2" style="text-align:right">Totals:</th>
          <th style="text-align:right">{{ result.tot_led_str }}</th>
          <th style="text-align:right">{{ result.tot_fab_str }}</th>
          <th style="text-align:right">{{ result.tot_word_str }}</th>
        </tr>
        <tr>
          <th colspan="4" style="text-align:right">Setup/Labour:</th>
          <th style="text-align:right">{{ result.setup_str }}</th>
        </tr>
        <tr>
          <th colspan="4" style="text-align:right">Job Total:</th>
          <th style="text-align:right">{{ result.job_total_str }}</th>
        </tr>
      </tfoot>
    </table>

    {% if result.svg_urls %}
      <h4>SVG skeletons (per letter)</h4>
      <ul>
        {% for it in result.svg_urls %}
          <li>{{ it.letter }}:
            <a href="{{ it.url_preview }}" target="_blank" rel="noopener">preview</a> |
            <a href="{{ it.url_download }}" download>download</a>
          </li>
        {% endfor %}
      </ul>
    {% endif %}

    {% if result.word_svg_preview %}
      <h4>Combined word SVG</h4>
      <p>
        <a href="{{ result.word_svg_preview }}" target="_blank" rel="noopener">preview</a> |
        <a href="{{ result.word_svg_download }}" download>download</a>
      </p>
    {% endif %}

    {% if result.report_urls %}
      <h4>Downloads</h4>
      <ul>
        {% if result.report_urls.csv %}<li><a href="{{ result.report_urls.csv }}" download>CSV report</a></li>{% endif %}
        {% if result.report_urls.xlsx %}<li><a href="{{ result.report_urls.xlsx }}" download>Excel report</a></li>{% endif %}
      </ul>
    {% endif %}

    <p style="margin-top:0.8rem;color:#666">
      (Using LED {{ currency }}{{ led_cost_pm_disp }}/m, fabrication {{ currency }}{{ fab_cost_mm_disp }}/mm, setup {{ currency }}{{ setup_cost_disp }}.)
    </p>
  {% else %}
    <pre>{{ result.raw | tojson(indent=2) }}</pre>
  {% endif %}
{% endif %}

<script>
(function () {
  const form = document.getElementById('calcForm');
  const btn  = document.getElementById('submitBtn');
  const prog = document.getElementById('progress');

  if (form) {
    form.addEventListener('submit', function () {
      if (prog) { prog.style.display = 'block'; prog.textContent = 'Calculating…'; prog.scrollIntoView({block:'start'}); }
      if (btn)  { btn.disabled = true; btn.dataset.label = btn.textContent; btn.textContent = 'Calculating…'; }
    });
  }
})();
</script>

{% if result %}
<script>
(function () {
  const anchor = document.getElementById('results');
  if (anchor) { anchor.scrollIntoView({block:'start'}); }
  const btn  = document.getElementById('submitBtn');
  const prog = document.getElementById('progress');
  if (btn && btn.dataset.label) { btn.disabled = false; btn.textContent = btn.dataset.label; }
  if (prog) { prog.style.display = 'none'; }
})();
</script>
{% endif %}
"""


@app.route("/", methods=["GET", "POST"])
def index():
    result = None
    fname = None

    text = (request.form.get("text") or "").strip()
    height_raw = request.form.get("height_mm")
    if height_raw is not None:
        height_raw = height_raw.replace(",", ".")
    try:
        height = float(height_raw) if height_raw is not None else None
    except ValueError:
        height = None

    currency    = (request.form.get("currency") or DEFAULT_CURRENCY).strip()
    led_cost_pm = _parse_decimal(request.form.get("led_cost_pm"), DEFAULT_LED_COST_PER_METER)
    fab_cost_mm = _parse_decimal(request.form.get("fab_cost_mm"),  DEFAULT_FAB_COST_PER_MM)
    setup_cost  = _parse_decimal(request.form.get("setup_cost"),   DEFAULT_SETUP_COST)

    export_svg       = bool(request.form.get("export_svg"))
    export_word_svg  = bool(request.form.get("export_word_svg"))
    export_report    = bool(request.form.get("export_report"))

    if request.method == "POST":
        f = request.files.get("font")
        if f and text:
            fname = getattr(f, "filename", None)
            payload = {
                "text": text,
                "height_mm": float(height if height is not None else 100.0),
                "font_b64": base64.b64encode(f.read()).decode("ascii"),
                "export_svg": export_svg,
                "export_word_svg": export_word_svg,
                "export_report": export_report,
                "report": {
                    "currency": currency,
                    "led_cost_per_meter": led_cost_pm,
                    "fab_cost_per_mm": fab_cost_mm,
                    "setup_cost": setup_cost,
                },
            }
            try:
                r = requests.post(API_URL, json=payload, timeout=180)
                try:
                    data = r.json()
                except ValueError:
                    data = {"ok": False, "status": r.status_code, "text": r.text[:500]}
            except requests.RequestException as e:
                data = {"ok": False, "error": str(e)}

            if isinstance(data, dict) and data.get("ok"):
                per_letter_map = data.get("per_letter", {}) or {}
                total_length_mm = float(data.get("total_length_mm") or 0.0)

                rows, tot_led, tot_fab = [], 0.0, 0.0
                for ch in text:
                    length_mm = float(per_letter_map.get(ch, 0.0))
                    led_cost = (length_mm / 1000.0) * led_cost_pm
                    fab_cost = length_mm * fab_cost_mm
                    rows.append({
                        "letter": ch if ch != " " else "␣",
                        "length_mm_str": _fmt(length_mm),
                        "led_cost_str": _fmt(led_cost),
                        "fab_cost_str": _fmt(fab_cost),
                        "total_cost_str": _fmt(led_cost + fab_cost),
                    })
                    tot_led += led_cost; tot_fab += fab_cost

                tot_word  = tot_led + tot_fab
                job_total = tot_word + setup_cost

                # Build HTTPS same-origin links via proxy
                svg_urls = data.get("svg_urls") or []
                svg_urls_abs = [{
                    "letter": it.get("letter"),
                    "url_preview": _proxy(it.get("url"), add_dl=False),
                    "url_download": _proxy(it.get("url"), add_dl=True),
                } for it in svg_urls]

                word_svg_preview = _proxy(data.get("word_svg_url"), add_dl=False) if data.get("word_svg_url") else None
                word_svg_download = _proxy(data.get("word_svg_url"), add_dl=True) if data.get("word_svg_url") else None

                report_urls_abs = None
                if data.get("report_urls"):
                    # Always force download for reports
                    report_urls_abs = {k: _proxy(v, add_dl=True) for k, v in data["report_urls"].items() if v}

                result = {
                    "ok": True,
                    "rows": rows,
                    "total_length_mm_str": _fmt(total_length_mm),
                    "tot_led_str": _fmt(tot_led),
                    "tot_fab_str": _fmt(tot_fab),
                    "tot_word_str": _fmt(tot_word),
                    "setup_str": _fmt(setup_cost),
                    "job_total_str": _fmt(job_total),
                    "svg_urls": svg_urls_abs,
                    "word_svg_preview": word_svg_preview,
                    "word_svg_download": word_svg_download,
                    "report_urls": report_urls_abs,
                }
                if data.get("word_visual_length_mm") is not None:
                    result["word_visual_length_mm_str"] = _fmt(float(data["word_visual_length_mm"]))
            else:
                result = {"ok": False, "raw": data}
        else:
            result = {"ok": False, "raw": {"error": "Please provide text and a font file."}}

    return render_template_string(
        FORM_HTML,
        result=result,
        text=text or None,
        height=height,
        fname=fname,
        api_url=API_URL,
        help_style=HELP_STYLE,
        currency=currency or DEFAULT_CURRENCY,
        led_cost_pm=led_cost_pm,
        fab_cost_mm=fab_cost_mm,
        setup_cost=setup_cost,
        export_svg=export_svg,
        export_word_svg=export_word_svg,
        export_report=export_report,
        default_currency=DEFAULT_CURRENCY,
        default_led=_fmt(DEFAULT_LED_COST_PER_METER),
        default_fab=_fmt(DEFAULT_FAB_COST_PER_MM),
        default_setup=_fmt(DEFAULT_SETUP_COST),
        led_cost_pm_disp=_fmt(led_cost_pm),
        fab_cost_mm_disp=_fmt(fab_cost_mm),
        setup_cost_disp=_fmt(setup_cost),
    )
