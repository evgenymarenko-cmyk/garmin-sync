import os, json, logging, requests
from datetime import date, timedelta
from flask import Flask, jsonify, request
from flask_cors import CORS

logging.basicConfig(level=logging.INFO)
app = Flask(__name__)
CORS(app)

API_SECRET = os.environ.get("API_SECRET", "healthlog123")
JWT_WEB = os.environ.get("JWT_WEB")
SESSION_ID = os.environ.get("SESSION_ID")

BASE = "https://connect.garmin.com"

def garmin_headers():
    return {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "NK": "NT",
        "X-app-ver": "4.65.1.0",
        "Accept": "application/json",
        "Cookie": f"JWT_WEB={JWT_WEB}; SESSIONID={SESSION_ID}",
    }

def require_secret(f):
    from functools import wraps
    @wraps(f)
    def decorated(*args, **kwargs):
        secret = request.headers.get("X-API-Secret") or request.args.get("secret")
        if secret != API_SECRET:
            return jsonify({"error": "Unauthorized"}), 401
        return f(*args, **kwargs)
    return decorated

@app.route("/")
def index():
    return jsonify({"status": "Garmin sync server running"})

@app.route("/health")
def health():
    return jsonify({"status": "ok"})

def garmin_get(path, params=None):
    url = BASE + path
    r = requests.get(url, headers=garmin_headers(), params=params, timeout=15)
    if r.status_code == 401:
        raise Exception("session_expired")
    if not r.ok:
        raise Exception(f"HTTP {r.status_code}: {r.text[:200]}")
    return r.json()

@app.route("/sync")
@require_secret
def sync():
    date_str = request.args.get("date", str(date.today()))
    try:
        date.fromisoformat(date_str)
    except ValueError:
        return jsonify({"error": "Invalid date, use YYYY-MM-DD"}), 400

    result = {"date": date_str}

    # Daily summary - steps, calories etc
    try:
        data = garmin_get(f"/proxy/usersummary-service/usersummary/daily/{date_str}", {"_": date_str})
        result["steps"] = data.get("totalSteps")
        result["calories_burned"] = data.get("totalKilocalories")
        result["active_calories"] = data.get("activeKilocalories")
        result["floors"] = data.get("floorsAscended")
        result["distance_km"] = round(data.get("totalDistanceMeters", 0) / 1000, 2)
        result["active_minutes"] = data.get("highlyActiveSeconds", 0) // 60
        result["stress_avg"] = data.get("averageStressLevel")
        result["body_battery_high"] = data.get("maxBodyBattery")
        result["body_battery_low"] = data.get("minBodyBattery")
    except Exception as e:
        logging.warning(f"Daily summary error: {e}")
        result["steps"] = None

    # Sleep
    try:
        data = garmin_get(f"/proxy/wellness-service/wellness/dailySleepData/{date_str}", {"date": date_str, "nonSleepBufferMinutes": 60})
        if data and "dailySleepDTO" in data:
            dto = data["dailySleepDTO"]
            secs = dto.get("sleepTimeSeconds", 0)
            result["sleep_hours"] = round(secs / 3600, 1) if secs else None
            result["sleep_deep_hours"] = round(dto.get("deepSleepSeconds", 0) / 3600, 1)
            result["sleep_rem_hours"] = round(dto.get("remSleepSeconds", 0) / 3600, 1)
            result["sleep_light_hours"] = round(dto.get("lightSleepSeconds", 0) / 3600, 1)
            score = dto.get("sleepScores", {})
            overall = score.get("overall", {}).get("value") if isinstance(score, dict) else None
            result["sleep_score"] = overall
            if overall is not None:
                if overall >= 80: result["sleep_quality"] = "🌟 Great"
                elif overall >= 60: result["sleep_quality"] = "😊 Good"
                elif overall >= 40: result["sleep_quality"] = "😐 OK"
                else: result["sleep_quality"] = "😴 Poor"
        else:
            result["sleep_hours"] = None
    except Exception as e:
        logging.warning(f"Sleep error: {e}")
        result["sleep_hours"] = None

    # Heart rate
    try:
        data = garmin_get(f"/proxy/wellness-service/wellness/dailyHeartRate/{date_str}", {"date": date_str})
        result["heart_rate_resting"] = data.get("restingHeartRate")
        result["heart_rate_max"] = data.get("maxHeartRate")
        result["heart_rate_min"] = data.get("minHeartRate")
    except Exception as e:
        logging.warning(f"HR error: {e}")
        result["heart_rate_resting"] = None

    # HRV
    try:
        data = garmin_get(f"/proxy/hrv-service/hrv/{date_str}")
        if data and "hrvSummary" in data:
            result["hrv"] = data["hrvSummary"].get("weeklyAvg")
            result["hrv_last_night"] = data["hrvSummary"].get("lastNight")
        else:
            result["hrv"] = None
    except Exception as e:
        logging.warning(f"HRV error: {e}")
        result["hrv"] = None

    # Activities
    try:
        data = garmin_get("/proxy/activitylist-service/activities/search/activities", {
            "startDate": date_str, "endDate": date_str, "limit": 10, "start": 0
        })
        result["activities"] = []
        for act in (data or []):
            result["activities"].append({
                "name": act.get("activityName", "Activity"),
                "type": act.get("activityType", {}).get("typeKey", "unknown"),
                "duration_min": round(act.get("duration", 0) / 60),
                "calories": act.get("calories", 0),
                "distance_km": round(act.get("distance", 0) / 1000, 2) if act.get("distance") else None,
                "avg_hr": act.get("averageHR"),
                "max_hr": act.get("maxHR"),
                "steps": act.get("steps"),
            })
    except Exception as e:
        logging.warning(f"Activities error: {e}")
        result["activities"] = []

    return jsonify(result)

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
