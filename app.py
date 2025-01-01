# app.py
import os
from flask import Flask, request, jsonify
from service import get_weather, get_directions_with_avoidance
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

app = Flask(__name__)


@app.route("/route", methods=["POST"])
def weather_endpoint():

    data = request.json
    source_lat = data.get("source_lat")
    source_long = data.get("source_long")
    dest_lat = data.get("dest_lat")
    dest_long = data.get("dest_long")

    if not all([source_lat, source_lat, dest_lat, dest_long]):
        return jsonify({"error": "Missing required parameters"}), 400

    result = get_directions_with_avoidance(source_lat, source_long, dest_lat, dest_long)


    return jsonify(result)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080, debug=True)