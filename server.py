import os, json, logging
from datetime import date, timedelta
from flask import Flask, jsonify, request
from flask_cors import CORS

logging.basicConfig(level=logging.INFO)
app = Flask(__name__)
CORS(app)

API_SECRET = os.environ.get("API_SECRET", "healthlog123")
EMAIL = os.environ.get("GARMIN_EMAIL")
PASSWORD = os.environ.get("GARMIN_PASSWORD")
TOKEN_STORE = "/tmp/garmin_tokens"

client = None

def get_client():
    global client
    if client:
        return client
    
    from garminconnect import Garmin
    gc = Garmin(email=EMAIL, password=PASSWORD, is_cn=False)
    
    # Try loading saved tokens
    try:
        gc.login(TOKEN_STORE)
        logging.info("Logged in using saved tokens")
        client = gc
        return client
    except Exception as e:
        logging.info(f"Token login failed ({e}), doing fresh login")
    
    # Fresh login
    gc.login()
    # Save tokens for next time
    try:
        gc.garth.dump(TOKEN_STORE)
        logging.info("Tokens saved")
    except Exception as e:
        logging.warning(f"Could not save tokens: {e}")
    
    client = gc
    return client

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

@app.route("/sync")
@require_secret
def sync():
    global client
    date_str = request.args.get("date", str(date.today()))
    try:
        date.fromisoformat(date_str)
    except ValueError:
        return jsonify({"error": "Invalid date, use YYYY-MM-DD"}), 400

    result = {"date": date_str}

    try:
        gc = get_client()
    except Exception as e:
        return jsonify({"error": str(e), "hint": "Check GARMIN_EMAIL and GARMIN_PASSWORD env vars"}), 500

    # Daily summary
    try:
        data = gc.get_stats(date_str)
        if data:
            result["steps"] = data.get("totalSteps")
            result["calories_burned"] = data.get("totalKilocalories")
            result["active_calories"] = data.get("activeKilocalories")
            result["floors"] = data.get("floorsAscended")
            result["distance_km"] = round((data.get("totalDistanceMeters") or 0) / 1000, 2)
            result["active_minutes"] = (data.get("highlyActiveSeconds") or 0) // 60
            result["stress_avg"] = data.get("averageStressLevel")
            result["body_battery_high"] = data.get("maxBodyBattery")
            result["body_battery_low"] = data.get("minBodyBattery")
    except Exception as e:
        logging.warning(f"Stats error: {e}")
        client = None  # Reset so next call re-authenticates

    # Sleep
    try:
        data = gc.get_sleep_data(date_str)
        if data and "dailySleepDTO" in data:
            dto = data["dailySleepDTO"]
            secs = dto.get("sleepTimeSeconds", 0)
            result["sleep_hours"] = round(secs / 3600, 1) if secs else None
            result["sleep_deep_hours"] = round((dto.get("deepSleepSeconds") or 0) / 3600, 1)
            result["sleep_rem_hours"] = round((dto.get("remSleepSeconds") or 0) / 3600, 1)
            score = (dto.get("sleepScores") or {})
            overall = score.get("overall", {}).get("value") if isinstance(score, dict) else None
            result["sleep_score"] = overall
            if overall:
                result["sleep_quality"] = "🌟 Great" if overall >= 80 else "😊 Good" if overall >= 60 else "😐 OK" if overall >= 40 else "😴 Poor"
    except Exception as e:
        logging.warning(f"Sleep error: {e}")

    # Heart rate
    try:
        data = gc.get_heart_rates(date_str)
        if data:
            result["heart_rate_resting"] = data.get("restingHeartRate")
            result["heart_rate_max"] = data.get("maxHeartRate")
    except Exception as e:
        logging.warning(f"HR error: {e}")

    # HRV
    try:
        data = gc.get_hrv_data(date_str)
        if data and "hrvSummary" in data:
            result["hrv"] = data["hrvSummary"].get("weeklyAvg")
            result["hrv_last_night"] = data["hrvSummary"].get("lastNight")
    except Exception as e:
        logging.warning(f"HRV error: {e}")

    # Activities
    try:
        data = gc.get_activities_by_date(date_str, date_str)
        result["activities"] = []
        for act in (data or []):
            result["activities"].append({
                "name": act.get("activityName", "Activity"),
                "type": act.get("activityType", {}).get("typeKey", "unknown"),
                "duration_min": round((act.get("duration") or 0) / 60),
                "calories": act.get("calories", 0),
                "distance_km": round((act.get("distance") or 0) / 1000, 2) if act.get("distance") else None,
                "avg_hr": act.get("averageHR"),
            })
    except Exception as e:
        logging.warning(f"Activities error: {e}")
        result["activities"] = []

    return jsonify(result)

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
