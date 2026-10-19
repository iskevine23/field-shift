import time
from datetime import date, timedelta

import pandas as pd
import requests
from flask import Flask, jsonify, render_template, request

app = Flask(__name__)

LAT, LON = -1.4998, 29.6335  # Musanze, approximate
URL = "https://power.larc.nasa.gov/api/temporal/daily/point"
CACHE_SECONDS = 12 * 3600
_cache = {"time": 0, "df": None}


def fetch_data():
    end = date.today() - timedelta(days=5)  # POWER has a short delay
    params = {
        "parameters": "PRECTOTCORR,T2M,GWETROOT",
        "community": "AG",
        "latitude": LAT,
        "longitude": LON,
        "start": "20150101",
        "end": end.strftime("%Y%m%d"),
        "format": "JSON",
    }
    r = requests.get(URL, params=params, timeout=120)
    r.raise_for_status()
    df = pd.DataFrame(r.json()["properties"]["parameter"])
    df.index = pd.to_datetime(df.index, format="%Y%m%d")
    df = df.rename(columns={
        "PRECTOTCORR": "rain_mm",
        "T2M": "temp_c",
        "GWETROOT": "soil_moisture",
    })
    return df.replace(-999, pd.NA).astype(float).dropna()


def get_data():
    if _cache["df"] is None or time.time() - _cache["time"] > CACHE_SECONDS:
        _cache["df"] = fetch_data()
        _cache["time"] = time.time()
    return _cache["df"].copy()


def add_flags(df):
    df["month"] = df.index.month
    df["rain_3d"] = df["rain_mm"].rolling(3).sum()
    df["rain_7d"] = df["rain_mm"].rolling(7).sum()
    df["soil_trend_7d"] = df["soil_moisture"].diff(7)

    g = df.groupby("month")
    dry_thr = g["rain_7d"].transform(lambda s: s.quantile(0.15))
    wet_thr = g["rain_3d"].transform(lambda s: s.quantile(0.95))
    soil_thr = g["soil_moisture"].transform(lambda s: s.quantile(0.15))
    df["soil_thr"] = soil_thr

    df["dry_risk"] = (df["rain_7d"] <= dry_thr) & (df["soil_trend_7d"] < 0)
    df["heavy_rain_risk"] = df["rain_3d"] >= wet_thr
    df["low_soil_risk"] = df["soil_moisture"] <= soil_thr
    return df.dropna()


def advice(row):
    if row["heavy_rain_risk"]:
        return "Heavy rain recently: check drainage and watch for pests and erosion."
    if row["dry_risk"] or row["low_soil_risk"]:
        return "Drier than usual for this time of year: consider delaying planting or watering."
    return "Conditions look normal for this time of year."


def get_status(as_of=None):
    df = add_flags(get_data())
    if as_of:
        df = df.loc[:as_of]
        if df.empty:
            raise ValueError("No data on or before that date (data starts in 2015).")
    last = df.iloc[-1]
    recent = []
    for day, row in df.tail(14).iloc[::-1].iterrows():
        flags = [name for name, key in [
            ("Dry", "dry_risk"),
            ("Heavy rain", "heavy_rain_risk"),
            ("Low soil moisture", "low_soil_risk"),
        ] if row[key]]
        recent.append({
            "date": day.strftime("%Y-%m-%d"),
            "rain": round(row["rain_mm"], 1),
            "soil": round(row["soil_moisture"], 2),
            "flags": ", ".join(flags) or "-",
        })

    tail = df.tail(90)
    chart = {
        "labels": [d.strftime("%Y-%m-%d") for d in tail.index],
        "rain": [round(v, 1) for v in tail["rain_mm"]],
        "soil": [round(v, 2) for v in tail["soil_moisture"]],
        "soil_thr": [round(v, 2) for v in tail["soil_thr"]],
    }

    return {
        "date": df.index[-1].strftime("%d %B %Y"),
        "rain_3d": round(last["rain_3d"], 1),
        "rain_7d": round(last["rain_7d"], 1),
        "soil": round(last["soil_moisture"], 2),
        "dry_risk": bool(last["dry_risk"]),
        "heavy_rain_risk": bool(last["heavy_rain_risk"]),
        "low_soil_risk": bool(last["low_soil_risk"]),
        "advice": advice(last),
        "recent": recent,
        "chart": chart,
    }


@app.route("/")
def index():
    try:
        return render_template("index.html", s=get_status(request.args.get("date")), error=None)
    except Exception as e:
        return render_template("index.html", s=None, error=str(e)), 502


@app.route("/api/status")
def api_status():
    try:
        return jsonify(get_status(request.args.get("date")))
    except Exception as e:
        return jsonify({"error": str(e)}), 502


if __name__ == "__main__":
    app.run(debug=True)