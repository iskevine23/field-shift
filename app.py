import math
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import date

import requests
from flask import Flask, Response, jsonify, request
from werkzeug.exceptions import HTTPException

from page import HTML

app = Flask(__name__)
URL = "https://power.larc.nasa.gov/api/temporal/monthly/point"
END = date.today().year - 1  # last complete year
MIN_START = 2001             # rainfall record start; check the POWER docs

VARS = {
    "T2M": ("Air temperature (2 m)", "°C"),
    "T2M_MAX": ("Daily maximum temperature", "°C"),
    "T2M_MIN": ("Daily minimum temperature", "°C"),
    "PRECTOTCORR": ("Rainfall", "mm/day"),
    "RH2M": ("Relative humidity", "%"),
    "WS2M": ("Wind speed (2 m)", "m/s"),
    "ALLSKY_SFC_SW_DWN": ("Solar radiation (all-sky)", "kWh/m²/day"),
    "GWETROOT": ("Root-zone soil wetness", "0-1"),
}
SEASONS = {
    "annual": ("Whole year", list(range(1, 13))),
    "JF": ("Jan-Feb", [1, 2]),
    "MAM": ("Mar-May (long rains)", [3, 4, 5]),
    "JJA": ("Jun-Aug (dry season)", [6, 7, 8]),
    "SOND": ("Sep-Dec (short rains)", [9, 10, 11, 12]),
}
SITES = {
    "Musanze": (-1.4998, 29.6335), "Kigali": (-1.9441, 30.0619),
    "Kayonza": (-1.85, 30.65), "Huye": (-2.5967, 29.7394),
    "Rusizi": (-2.4846, 28.9075),
}
_cache = {}


def fetch_monthly(lat, lon, start):
    key = (lat, lon, start)
    if key not in _cache:
        params = {"parameters": ",".join(VARS), "community": "AG",
                  "latitude": lat, "longitude": lon,
                  "start": start, "end": END, "format": "JSON"}
        r = requests.get(URL, params=params, timeout=60)
        r.raise_for_status()
        _cache[key] = r.json()["properties"]["parameter"]
    return _cache[key]


def get_series(lat, lon, var, season, start):
    data = fetch_monthly(lat, lon, start)[var]
    months = SEASONS[season][1]
    xs, ys = [], []
    for yr in range(start, END + 1):
        vals = [data.get(f"{yr}{m:02d}") for m in months]
        if any(v is None or v == -999 for v in vals):
            continue
        xs.append(yr)
        ys.append(sum(vals) / len(vals))
    return xs, ys


def median(v):
    v = sorted(v)
    n = len(v)
    return v[n // 2] if n % 2 else (v[n // 2 - 1] + v[n // 2]) / 2


def analyze(xs, ys):
    """Mann-Kendall test (normal approximation, tie-corrected) and Sen's slope."""
    n = len(ys)
    if n < 10:
        raise ValueError("Fewer than 10 complete years of data for this selection.")
    s, slopes = 0, []
    for i in range(n - 1):
        for j in range(i + 1, n):
            d = ys[j] - ys[i]
            s += (d > 0) - (d < 0)
            slopes.append(d / (xs[j] - xs[i]))
    ties = Counter(ys).values()
    var = (n * (n - 1) * (2 * n + 5) - sum(t * (t - 1) * (2 * t + 5) for t in ties)) / 18
    z = 0.0 if (s == 0 or var <= 0) else (s - (1 if s > 0 else -1)) / math.sqrt(var)
    p = math.erfc(abs(z) / math.sqrt(2))
    slope = median(slopes)
    intercept = median([y - slope * x for x, y in zip(xs, ys)])
    return {"n": n, "slope_decade": slope * 10, "p": p, "z": z,
            "change_total": slope * (xs[-1] - xs[0]),
            "line": [intercept + slope * x for x in xs]}


def fdr(ps):
    """Benjamini-Hochberg adjusted p-values (q-values)."""
    m = len(ps)
    order = sorted(range(m), key=lambda i: ps[i])
    q, prev = [0.0] * m, 1.0
    for rank in range(m, 0, -1):
        i = order[rank - 1]
        prev = min(prev, ps[i] * m / rank)
        q[i] = prev
    return q


def read_season_start():
    season = request.args.get("season", "annual")
    start = int(request.args.get("start", MIN_START))
    if season not in SEASONS:
        raise ValueError("Unknown season.")
    if not (MIN_START <= start <= END - 9):
        raise ValueError(f"Start year must be between {MIN_START} and {END - 9}.")
    return season, start


def read_args():
    lat, lon = float(request.args["lat"]), float(request.args["lon"])
    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
        raise ValueError("Invalid coordinates.")
    season, start = read_season_start()
    return round(lat, 2), round(lon, 2), season, start


@app.errorhandler(Exception)
def on_error(e):
    if isinstance(e, HTTPException):
        return e
    code = 400 if isinstance(e, (ValueError, KeyError)) else 502
    return jsonify({"error": f"{type(e).__name__}: {e}"}), code


@app.route("/")
def index():
    return Response(HTML, mimetype="text/html")


@app.route("/api/meta")
def meta():
    return jsonify({
        "vars": [{"id": k, "name": v[0], "unit": v[1]} for k, v in VARS.items()],
        "seasons": [{"id": k, "name": v[0]} for k, v in SEASONS.items()],
        "end": END, "min_start": MIN_START, "max_start": END - 9,
    })


@app.route("/api/trend")
def trend():
    lat, lon, season, start = read_args()
    var = request.args["var"]
    if var not in VARS:
        raise ValueError("Unknown variable.")
    xs, ys = get_series(lat, lon, var, season, start)
    res = analyze(xs, ys)
    res.update({"var": var, "name": VARS[var][0], "unit": VARS[var][1],
                "season_label": SEASONS[season][0], "years": xs, "values": ys})
    return jsonify(res)


@app.route("/api/screen")
def screen():
    lat, lon, season, start = read_args()
    rows = []
    for var, (name, unit) in VARS.items():
        row = {"id": var, "name": name, "unit": unit}
        try:
            row.update(analyze(*get_series(lat, lon, var, season, start)))
            row.pop("line")
        except Exception as e:
            row["error"] = str(e)
        rows.append(row)
    ok = [r for r in rows if "error" not in r]
    for r, q in zip(ok, fdr([r["p"] for r in ok])):
        r["q"] = q
    return jsonify(rows)


@app.route("/api/compare")
def compare():
    season, start = read_season_start()
    var = request.args["var"]
    if var not in VARS:
        raise ValueError("Unknown variable.")

    def one(item):
        name, (lat, lon) = item
        row = {"name": name, "lat": lat, "lon": lon}
        try:
            r = analyze(*get_series(lat, lon, var, season, start))
            row.update({"slope_decade": r["slope_decade"], "p": r["p"]})
        except Exception as e:
            row["error"] = str(e)
        return row

    with ThreadPoolExecutor(max_workers=5) as ex:
        rows = list(ex.map(one, SITES.items()))
    ok = [r for r in rows if "error" not in r]
    for r, q in zip(ok, fdr([r["p"] for r in ok])):
        r["q"] = q
    return jsonify({"unit": VARS[var][1], "sites": rows})


if __name__ == "__main__":
    app.run(debug=True)