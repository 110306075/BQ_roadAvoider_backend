import googlemaps
from html import unescape
import re
from datetime import datetime
from google.cloud import bigquery
from google.oauth2 import service_account
import requests
import os
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

WEATHER_API_KEY = os.getenv("OPEN_WEATHER_MAP_API_KEY")
GOOGLE_MAP_KEY = os.getenv("GOOGLE_MAP_API_KEY")
GOOGLE_CREDENTIAL = os.getenv("GOOGLE_CREDENTIAL_PATH")
credentials = service_account.Credentials.from_service_account_file(GOOGLE_CREDENTIAL)
project_id = "rock-elevator-444916-u9"
WEATHER_API_KEY = os.getenv("OPEN_WEATHER_MAP_API_KEY")
GOOGLE_MAP_KEY = os.getenv("GOOGLE_MAP_API_KEY")
# OpenWeatherMap settings
weather_map = {
    "clear sky": "sunny",
    "few clouds": "sunny",
    "scattered clouds": "cloudy",
    "broken clouds": "cloudy",
    "shower rain": "rainy",
    "rain": "rainy",
    "thunderstorm": "rainy",
    "snow": "rainy",
    "mist": "rainy",
}


def get_weather(lat, lon, api_key):
    url = f"https://api.openweathermap.org/data/2.5/weather?lat={lat}&lon={lon}&appid={api_key}&units=metric"
    response = requests.get(url)
    if response.status_code == 200:
        weather_data = response.json()
        weather_description = weather_data["weather"][0]["description"]
        return {"description": weather_map.get(weather_description, "unknown")}
    else:
        return {"description": None}


def get_checkpoints(step_points, num_points=3):
    total_points = len(step_points)
    checkpoint_indices = [
        int(i * (total_points - 1) / (num_points - 1)) for i in range(num_points)
    ]
    return [step_points[idx] for idx in checkpoint_indices]


def predict_route_risks(route, api_key):
    risks = []
    client = bigquery.Client(credentials=credentials, project=project_id)

    for leg in route["legs"]:
        for step in leg["steps"]:
            step_points = googlemaps.convert.decode_polyline(step["polyline"]["points"])
            checkpoints = get_checkpoints(step_points)

            now = datetime.now()
            current_hour = now.hour
            day_of_week = now.isoweekday()

            if len(checkpoints) == 0:
                continue

            enriched_checkpoints = []
            for p in checkpoints:
                weather = get_weather(p["lat"], p["lng"], WEATHER_API_KEY)
                enriched_checkpoints.append(
                    {
                        "lat": p["lat"],
                        "lng": p["lng"],
                        "hour": current_hour,
                        "day_of_week": day_of_week,
                        "weather": weather["description"],
                    }
                )

            checkpoint_sql = ",".join(
                [
                    f"STRUCT({p['lat']} as coord_y, {p['lng']} as coord_x, {p['hour']} as hour, {p['day_of_week']} as day_of_week, '{p['weather']}' as weather)"
                    for p in enriched_checkpoints
                ]
            )

            query = f"""
            SELECT
              cp.*,
              predicted_severity_level,
              predicted_severity_level_probs[OFFSET(0)] as prob_severe,
              predicted_severity_level_probs[OFFSET(1)] as prob_moderate,
              predicted_severity_level_probs[OFFSET(2)] as prob_minor
            FROM UNNEST([{checkpoint_sql}]) as cp,
            ML.PREDICT(
              MODEL `taipei_traffic_data.route_risk_model_v9`,
              (SELECT * FROM UNNEST([{checkpoint_sql}]))
            )
            """

            query_job = client.query(query)
            results = list(query_job.result())

            risk_weights = {"minor": 0.2, "moderate": 0.5, "severe": 1.0}

            risk_score = 0
            for r in results:
                # weighted_score = (
                #     r.prob_severe * risk_weights['severe'] +
                #     r.prob_moderate * risk_weights['moderate'] +
                #     r.prob_minor * risk_weights['minor']
                # )
                # risk_score += weighted_score
                prob_severe = r.prob_severe["prob"]
                prob_moderate = r.prob_moderate["prob"]
                prob_minor = r.prob_minor["prob"]

                # Calculate the weighted risk score
                weighted_score = (
                    prob_severe * risk_weights["severe"]
                    + prob_moderate * risk_weights["moderate"]
                    + prob_minor * risk_weights["minor"]
                )
                risk_score += weighted_score

            risk_score = risk_score / len(results)

            if risk_score > 0.4:
                description = "此路段交通事故發生頻率中等"
                if risk_score > 0.6:
                    description = "此路段交通事故發生頻率較高"
                risks.append(
                    {
                        "start": checkpoints[0],
                        "end": checkpoints[-1],
                        "risk_score": risk_score,
                        "description": description,
                    }
                )

    return risks


def get_directions_with_avoidance(origin_lat, origin_lng, dest_lat, dest_lng):
    gmaps = googlemaps.Client(key=GOOGLE_MAP_KEY)
    origin = f"{origin_lat},{origin_lng}"
    destination = f"{dest_lat},{dest_lng}"
    response = gmaps.directions(origin, destination, mode="driving", alternatives=True)

    route_risks = []
    for route in response:
        risks = predict_route_risks(route, GOOGLE_MAP_KEY)
        avg_risk = sum(r["risk_score"] for r in risks) / len(risks) if risks else 0
        route_risks.append(
            {"route": route, "segment_risks": risks, "avg_risk": avg_risk}
        )

    safest_route = min(route_risks, key=lambda x: x["avg_risk"])

    result = {
        "route": safest_route["route"],
        "segmentRisks": [
            {
                "start": risk["start"],
                "end": risk["end"],
                "risk_score": risk["risk_score"],
                "description": risk["description"],
            }
            for risk in safest_route["segment_risks"]
            if risk["risk_score"] > 0.3
        ],
    }

    return result
