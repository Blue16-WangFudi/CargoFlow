from __future__ import annotations

import json
import threading
import unittest
from urllib.request import urlopen

from cargoflow_api.server import (
    SERVICE_NAME,
    build_demo_shipment,
    build_health_payload,
    create_server,
)


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
        with urlopen(f"{self.base_url}/api/shipments/demo", timeout=3) as response:
            payload = json.loads(response.read().decode("utf-8"))

        self.assertEqual(response.status, 200)
        self.assertEqual(payload["shipmentId"], "CGF-DEMO-001")


if __name__ == "__main__":
    unittest.main()
