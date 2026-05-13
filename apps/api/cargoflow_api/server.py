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
from urllib.parse import unquote, urlparse

from cargoflow_api import __version__
from cargoflow_api.access_control import (
    AccessControlError,
    Principal,
    Role,
    ShipmentScope,
    parse_principal,
    require_shipment_access,
)
from cargoflow_api.driver_status_reporting import (
    DriverStatusReportError,
    parse_driver_status_report_payload,
    status_report_to_wire,
)
from cargoflow_api.eta import EtaService
from cargoflow_api.location_ingest import DeviceEventError, DeviceEventStore
from cargoflow_api.shipment_tracking import ShipmentTrackingError, ShipmentTrackingStore
from cargoflow_api.vehicle_management import (
    VehicleManagementError,
    VehicleStore,
    vehicle_to_wire,
)

SERVICE_NAME = "cargoflow-api"
MAX_JSON_BODY_BYTES = 32 * 1024


DEMO_SHIPMENT_SCOPE = ShipmentScope(
    shipment_id="CGF-DEMO-001",
    tenant_id="cgf-demo",
    owner_user_id="owner-acme",
    driver_user_id="driver-demo",
    warehouse_ids=("warehouse-shanghai",),
    dispatch_region_ids=("east-china",),
)
SHIPMENT_TRACKING = ShipmentTrackingStore.demo()
DEVICE_EVENTS = DeviceEventStore.demo()
ETA_SERVICE = EtaService()
VEHICLES = VehicleStore.demo()


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
    latest_location = DEVICE_EVENTS.latest_location("task-demo-001")
    if latest_location is None:
        latest_location_payload = {
            "longitude": 121.4737,
            "latitude": 31.2304,
            "recordedAt": "2026-05-13T10:00:00+00:00",
        }
    else:
        latest_location_payload = {
            "longitude": latest_location.longitude,
            "latitude": latest_location.latitude,
            "recordedAt": latest_location.captured_at.isoformat(),
            "reportedAt": latest_location.reported_at.isoformat(),
            "eventId": latest_location.event_id,
        }

    eta_payload = SHIPMENT_TRACKING.eta_payload(
        "CGF-DEMO-001",
        Principal("owner-acme", Role.CARGO_OWNER, "cgf-demo"),
        DEVICE_EVENTS,
        ETA_SERVICE,
    )["eta"]

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
        "latestLocation": latest_location_payload,
        "eta": eta_payload,
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


def latest_location_shipment_id(path: str) -> str | None:
    return shipment_action_id(path, "latest-location")


def eta_shipment_id(path: str) -> str | None:
    return shipment_action_id(path, "eta")


def trajectory_shipment_id(path: str) -> str | None:
    return shipment_action_id(path, "trajectory")


def driver_status_report_shipment_id(path: str) -> str | None:
    return shipment_action_id(path, "driver-status-reports")


def vehicle_id_from_path(path: str) -> str | None:
    parts = [unquote(part) for part in path.strip("/").split("/") if part]
    if len(parts) == 3 and parts[:2] == ["api", "vehicles"]:
        return parts[2]
    return None


def vehicle_action_from_path(path: str, action: str) -> str | None:
    parts = [unquote(part) for part in path.strip("/").split("/") if part]
    if len(parts) == 4 and parts[:2] == ["api", "vehicles"] and parts[3] == action:
        return parts[2]
    return None


