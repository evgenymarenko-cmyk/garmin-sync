import os, json, logging
from datetime import date, timedelta
from flask import Flask, jsonify, request
from flask_cors import CORS
from garminconnect import Garmin, GarminConnectAuthenticationError, GarminConnectTooManyRequestsError

logging.basicConfig(level=logging.INFO)
app = Flask(__name__)
CORS(app)

EMAIL = os.environ.get("GARMIN_EMAIL")
PASSWORD = os.environ.get("GARMIN_PASSWORD")
API_SECRET = os.environ.get("API_SECRET", "healthlog123")

# Global client — persists session across requests
client = None
SESSION_FILE = "/tmp/garmin_session.json"

def get_client():
    global client
    if client is not None:
        return client

    client = Garmin(EMAIL, PASSWORD)

    # Try loading saved session first
    if os.path.exists(SESSION_FILE):
        try:
            with open(SESSION_FILE, "r") as f:
                saved = json.load(f)
            client.garth.loads(saved)
            client.display_name = saved.get("display_name", EMAIL)
            logging.info("Loaded saved Garmin session")
            return client
        except Exception as e:
            logging.warning(f"Saved session invalid: {e}")

    # Fresh login
    try:
        client.login()
        # Save session for reuse
        with open(SESSION_FILE, "w") as f:
            data = client.garth.dumps()
            if isinstance(data, str):
                f.write(data)
            else:
                json.dump(data, f)
        logging.info("Garmin login successful, session saved")
    except Exception as e:
        logging.error(f"Garmin login failed: {e}")
        client = None
        raise e
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
    return jsonify({"status": "Garmin sync server running", "endpoints": ["/health", "/sync", "/mfa"]})

@app.route("/health")
def health():
    return jsonify({"status": "ok"})

@app.route("/mfa", methods=["POST"])
def submit_mfa():
    """Submit MFA code when Garmin requests it"""
    global client
    data = request.json or {}
    code = data.get("code", "").strip()
    secret = data.get("secret", "")
    if secret != API_SECRET:
        return jsonify({"error": "Unauthorized"}), 401
    if not code:
        return jsonify({"error": "No code provided"}), 400
    try:
        client = Garmin(EMAIL, PASSWORD)
        client.login(code)
        # Save session
        with open(SESSION_FILE, "w") as f:
            data = client.garth.dumps()
            if isinstance(data, str):
                f.write(data)
            else:
                json.dump(data, f)
        return jsonify({"status": "MFA successful, logged in"})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/sync")
@require_secret
def sync():
    """Fetch Garmin data for a given date (defaults to today)"""
    date_str = request.args.get("date", str(date.today()))
    try:
        target_date = date.fromisoformat(date_str)
    except ValueError:
        return jsonify({"error": "Invalid date format, use YYYY-MM-DD"}), 400

    try:
        gc = get_client()
    except GarminConnectAuthenticationError:
        return jsonify({"error": "mfa_required", "message": "Garmin needs MFA code. POST to /mfa with your code."}), 401
    except Exception as e:
        return jsonify({"error": str(e)}), 500

    result = {"date": date_str}

    # Steps
    try:
        steps_data = gc.get_steps_data(date_str)
        total_steps = sum(s.get("steps", 0) for s in steps_data) if steps_data else 0
        result["steps"] = total_steps
    except Exception as e:
        result["steps"] = None
        logging.warning(f"Steps error: {e}")

    # Sleep
    try:
        sleep_data = gc.get_sleep_data(date_str)
        if sleep_data and "dailySleepDTO" in sleep_data:
            dto = sleep_data["dailySleepDTO"]
            sleep_seconds = dto.get("sleepTimeSeconds", 0)
            result["sleep_hours"] = round(sleep_seconds / 3600, 1) if sleep_seconds else None
            result["sleep_score"] = sleep_data.get("dailySleepDTO", {}).get("sleepScores", {}).get("overall", {}).get("value", None)
            # Map score to quality label
            score = result["sleep_score"]
            if score is not None:
                if score >= 80: result["sleep_quality"] = "🌟 Great"
                elif score >= 60: result["sleep_quality"] = "😊 Good"
                elif score >= 40: result["sleep_quality"] = "😐 OK"
                else: result["sleep_quality"] = "😴 Poor"
        else:
            result["sleep_hours"] = None
            result["sleep_quality"] = None
    except Exception as e:
        result["sleep_hours"] = None
        logging.warning(f"Sleep error: {e}")

    # Heart rate
    try:
        hr_data = gc.get_heart_rates(date_str)
        if hr_data:
            result["heart_rate_resting"] = hr_data.get("restingHeartRate")
            result["heart_rate_max"] = hr_data.get("maxHeartRate")
        else:
            result["heart_rate_resting"] = None
    except Exception as e:
        result["heart_rate_resting"] = None
        logging.warning(f"HR error: {e}")

    # Body battery / stress
    try:
        bb_data = gc.get_body_battery(date_str)
        if bb_data and len(bb_data) > 0:
            latest = bb_data[-1]
            result["body_battery"] = latest.get("value")
        else:
            result["body_battery"] = None
    except Exception as e:
        result["body_battery"] = None
        logging.warning(f"Body battery error: {e}")

    # Calories burned
    try:
        stats = gc.get_stats(date_str)
        if stats:
            result["calories_burned"] = stats.get("totalKilocalories")
            result["active_calories"] = stats.get("activeKilocalories")
            result["floors"] = stats.get("floorsAscended")
            result["intensity_minutes"] = stats.get("intensityMinutesGoal")
        else:
            result["calories_burned"] = None
    except Exception as e:
        result["calories_burned"] = None
        logging.warning(f"Stats error: {e}")

    # Activities / workouts
    try:
        activities = gc.get_activities_by_date(date_str, date_str)
        result["activities"] = []
        for act in (activities or []):
            result["activities"].append({
                "name": act.get("activityName", "Activity"),
                "type": act.get("activityType", {}).get("typeKey", "unknown"),
                "duration_min": round(act.get("duration", 0) / 60),
                "calories": act.get("calories", 0),
                "distance_km": round(act.get("distance", 0) / 1000, 2) if act.get("distance") else None,
                "avg_hr": act.get("averageHR"),
            })
    except Exception as e:
        result["activities"] = []
        logging.warning(f"Activities error: {e}")

    # HRV
    try:
        hrv = gc.get_hrv_data(date_str)
        if hrv and "hrvSummary" in hrv:
            result["hrv"] = hrv["hrvSummary"].get("weeklyAvg")
        else:
            result["hrv"] = None
    except Exception as e:
        result["hrv"] = None
        logging.warning(f"HRV error: {e}")

    return jsonify(result)

@app.route("/week")
@require_secret
def week():
    """Fetch last 7 days summary"""
    results = []
    for i in range(7):
        d = date.today() - timedelta(days=i)
        results.append({"date": str(d), "pending": True})
    return jsonify({"message": "Use /sync?date=YYYY-MM-DD for individual days", "last_7_days": [str(date.today() - timedelta(days=i)) for i in range(7)]})

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
