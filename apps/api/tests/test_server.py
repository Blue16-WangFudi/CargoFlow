from __future__ import annotations

import json
import threading
import unittest
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import cargoflow_api.server as server_module
from cargoflow_api.server import (
    SERVICE_NAME,
    build_authorized_demo_shipment,
    build_demo_shipment,
    build_health_payload,
    create_server,
    eta_shipment_id,
    latest_location_shipment_id,
    trajectory_shipment_id,
)
from cargoflow_api.access_control import Principal, Role
from cargoflow_api.cargo_binding import CargoBindingStore
from cargoflow_api.location_ingest import DeviceEventStore
from cargoflow_api.shipment_tracking import ShipmentTrackingStore
from cargoflow_api.vehicle_management import VehicleStore


DEMO_AUTH_HEADERS = {
    "X-CargoFlow-User-Id": "owner-acme",
    "X-CargoFlow-Role": "cargo_owner",
    "X-CargoFlow-Tenant-Id": "cgf-demo",
}
WAREHOUSE_AUTH_HEADERS = {
    "X-CargoFlow-User-Id": "warehouse-admin",
    "X-CargoFlow-Role": "warehouse_admin",
    "X-CargoFlow-Tenant-Id": "cgf-demo",
    "X-CargoFlow-Warehouse-Ids": "warehouse-shanghai",
}


class PayloadTests(unittest.TestCase):
    def test_health_payload_has_required_fields(self) -> None:
        payload = build_health_payload()

        self.assertEqual(payload["service"], SERVICE_NAME)
        self.assertEqual(payload["status"], "ok")
        self.assertIn("version", payload)
        self.assertIn("time", payload)

    def test_demo_shipment_has_tracking_data(self) -> None:
        shipment = build_demo_shipment()

        self.assertEqual(shipment["shipmentId"], "CGF-DEMO-001")
        self.assertIn("latestLocation", shipment)
        self.assertIn("vehicle", shipment)
        self.assertIn("eta", shipment)

    def test_authorized_demo_shipment_includes_access_context(self) -> None:
        principal = Principal("owner-acme", Role.CARGO_OWNER, "cgf-demo")

        shipment = build_authorized_demo_shipment(principal)

        self.assertEqual(shipment["access"]["role"], "cargo_owner")
        self.assertEqual(shipment["access"]["principalId"], "owner-acme")

    def test_latest_location_route_parser_extracts_shipment_id(self) -> None:
        self.assertEqual(
            latest_location_shipment_id("/api/shipments/CGF-DEMO-001/latest-location"),
            "CGF-DEMO-001",
        )
        self.assertEqual(
            latest_location_shipment_id("/api/shipments/CGF%20DEMO/latest-location"),
            "CGF DEMO",
        )
        self.assertIsNone(latest_location_shipment_id("/api/shipments/demo"))

    def test_eta_route_parser_extracts_shipment_id(self) -> None:
        self.assertEqual(
            eta_shipment_id("/api/shipments/CGF-DEMO-001/eta"),
            "CGF-DEMO-001",
        )
        self.assertEqual(
            eta_shipment_id("/api/shipments/CGF%20DEMO/eta"),
            "CGF DEMO",
        )
        self.assertIsNone(eta_shipment_id("/api/shipments/CGF-DEMO-001/latest-location"))

    def test_trajectory_route_parser_extracts_shipment_id(self) -> None:
        self.assertEqual(
            trajectory_shipment_id("/api/shipments/CGF-DEMO-001/trajectory"),
            "CGF-DEMO-001",
        )
        self.assertEqual(
            trajectory_shipment_id("/api/shipments/CGF%20DEMO/trajectory"),
            "CGF DEMO",
        )
        self.assertIsNone(trajectory_shipment_id("/api/shipments/CGF-DEMO-001/eta"))


class HttpRouteTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.server = create_server("127.0.0.1", 0)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        host, port = cls.server.server_address
        cls.base_url = f"http://{host}:{port}"

    @classmethod
    def tearDownClass(cls) -> None:
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=2)

    def setUp(self) -> None:
        server_module.DEVICE_EVENTS = DeviceEventStore.demo()
        server_module.VEHICLES = VehicleStore.demo()
        server_module.SHIPMENT_TRACKING = ShipmentTrackingStore.demo()
        server_module.CARGO_BINDINGS = CargoBindingStore.demo()

    def test_health_route(self) -> None:
        with urlopen(f"{self.base_url}/health", timeout=3) as response:
            payload = json.loads(response.read().decode("utf-8"))

        self.assertEqual(response.status, 200)
        self.assertEqual(payload["status"], "ok")

    def test_demo_shipment_route(self) -> None:
        request = Request(
            f"{self.base_url}/api/shipments/demo",
            headers=DEMO_AUTH_HEADERS,
        )

        with urlopen(request, timeout=3) as response:
            payload = json.loads(response.read().decode("utf-8"))

        self.assertEqual(response.status, 200)
        self.assertEqual(payload["shipmentId"], "CGF-DEMO-001")
        self.assertEqual(payload["access"]["role"], "cargo_owner")

    def test_demo_shipment_route_requires_identity_headers(self) -> None:
        with self.assertRaises(HTTPError) as context:
            urlopen(f"{self.base_url}/api/shipments/demo", timeout=3)

        error = context.exception
        payload = json.loads(error.read().decode("utf-8"))
        self.assertEqual(error.code, 401)
        self.assertEqual(payload["error"], "unauthorized")

    def test_demo_shipment_route_rejects_forbidden_role_scope(self) -> None:
        headers = {
            **DEMO_AUTH_HEADERS,
            "X-CargoFlow-User-Id": "owner-other",
        }
        request = Request(f"{self.base_url}/api/shipments/demo", headers=headers)

        with self.assertRaises(HTTPError) as context:
            urlopen(request, timeout=3)

        error = context.exception
        payload = json.loads(error.read().decode("utf-8"))
        self.assertEqual(error.code, 403)
        self.assertEqual(payload["error"], "forbidden")

    def test_latest_location_route_returns_bound_shipment_snapshot(self) -> None:
        request = Request(
            f"{self.base_url}/api/shipments/CGF-DEMO-001/latest-location",
            headers=DEMO_AUTH_HEADERS,
        )

        with urlopen(request, timeout=3) as response:
            payload = json.loads(response.read().decode("utf-8"))

        self.assertEqual(response.status, 200)
        self.assertEqual(payload["shipmentId"], "CGF-DEMO-001")
        self.assertEqual(payload["transportStatus"], "in_transit")
        self.assertEqual(payload["vehicle"]["deviceId"], "gps-demo-001")
        self.assertEqual(payload["latestLocation"]["eventId"], "evt-demo-seed-location")
        self.assertIn("delayHint", payload)

    def test_latest_location_route_rejects_forbidden_owner(self) -> None:
        headers = {
            **DEMO_AUTH_HEADERS,
            "X-CargoFlow-User-Id": "owner-other",
        }
        request = Request(
            f"{self.base_url}/api/shipments/CGF-DEMO-001/latest-location",
            headers=headers,
        )

        with self.assertRaises(HTTPError) as context:
            urlopen(request, timeout=3)

        error = context.exception
        payload = json.loads(error.read().decode("utf-8"))
        self.assertEqual(error.code, 403)
        self.assertEqual(payload["error"], "forbidden")

    def test_eta_route_returns_remaining_distance_and_arrival(self) -> None:
        request = Request(
            f"{self.base_url}/api/shipments/CGF-DEMO-001/eta",
            headers=DEMO_AUTH_HEADERS,
        )

        with urlopen(request, timeout=3) as response:
            payload = json.loads(response.read().decode("utf-8"))

        self.assertEqual(response.status, 200)
        self.assertEqual(payload["shipmentId"], "CGF-DEMO-001")
        self.assertEqual(payload["transportStatus"], "in_transit")
        self.assertEqual(payload["eta"]["status"], "available")
        self.assertEqual(payload["eta"]["remainingDistanceKm"], 17.46)
        self.assertEqual(payload["eta"]["updatedAt"], "2026-05-13T10:00:03+00:00")
        self.assertEqual(
            payload["eta"]["destination"]["name"],
            "Shanghai Waigaoqiao Logistics Park",
        )
        self.assertIsNotNone(payload["eta"]["estimatedArrival"])

    def test_eta_route_rejects_forbidden_owner(self) -> None:
        headers = {
            **DEMO_AUTH_HEADERS,
            "X-CargoFlow-User-Id": "owner-other",
        }
        request = Request(
            f"{self.base_url}/api/shipments/CGF-DEMO-001/eta",
            headers=headers,
        )

        with self.assertRaises(HTTPError) as context:
            urlopen(request, timeout=3)

        error = context.exception
        payload = json.loads(error.read().decode("utf-8"))
        self.assertEqual(error.code, 403)
        self.assertEqual(payload["error"], "forbidden")

    def test_trajectory_route_returns_replay_points_and_key_nodes(self) -> None:
        request = Request(
            f"{self.base_url}/api/shipments/CGF-DEMO-001/trajectory",
            headers=DEMO_AUTH_HEADERS,
        )

        with urlopen(request, timeout=3) as response:
            payload = json.loads(response.read().decode("utf-8"))

        self.assertEqual(response.status, 200)
        self.assertEqual(payload["shipmentId"], "CGF-DEMO-001")
        self.assertEqual(payload["vehicle"]["deviceId"], "gps-demo-001")
        self.assertGreaterEqual(payload["summary"]["pointCount"], 5)
        self.assertEqual(payload["summary"]["gpsPointCount"], 1)
        self.assertTrue(payload["summary"]["hasStartPoint"])
        self.assertTrue(payload["summary"]["hasEndPoint"])
        kinds = [point["kind"] for point in payload["trajectory"]]
        self.assertEqual(kinds[0], "start")
        self.assertEqual(kinds[-1], "end")
        self.assertIn("gps", kinds)
        self.assertIn("alert", kinds)
        self.assertIn("status_report", kinds)

    def test_trajectory_route_rejects_forbidden_owner(self) -> None:
        headers = {
            **DEMO_AUTH_HEADERS,
            "X-CargoFlow-User-Id": "owner-other",
        }
        request = Request(
            f"{self.base_url}/api/shipments/CGF-DEMO-001/trajectory",
            headers=headers,
        )

        with self.assertRaises(HTTPError) as context:
            urlopen(request, timeout=3)

        error = context.exception
        payload = json.loads(error.read().decode("utf-8"))
        self.assertEqual(error.code, 403)
        self.assertEqual(payload["error"], "forbidden")

    def test_demo_shipment_route_supports_cors_preflight(self) -> None:
        request = Request(
            f"{self.base_url}/api/shipments/demo",
            method="OPTIONS",
            headers={
                "Origin": "http://127.0.0.1:5173",
                "Access-Control-Request-Method": "GET",
                "Access-Control-Request-Headers": "X-CargoFlow-User-Id",
            },
        )

        with urlopen(request, timeout=3) as response:
            headers = response.headers

        self.assertEqual(response.status, 204)
        self.assertEqual(headers["Access-Control-Allow-Origin"], "*")
        self.assertIn("X-CargoFlow-User-Id", headers["Access-Control-Allow-Headers"])
        self.assertIn("POST", headers["Access-Control-Allow-Methods"])

    def test_device_event_route_accepts_gps_and_updates_demo_location(self) -> None:
        payload = {
            "eventId": "evt-http-gps-1",
            "eventType": "gps",
            "deviceId": "gps-demo-001",
            "taskId": "task-demo-001",
            "occurredAt": "2026-05-13T10:05:00+00:00",
            "reportedAt": "2026-05-13T10:05:02+00:00",
            "schemaVersion": 1,
            "longitude": 121.5,
            "latitude": 31.2,
            "speedKph": 60,
            "headingDegrees": 90,
        }
        request = Request(
            f"{self.base_url}/api/device-events",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )

        with urlopen(request, timeout=3) as response:
            event_response = json.loads(response.read().decode("utf-8"))

        self.assertEqual(response.status, 202)
        self.assertTrue(event_response["latestLocationUpdated"])
        shipment_request = Request(
            f"{self.base_url}/api/shipments/demo",
            headers=DEMO_AUTH_HEADERS,
        )
        with urlopen(shipment_request, timeout=3) as response:
            shipment = json.loads(response.read().decode("utf-8"))
        self.assertEqual(shipment["latestLocation"]["eventId"], "evt-http-gps-1")
        self.assertEqual(shipment["latestLocation"]["longitude"], 121.5)
        latest_request = Request(
            f"{self.base_url}/api/shipments/CGF-DEMO-001/latest-location",
            headers=DEMO_AUTH_HEADERS,
        )
        with urlopen(latest_request, timeout=3) as response:
            latest = json.loads(response.read().decode("utf-8"))
        self.assertEqual(latest["latestLocation"]["eventId"], "evt-http-gps-1")
        self.assertEqual(latest["latestLocation"]["longitude"], 121.5)

    def test_device_event_route_accepts_heartbeat_and_box_events(self) -> None:
        for payload in (
            {
                "eventId": "evt-http-heartbeat-1",
                "eventType": "heartbeat",
                "deviceId": "gps-demo-001",
                "taskId": "task-demo-001",
                "occurredAt": "2026-05-13T10:05:00+00:00",
                "reportedAt": "2026-05-13T10:05:01+00:00",
                "schemaVersion": 1,
            },
            {
                "eventId": "evt-http-box-1",
                "eventType": "box_opened",
                "deviceId": "gps-demo-001",
                "taskId": "task-demo-001",
                "occurredAt": "2026-05-13T10:06:00+00:00",
                "reportedAt": "2026-05-13T10:06:02+00:00",
                "schemaVersion": 1,
            },
        ):
            with self.subTest(event_type=payload["eventType"]):
                request = Request(
                    f"{self.base_url}/api/device-events",
                    data=json.dumps(payload).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )

                with urlopen(request, timeout=3) as response:
                    event_response = json.loads(response.read().decode("utf-8"))

                self.assertEqual(response.status, 202)
                self.assertTrue(event_response["received"])
                self.assertFalse(event_response["latestLocationUpdated"])
                if payload["eventType"] == "box_opened":
                    self.assertEqual(len(event_response["generatedAlerts"]), 1)
                    self.assertEqual(
                        event_response["generatedAlerts"][0]["alertType"],
                        "box_opened",
                    )
                    self.assertEqual(
                        event_response["generatedAlerts"][0]["severity"],
                        "high",
                    )

    def test_device_event_route_rejects_unknown_device(self) -> None:
        request = Request(
            f"{self.base_url}/api/device-events",
            data=json.dumps(
                {
                    "eventId": "evt-http-gps-1",
                    "eventType": "gps",
                    "deviceId": "gps-other",
                    "taskId": "task-demo-001",
                    "occurredAt": "2026-05-13T10:05:00+00:00",
                    "reportedAt": "2026-05-13T10:05:02+00:00",
                    "schemaVersion": 1,
                    "longitude": 121.5,
                    "latitude": 31.2,
                }
            ).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )

        with self.assertRaises(HTTPError) as context:
            urlopen(request, timeout=3)

        error = context.exception
        payload = json.loads(error.read().decode("utf-8"))
        self.assertEqual(error.code, 400)
        self.assertEqual(payload["error"], "invalid_device_event")

    def test_vehicle_routes_create_list_update_disable_and_unbind(self) -> None:
        create_request = Request(
            f"{self.base_url}/api/vehicles",
            data=json.dumps(
                {
                    "vehicleId": "vehicle-http-001",
                    "vehicleNumber": "VH-HTTP-001",
                    "plateNumber": "SH-C12345",
                    "deviceId": "gps-http-001",
                    "driverUserId": "driver-http",
                }
            ).encode("utf-8"),
            headers={
                **WAREHOUSE_AUTH_HEADERS,
                "Content-Type": "application/json",
            },
            method="POST",
        )

        with urlopen(create_request, timeout=3) as response:
            created = json.loads(response.read().decode("utf-8"))

        self.assertEqual(response.status, 201)
        self.assertEqual(created["vehicle"]["vehicleId"], "vehicle-http-001")
        self.assertEqual(created["vehicle"]["bindingStatus"], "available")

        list_request = Request(
            f"{self.base_url}/api/vehicles",
            headers=WAREHOUSE_AUTH_HEADERS,
        )
        with urlopen(list_request, timeout=3) as response:
            listed = json.loads(response.read().decode("utf-8"))

        self.assertEqual(response.status, 200)
        self.assertEqual(listed["count"], 2)

        patch_request = Request(
            f"{self.base_url}/api/vehicles/vehicle-http-001",
            data=json.dumps({"plateNumber": "SH-C54321"}).encode("utf-8"),
            headers={
                **WAREHOUSE_AUTH_HEADERS,
                "Content-Type": "application/json",
            },
            method="PATCH",
        )
        with urlopen(patch_request, timeout=3) as response:
            updated = json.loads(response.read().decode("utf-8"))

        self.assertEqual(updated["vehicle"]["plateNumber"], "SH-C54321")

        unbind_request = Request(
            f"{self.base_url}/api/vehicles/vehicle-demo-001/unbind",
            data=json.dumps({"reason": "completed"}).encode("utf-8"),
            headers={
                **WAREHOUSE_AUTH_HEADERS,
                "Content-Type": "application/json",
            },
            method="POST",
        )
        with urlopen(unbind_request, timeout=3) as response:
            unbound = json.loads(response.read().decode("utf-8"))

        self.assertEqual(unbound["vehicle"]["bindingStatus"], "available")
        self.assertIsNone(unbound["vehicle"]["driverUserId"])

        disable_request = Request(
            f"{self.base_url}/api/vehicles/vehicle-http-001/disable",
            data=json.dumps({"reason": "maintenance"}).encode("utf-8"),
            headers={
                **WAREHOUSE_AUTH_HEADERS,
                "Content-Type": "application/json",
            },
            method="POST",
        )
        with urlopen(disable_request, timeout=3) as response:
            disabled = json.loads(response.read().decode("utf-8"))

        self.assertEqual(disabled["vehicle"]["bindingStatus"], "disabled")
        self.assertEqual(disabled["vehicle"]["onlineStatus"], "offline")

    def test_vehicle_routes_reject_duplicate_unique_keys(self) -> None:
        request = Request(
            f"{self.base_url}/api/vehicles",
            data=json.dumps(
                {
                    "vehicleId": "vehicle-http-duplicate",
                    "vehicleNumber": "VH-HTTP-002",
                    "plateNumber": "CF-2026",
                    "deviceId": "gps-http-002",
                }
            ).encode("utf-8"),
            headers={
                **WAREHOUSE_AUTH_HEADERS,
                "Content-Type": "application/json",
            },
            method="POST",
        )

        with self.assertRaises(HTTPError) as context:
            urlopen(request, timeout=3)

        error = context.exception
        payload = json.loads(error.read().decode("utf-8"))
        self.assertEqual(error.code, 409)
        self.assertEqual(payload["error"], "vehicle_conflict")

    def test_vehicle_routes_reject_duplicate_vehicle_id(self) -> None:
        request = Request(
            f"{self.base_url}/api/vehicles",
            data=json.dumps(
                {
                    "vehicleId": "vehicle-demo-001",
                    "vehicleNumber": "VH-HTTP-003",
                    "plateNumber": "SH-C99999",
                    "deviceId": "gps-http-003",
                }
            ).encode("utf-8"),
            headers={
                **WAREHOUSE_AUTH_HEADERS,
                "Content-Type": "application/json",
            },
            method="POST",
        )

        with self.assertRaises(HTTPError) as context:
            urlopen(request, timeout=3)

        error = context.exception
        payload = json.loads(error.read().decode("utf-8"))
        self.assertEqual(error.code, 409)
        self.assertEqual(payload["error"], "vehicle_conflict")

    def test_vehicle_routes_reject_cargo_owner(self) -> None:
        request = Request(
            f"{self.base_url}/api/vehicles",
            headers=DEMO_AUTH_HEADERS,
        )

        with self.assertRaises(HTTPError) as context:
            urlopen(request, timeout=3)

        error = context.exception
        payload = json.loads(error.read().decode("utf-8"))
        self.assertEqual(error.code, 403)
        self.assertEqual(payload["error"], "vehicle_access_denied")

    def test_cargo_binding_route_creates_task_and_tracking_link(self) -> None:
        create_vehicle_request = Request(
            f"{self.base_url}/api/vehicles",
            data=json.dumps(
                {
                    "vehicleId": "vehicle-bind-http-001",
                    "vehicleNumber": "VH-BIND-HTTP-001",
                    "plateNumber": "SH-BIND-HTTP",
                    "deviceId": "gps-bind-http-001",
                    "driverUserId": "driver-http",
                }
            ).encode("utf-8"),
            headers={
                **WAREHOUSE_AUTH_HEADERS,
                "Content-Type": "application/json",
            },
            method="POST",
        )
        with urlopen(create_vehicle_request, timeout=3) as response:
            self.assertEqual(response.status, 201)

        bind_request = Request(
            f"{self.base_url}/api/cargo-bindings",
            data=json.dumps(
                {
                    "cargoId": "cargo-pending-001",
                    "vehicleId": "vehicle-bind-http-001",
                }
            ).encode("utf-8"),
            headers={
                **WAREHOUSE_AUTH_HEADERS,
                "Content-Type": "application/json",
            },
            method="POST",
        )
        with urlopen(bind_request, timeout=3) as response:
            binding = json.loads(response.read().decode("utf-8"))

        self.assertEqual(response.status, 201)
        task_id = binding["binding"]["taskId"]
        self.assertEqual(binding["binding"]["shipmentId"], "CGF-PENDING-001")
        self.assertEqual(
            binding["binding"]["vehicle"]["bindingStatus"],
            "bound",
        )

        event_request = Request(
            f"{self.base_url}/api/device-events",
            data=json.dumps(
                {
                    "eventId": "evt-http-bound-cargo",
                    "eventType": "gps",
                    "deviceId": "gps-bind-http-001",
                    "taskId": task_id,
                    "occurredAt": "2026-05-13T10:10:00+00:00",
                    "reportedAt": "2026-05-13T10:10:02+00:00",
                    "schemaVersion": 1,
                    "longitude": 121.2,
                    "latitude": 31.2,
                }
            ).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urlopen(event_request, timeout=3) as response:
            event = json.loads(response.read().decode("utf-8"))

        self.assertTrue(event["latestLocationUpdated"])
        latest_request = Request(
            f"{self.base_url}/api/shipments/CGF-PENDING-001/latest-location",
            headers={
                "X-CargoFlow-User-Id": "owner-beta",
                "X-CargoFlow-Role": "cargo_owner",
                "X-CargoFlow-Tenant-Id": "cgf-demo",
            },
        )
        with urlopen(latest_request, timeout=3) as response:
            latest = json.loads(response.read().decode("utf-8"))

        self.assertEqual(response.status, 200)
        self.assertEqual(latest["cargoId"], "cargo-pending-001")
        self.assertEqual(latest["latestLocation"]["eventId"], "evt-http-bound-cargo")
        self.assertEqual(latest["vehicle"]["deviceId"], "gps-bind-http-001")

    def test_cargo_binding_route_rejects_cargo_owner(self) -> None:
        request = Request(
            f"{self.base_url}/api/cargo-bindings",
            data=json.dumps(
                {
                    "cargoId": "cargo-pending-001",
                    "vehicleId": "vehicle-demo-001",
                }
            ).encode("utf-8"),
            headers={
                **DEMO_AUTH_HEADERS,
                "Content-Type": "application/json",
            },
            method="POST",
        )

        with self.assertRaises(HTTPError) as context:
            urlopen(request, timeout=3)

        error = context.exception
        payload = json.loads(error.read().decode("utf-8"))
        self.assertEqual(error.code, 403)
        self.assertEqual(payload["error"], "cargo_binding_access_denied")


if __name__ == "__main__":
    unittest.main()