def shipment_action_id(path: str, action: str) -> str | None:
    parts = [unquote(part) for part in path.strip("/").split("/") if part]
    if len(parts) == 4 and parts[:2] == ["api", "shipments"]:
        if parts[3] == action:
            return parts[2]
    return None


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
        if path == "/api/vehicles":
            self.send_vehicle_list()
            return
        vehicle_id = vehicle_id_from_path(path)
        if vehicle_id is not None:
            self.send_vehicle(vehicle_id)
            return
        shipment_id = latest_location_shipment_id(path)
        if shipment_id is not None:
            self.send_latest_shipment_location(shipment_id)
            return
        shipment_id = eta_shipment_id(path)
        if shipment_id is not None:
            self.send_shipment_eta(shipment_id)
            return
        shipment_id = trajectory_shipment_id(path)
        if shipment_id is not None:
            self.send_shipment_trajectory(shipment_id)
            return
        self.send_json(
            HTTPStatus.NOT_FOUND,
            {
                "error": "not_found",
                "message": f"No CargoFlow route for {path}",
            },
        )

    def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        path = urlparse(self.path).path
        if path == "/api/device-events":
            self.receive_device_event()
            return
        if path == "/api/vehicles":
            self.create_vehicle()
            return
        shipment_id = driver_status_report_shipment_id(path)
        if shipment_id is not None:
            self.receive_driver_status_report(shipment_id)
            return
        vehicle_id = vehicle_action_from_path(path, "disable")
        if vehicle_id is not None:
            self.disable_vehicle(vehicle_id)
            return
        vehicle_id = vehicle_action_from_path(path, "unbind")
        if vehicle_id is not None:
            self.unbind_vehicle(vehicle_id)
            return
        self.send_json(
            HTTPStatus.NOT_FOUND,
            {
                "error": "not_found",
                "message": f"No CargoFlow route for {path}",
            },
        )

    def do_PATCH(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        path = urlparse(self.path).path
        vehicle_id = vehicle_id_from_path(path)
        if vehicle_id is not None:
            self.update_vehicle(vehicle_id)
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

    def send_vehicle_list(self) -> None:
        try:
            principal = parse_principal(self.headers)
            vehicles = VEHICLES.list_vehicles(principal)
        except AccessControlError as exc:
            self.send_access_error(exc)
            return
        except VehicleManagementError as exc:
            self.send_vehicle_error(exc)
            return
        self.send_json(
            HTTPStatus.OK,
            {
                "vehicles": [vehicle_to_wire(vehicle) for vehicle in vehicles],
                "count": len(vehicles),
            },
        )

    def send_vehicle(self, vehicle_id: str) -> None:
        try:
            principal = parse_principal(self.headers)
            vehicle = VEHICLES.get_vehicle(vehicle_id, principal)
        except AccessControlError as exc:
            self.send_access_error(exc)
            return
        except VehicleManagementError as exc:
            self.send_vehicle_error(exc)
            return
        self.send_json(HTTPStatus.OK, {"vehicle": vehicle_to_wire(vehicle)})

    def create_vehicle(self) -> None:
        try:
            principal = parse_principal(self.headers)
            payload = self.read_json_body()
            vehicle = VEHICLES.create_vehicle(payload, principal)
        except AccessControlError as exc:
            self.send_access_error(exc)
            return
        except (DeviceEventError, VehicleManagementError) as exc:
            self.send_vehicle_error(exc)
            return
        self.send_json(HTTPStatus.CREATED, {"vehicle": vehicle_to_wire(vehicle)})

    def update_vehicle(self, vehicle_id: str) -> None:
        try:
            principal = parse_principal(self.headers)
            payload = self.read_json_body()
            vehicle = VEHICLES.update_vehicle(vehicle_id, payload, principal)
        except AccessControlError as exc:
            self.send_access_error(exc)
            return
        except (DeviceEventError, VehicleManagementError) as exc:
            self.send_vehicle_error(exc)
            return
        self.send_json(HTTPStatus.OK, {"vehicle": vehicle_to_wire(vehicle)})

    def disable_vehicle(self, vehicle_id: str) -> None:
        try:
            principal = parse_principal(self.headers)
            payload = self.read_optional_json_body()
            reason = (
                payload.get("reason")
                if isinstance(payload.get("reason"), str)
                else None
            )
            vehicle = VEHICLES.disable_vehicle(vehicle_id, principal, reason=reason)
        except AccessControlError as exc:
            self.send_access_error(exc)
            return
        except (DeviceEventError, VehicleManagementError) as exc:
            self.send_vehicle_error(exc)
            return
        self.send_json(HTTPStatus.OK, {"vehicle": vehicle_to_wire(vehicle)})

    def unbind_vehicle(self, vehicle_id: str) -> None:
        try:
            principal = parse_principal(self.headers)
            payload = self.read_optional_json_body()
            reason = (
                payload.get("reason")
                if isinstance(payload.get("reason"), str)
                else None
            )
            vehicle = VEHICLES.unbind_vehicle(vehicle_id, principal, reason=reason)
        except AccessControlError as exc:
            self.send_access_error(exc)
            return
        except (DeviceEventError, VehicleManagementError) as exc:
            self.send_vehicle_error(exc)
            return
        self.send_json(HTTPStatus.OK, {"vehicle": vehicle_to_wire(vehicle)})

    def send_latest_shipment_location(self, shipment_id: str) -> None:
        try:
            principal = parse_principal(self.headers)
            payload = SHIPMENT_TRACKING.latest_location_payload(
                shipment_id,
                principal,
                DEVICE_EVENTS,
            )
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
        except ShipmentTrackingError as exc:
            self.send_json(
                exc.status,
                {
                    "error": exc.error_code,
                    "message": exc.message,
                },
            )
            return
        self.send_json(HTTPStatus.OK, payload)

    def send_shipment_eta(self, shipment_id: str) -> None:
        try:
            principal = parse_principal(self.headers)
            payload = SHIPMENT_TRACKING.eta_payload(
                shipment_id,
                principal,
                DEVICE_EVENTS,
                ETA_SERVICE,
            )
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
        except ShipmentTrackingError as exc:
            self.send_json(
                exc.status,
                {
                    "error": exc.error_code,
                    "message": exc.message,
                },
            )
            return
        self.send_json(HTTPStatus.OK, payload)

    def send_shipment_trajectory(self, shipment_id: str) -> None:
        try:
            principal = parse_principal(self.headers)
            payload = SHIPMENT_TRACKING.trajectory_payload(
                shipment_id,
                principal,
                DEVICE_EVENTS,
            )
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
        except ShipmentTrackingError as exc:
            self.send_json(
                exc.status,
                {
                    "error": exc.error_code,
                    "message": exc.message,
                },
            )
            return
        self.send_json(HTTPStatus.OK, payload)

    def receive_device_event(self) -> None:
        try:
            payload = self.read_json_body()
            result = DEVICE_EVENTS.ingest(payload)
        except DeviceEventError as exc:
            self.send_json(
                HTTPStatus.BAD_REQUEST,
                {
                    "error": exc.error_code,
                    "message": exc.message,
                },
            )
            return
        self.send_json(HTTPStatus.ACCEPTED, result.to_wire())

    def receive_driver_status_report(self, shipment_id: str) -> None:
        try:
            principal = parse_principal(self.headers)
            payload = self.read_json_body()
            report_payload = parse_driver_status_report_payload(
                payload,
                now=datetime.now(UTC),
            )
            report, record = SHIPMENT_TRACKING.add_driver_status_report(
                shipment_id,
                principal,
                report_payload,
            )
        except AccessControlError as exc:
            self.send_access_error(exc)
            return
        except ShipmentTrackingError as exc:
            self.send_json(
                exc.status,
                {
                    "error": exc.error_code,
                    "message": exc.message,
                },
            )
            return
        except DriverStatusReportError as exc:
            self.send_json(
                exc.status,
                {
                    "error": exc.error_code,
                    "message": exc.message,
                },
            )
            return
        except DeviceEventError as exc:
            self.send_json(
                HTTPStatus.BAD_REQUEST,
                {
                    "error": exc.error_code,
                    "message": exc.message,
                },
            )
            return
        self.send_json(
            HTTPStatus.CREATED,
            {
                "shipmentId": record.shipment_id,
                "taskId": record.task_id,
                "transportStatus": record.transport_status.value,
                "statusReport": status_report_to_wire(report),
            },
        )

    def send_access_error(self, exc: AccessControlError) -> None:
        status = HTTPStatus(exc.status_code)
        self.send_json(
            status,
            {
                "error": exc.error_code,
                "message": exc.message,
            },
            authenticate=status is HTTPStatus.UNAUTHORIZED,
        )

    def send_vehicle_error(
        self,
        exc: DeviceEventError | VehicleManagementError,
    ) -> None:
        if isinstance(exc, VehicleManagementError):
            self.send_json(
                exc.status,
                {
                    "error": exc.error_code,
                    "message": exc.message,
                },
            )
            return
        self.send_json(
            HTTPStatus.BAD_REQUEST,
            {
                "error": exc.error_code,
                "message": exc.message,
            },
        )

    def read_json_body(self) -> dict[str, Any]:
        content_length = self.headers.get("Content-Length", "0").strip()
        try:
            body_size = int(content_length)
        except ValueError as exc:
            raise DeviceEventError("Content-Length must be an integer") from exc
        if body_size <= 0:
            raise DeviceEventError("Request body must not be empty")
        if body_size > MAX_JSON_BODY_BYTES:
            raise DeviceEventError("Request body is too large")
        raw_body = self.rfile.read(body_size)
        try:
            payload = json.loads(raw_body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise DeviceEventError("Request body must be a JSON object") from exc
        if not isinstance(payload, dict):
            raise DeviceEventError("Request body must be a JSON object")
        return payload

    def read_optional_json_body(self) -> dict[str, Any]:
        content_length = self.headers.get("Content-Length", "0").strip()
        try:
            body_size = int(content_length)
        except ValueError as exc:
            raise DeviceEventError("Content-Length must be an integer") from exc
        if body_size == 0:
            return {}
        return self.read_json_body()

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
        self.send_header("Access-Control-Allow-Methods", "GET, POST, PATCH, OPTIONS")
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
