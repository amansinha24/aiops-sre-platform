from flask import Flask, render_template, jsonify
import requests
import os
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)

API_URL = os.getenv("API_URL", "http://api-service:8000")

@app.route("/")
def index():
    """Main dashboard page"""
    try:
        response = requests.get(f"{API_URL}/api/incidents", timeout=5)
        incidents = response.json().get("incidents", [])
    except Exception as e:
        logger.error(f"Failed to fetch incidents: {e}")
        incidents = []
    return render_template("index.html", incidents=incidents)

@app.route("/health")
def health():
    """Health check endpoint"""
    return jsonify({"status": "healthy", "service": "frontend"})

@app.route("/api/status")
def status():
    """Check API connectivity"""
    try:
        response = requests.get(f"{API_URL}/health", timeout=5)
        return jsonify({
            "frontend": "healthy",
            "api": response.json()
        })
    except Exception as e:
        return jsonify({
            "frontend": "healthy",
            "api": "unreachable",
            "error": str(e)
        }), 503

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=False)