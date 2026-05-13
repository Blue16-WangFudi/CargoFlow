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
from cargoflow_api.access_control import (
    AccessControlError,
    Principal,
    ShipmentScope,
    parse_principal,
    require_shipment_access,
)

SERVICE_NAME = "cargoflow-api"


DEMO_SHIPMENT_SCOPE = ShipmentScope(
    shipment_id="CGF-DEMO-001",
    tenant_id="cgf-demo",
    owner_user_id="owner-acme",
    driver_user_id="driver-demo",
    warehouse_ids=("warehouse-shanghai",),
    dispatch_region_ids=("east-china",),
)


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
        "tenantId": DEMO_SHIPMENT_SCOPE.tenant_id,
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


def build_authorized_demo_shipment(principal: Principal) -> dict[str, Any]:
    require_shipment_access(principal, DEMO_SHIPMENT_SCOPE)
    payload = build_demo_shipment()
    payload["access"] = {
        "role": principal.role.value,
        "principalId": principal.user_id,
    }
    return payload


class CargoFlowHandler(BaseHTTPRequestHandler):
    server_version = f"{SERVICE_NAME}/{__version__}"

    def do_OPTIONS(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        self.send_response(HTTPStatus.NO_CONTENT)
        self.send_cors_headers()
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        path = urlparse(self.path).path
        if path == "/health":
            self.send_json(HTTPStatus.OK, build_health_payload())
            return
        if path == "/api/shipments/demo":
            self.send_guarded_demo_shipment()
            return
        self.send_json(
            HTTPStatus.NOT_FOUND,
            {
                "error": "not_found",
                "message": f"No CargoFlow route for {path}",
            },
        )

    def send_guarded_demo_shipment(self) -> None:
        try:
            principal = parse_principal(self.headers)
            payload = build_authorized_demo_shipment(principal)
        except AccessControlError as exc:
            status = HTTPStatus(exc.status_code)
            self.send_json(
                status,
                {
                    "error": exc.error_code,
                    "message": exc.message,
                },
                authenticate=status is HTTPStatus.UNAUTHORIZED,
            )
            return
        self.send_json(HTTPStatus.OK, payload)

    def log_message(self, format: str, *args: Any) -> None:
        timestamp = utc_now_iso()
        print(f"{timestamp} {self.address_string()} {format % args}")

    def send_json(
        self,
        status: HTTPStatus,
        payload: dict[str, Any],
        *,
        authenticate: bool = False,
    ) -> None:
        body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode(
            "utf-8"
        )
        self.send_response(status)
        self.send_cors_headers()
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Type", "application/json; charset=utf-8")
        if authenticate:
            self.send_header(
                "WWW-Authenticate",
                'CargoFlow realm="development-auth-headers"',
            )
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def send_cors_headers(self) -> None:
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")
        self.send_header(
            "Access-Control-Allow-Headers",
            (
                "Content-Type, X-CargoFlow-User-Id, X-CargoFlow-Role, "
                "X-CargoFlow-Tenant-Id, X-CargoFlow-Warehouse-Ids, "
                "X-CargoFlow-Dispatch-Region-Ids"
            ),
        )


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
