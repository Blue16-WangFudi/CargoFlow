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
)
from cargoflow_api.access_control import Principal, Role
from cargoflow_api.location_ingest import DeviceEventStore


DEMO_AUTH_HEADERS = {
    "X-CargoFlow-User-Id": "owner-acme",
    "X-CargoFlow-Role": "cargo_owner",
    "X-CargoFlow-Tenant-Id": "cgf-demo",
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


if __name__ == "__main__":
    unittest.main()
