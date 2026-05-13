"""Minimal CargoFlow API server.

The first engineering slice avoids framework dependencies so that a new
contributor can run the product skeleton with only Python installed.
"""

from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import urlparse

from cargoflow_api import __version__

SERVICE_NAME = "cargoflow-api"


def utc_now_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()


def build_health_payload() -> dict[str, Any]:
    return {
        "service": SERVICE_NAME,
        "status": "ok",
        "version": __version__,
        "time": utc_now_iso(),
    }


def build_demo_shipment() -> dict[str, Any]:
    return {
        "shipmentId": "CGF-DEMO-001",
        "cargo": {
            "owner": "Acme Export",
            "description": "Temperature controlled cargo",
            "status": "in_transit",
        },
        "vehicle": {
            "plateNumber": "CF-2026",
            "deviceId": "gps-demo-001",
            "driver": "Demo Driver",
        },
        "latestLocation": {
            "longitude": 121.4737,
            "latitude": 31.2304,
            "recordedAt": "2026-05-13T10:00:00+00:00",
        },
        "eta": {
            "destination": "Shanghai Waigaoqiao Logistics Park",
            "estimatedArrival": "2026-05-13T14:30:00+00:00",
            "remainingDistanceKm": 42.5,
        },
        "alerts": [],
    }


class CargoFlowHandler(BaseHTTPRequestHandler):
    server_version = f"{SERVICE_NAME}/{__version__}"

    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        path = urlparse(self.path).path
        if path == "/health":
            self.send_json(HTTPStatus.OK, build_health_payload())
            return
        if path == "/api/shipments/demo":
            self.send_json(HTTPStatus.OK, build_demo_shipment())
            return
        self.send_json(
            HTTPStatus.NOT_FOUND,
            {
                "error": "not_found",
                "message": f"No CargoFlow route for {path}",
            },
        )

    def log_message(self, format: str, *args: Any) -> None:
        timestamp = utc_now_iso()
        print(f"{timestamp} {self.address_string()} {format % args}")

    def send_json(self, status: HTTPStatus, payload: dict[str, Any]) -> None:
        body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode(
            "utf-8"
        )
        self.send_response(status)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def create_server(host: str, port: int) -> ThreadingHTTPServer:
    return ThreadingHTTPServer((host, port), CargoFlowHandler)


def run(host: str, port: int) -> None:
    server = create_server(host, port)
    print(f"{SERVICE_NAME} listening on http://{host}:{port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("Stopping CargoFlow API")
    finally:
        server.server_close()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the CargoFlow API service")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    run(args.host, args.port)


if __name__ == "__main__":
    main()
