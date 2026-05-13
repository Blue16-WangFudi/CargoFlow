from __future__ import annotations

import json
import threading
import unittest
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from cargoflow_api.server import (
    SERVICE_NAME,
    build_authorized_demo_shipment,
    build_demo_shipment,
    build_health_payload,
    create_server,
)
from cargoflow_api.access_control import Principal, Role


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


if __name__ == "__main__":
    unittest.main()
