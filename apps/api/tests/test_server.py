from __future__ import annotations

import json
import threading
import unittest
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import cargoflow_api.server as server_module
from cargoflow_api.server import (
    SERVICE_NAME,
    alert_action_from_path,
    alert_id_from_path,
    build_authorized_demo_shipment,
    build_demo_shipment,
    build_health_payload,
    create_server,
    driver_command_action_from_path,
    driver_task_action_from_path,
    eta_shipment_id,
    latest_location_shipment_id,
    sign_shipment_id,
    trajectory_shipment_id,
)
from cargoflow_api.access_control import Principal, Role
from cargoflow_api.alert_handling import AlertHandlingStore
from cargoflow_api.alert_rules import AlertRuleStore
from cargoflow_api.cargo_binding import CargoBindingStore
from cargoflow_api.dispatch_distribution import DispatchDistributionStore
from cargoflow_api.driver_workflow import DriverWorkflowStore
from cargoflow_api.location_ingest import DeviceEventStore
from cargoflow_api.qa_service import QaService
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
DISPATCHER_AUTH_HEADERS = {
    "X-CargoFlow-User-Id": "dispatcher-demo",
    "X-CargoFlow-Role": "dispatcher",
    "X-CargoFlow-Tenant-Id": "cgf-demo",
    "X-CargoFlow-Dispatch-Region-Ids": "east-china",
}
SYSTEM_ADMIN_AUTH_HEADERS = {
    "X-CargoFlow-User-Id": "admin-demo",
    "X-CargoFlow-Role": "system_admin",
    "X-CargoFlow-Tenant-Id": "cgf-demo",
}
DRIVER_AUTH_HEADERS = {
    "X-CargoFlow-User-Id": "driver-demo",
    "X-CargoFlow-Role": "driver",
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

    def test_sign_route_parser_extracts_shipment_id(self) -> None:
        self.assertEqual(
            sign_shipment_id("/api/shipments/CGF-DEMO-001/sign"),
            "CGF-DEMO-001",
        )
        self.assertEqual(
            sign_shipment_id("/api/shipments/CGF%20DEMO/sign"),
            "CGF DEMO",
        )
        self.assertIsNone(sign_shipment_id("/api/shipments/CGF-DEMO-001/eta"))

    def test_alert_route_parsers_extract_alert_id(self) -> None:
        self.assertEqual(alert_id_from_path("/api/alerts/alert-demo"), "alert-demo")
        self.assertEqual(alert_id_from_path("/api/alerts/alert%201"), "alert 1")
        self.assertIsNone(alert_id_from_path("/api/alerts"))
        self.assertEqual(
            alert_action_from_path("/api/alerts/alert-demo/false-positive", "false-positive"),
            "alert-demo",
        )
        self.assertIsNone(alert_action_from_path("/api/alerts/alert-demo", "close"))

    def test_driver_route_parsers_extract_ids(self) -> None:
        self.assertEqual(
            driver_task_action_from_path(
                "/api/driver/tasks/task-demo-001/status-reports",
                "status-reports",
            ),
            "task-demo-001",
        )
        self.assertEqual(
            driver_task_action_from_path(
                "/api/driver/tasks/task%201/status-reports",
                "status-reports",
            ),
            "task 1",
        )
        self.assertIsNone(
            driver_task_action_from_path("/api/driver/tasks/task-demo-001", "status-reports")
        )
        self.assertEqual(
            driver_command_action_from_path(
                "/api/driver/commands/cmd-demo-box-001/acknowledge",
                "acknowledge",
            ),
            "cmd-demo-box-001",
        )


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
        server_module.ALERT_RULES = AlertRuleStore()
        server_module.ALERT_HANDLING = AlertHandlingStore.demo(server_module.ALERT_RULES)
        server_module.DEVICE_EVENTS = DeviceEventStore.demo(server_module.ALERT_RULES)
        server_module.VEHICLES = VehicleStore.demo()
        server_module.SHIPMENT_TRACKING = ShipmentTrackingStore.demo()
        server_module.CARGO_BINDINGS = CargoBindingStore.demo()
        server_module.DISPATCH_DISTRIBUTION = DispatchDistributionStore.demo()
        server_module.DRIVER_WORKFLOW = DriverWorkflowStore.demo()
        server_module.QA_SERVICE = QaService(
            context_filter=server_module.build_demo_business_context_filter()
        )

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

    def test_alert_routes_process_close_and_filter_audited_status(self) -> None:
        list_request = Request(
            f"{self.base_url}/api/alerts?status=pending",
            headers=DISPATCHER_AUTH_HEADERS,
        )
        with urlopen(list_request, timeout=3) as response:
            listed = json.loads(response.read().decode("utf-8"))

        self.assertEqual(response.status, 200)
        self.assertEqual(listed["count"], 1)
        self.assertEqual(listed["alerts"][0]["alertId"], "alert-demo-box-001")

        process_request = Request(
            f"{self.base_url}/api/alerts/alert-demo-box-001/process",
            data=b"{}",
            headers={
                **DISPATCHER_AUTH_HEADERS,
                "Content-Type": "application/json",
            },
            method="POST",
        )
        with urlopen(process_request, timeout=3) as response:
            processed = json.loads(response.read().decode("utf-8"))

        self.assertEqual(response.status, 200)
        self.assertEqual(processed["alert"]["status"], "processing")
        self.assertEqual(processed["alert"]["handledByUserId"], "dispatcher-demo")
        self.assertIn("handledAt", processed["alert"])

        close_request = Request(
            f"{self.base_url}/api/alerts/alert-demo-box-001/close",
            data=json.dumps({"closeReason": "Driver confirmed cargo is secured."}).encode(
                "utf-8"
            ),
            headers={
                **DISPATCHER_AUTH_HEADERS,
                "Content-Type": "application/json",
            },
            method="POST",
        )
        with urlopen(close_request, timeout=3) as response:
            closed = json.loads(response.read().decode("utf-8"))

        self.assertEqual(response.status, 200)
        self.assertEqual(closed["alert"]["status"], "closed")
        self.assertEqual(closed["alert"]["closedByUserId"], "dispatcher-demo")
        self.assertEqual(
            closed["alert"]["closeReason"],
            "Driver confirmed cargo is secured.",
        )

        with self.assertRaises(HTTPError) as context:
            urlopen(close_request, timeout=3)

        error = context.exception
        payload = json.loads(error.read().decode("utf-8"))
        self.assertEqual(error.code, 409)
        self.assertEqual(payload["error"], "alert_state_conflict")

    def test_alert_detail_and_dispatch_command_route_return_command_chain(self) -> None:
        detail_request = Request(
            f"{self.base_url}/api/alerts/alert-demo-box-001",
            headers=DISPATCHER_AUTH_HEADERS,
        )
        with urlopen(detail_request, timeout=3) as response:
            detail = json.loads(response.read().decode("utf-8"))

        self.assertEqual(response.status, 200)
        self.assertEqual(detail["alert"]["alertId"], "alert-demo-box-001")
        self.assertEqual(detail["alert"]["chain"]["dispatchCommandCount"], 1)
        self.assertEqual(
            detail["alert"]["dispatchCommands"][0]["status"],
            "acknowledged",
        )

        create_request = Request(
            f"{self.base_url}/api/alerts/alert-demo-box-001/dispatch-commands",
            data=json.dumps(
                {
                    "content": "Call the driver and request seal inspection.",
                    "targetType": "driver",
                    "targetId": "driver-demo",
                }
            ).encode("utf-8"),
            headers={
                **DISPATCHER_AUTH_HEADERS,
                "Content-Type": "application/json",
            },
            method="POST",
        )
        with urlopen(create_request, timeout=3) as response:
            created = json.loads(response.read().decode("utf-8"))

        self.assertEqual(response.status, 201)
        self.assertEqual(created["alert"]["status"], "processing")
        self.assertEqual(created["dispatchCommand"]["status"], "sent")
        self.assertEqual(created["dispatchCommand"]["targetId"], "driver-demo")
        self.assertEqual(created["alert"]["chain"]["dispatchCommandCount"], 2)

        refreshed_request = Request(
            f"{self.base_url}/api/alerts/alert-demo-box-001",
            headers=DISPATCHER_AUTH_HEADERS,
        )
        with urlopen(refreshed_request, timeout=3) as response:
            refreshed = json.loads(response.read().decode("utf-8"))

        self.assertEqual(refreshed["alert"]["chain"]["dispatchCommandCount"], 2)

    def test_alert_routes_mark_false_positive_terminal_status(self) -> None:
        request = Request(
            f"{self.base_url}/api/alerts/alert-demo-box-001/false-positive",
            data=json.dumps({"reason": "Sensor health check showed a false open event."}).encode(
                "utf-8"
            ),
            headers={
                **DISPATCHER_AUTH_HEADERS,
                "Content-Type": "application/json",
            },
            method="POST",
        )

        with urlopen(request, timeout=3) as response:
            payload = json.loads(response.read().decode("utf-8"))

        self.assertEqual(response.status, 200)
        self.assertEqual(payload["alert"]["status"], "false_positive")
        self.assertEqual(
            payload["alert"]["closeReason"],
            "Sensor health check showed a false open event.",
        )

    def test_alert_routes_reject_unscoped_dispatcher_and_cargo_owner(self) -> None:
        unscoped_request = Request(
            f"{self.base_url}/api/alerts/alert-demo-box-001/process",
            data=b"{}",
            headers={
                **DISPATCHER_AUTH_HEADERS,
                "X-CargoFlow-Dispatch-Region-Ids": "north-china",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        with self.assertRaises(HTTPError) as context:
            urlopen(unscoped_request, timeout=3)

        error = context.exception
        payload = json.loads(error.read().decode("utf-8"))
        self.assertEqual(error.code, 403)
        self.assertEqual(payload["error"], "alert_access_denied")

        owner_request = Request(
            f"{self.base_url}/api/alerts",
            headers=DEMO_AUTH_HEADERS,
        )
        with self.assertRaises(HTTPError) as context:
            urlopen(owner_request, timeout=3)

        error = context.exception
        payload = json.loads(error.read().decode("utf-8"))
        self.assertEqual(error.code, 403)
        self.assertEqual(payload["error"], "alert_access_denied")

    def test_dispatch_vehicle_distribution_returns_scoped_map_payload(self) -> None:
        request = Request(
            f"{self.base_url}/api/dispatch/vehicle-distribution",
            headers=DISPATCHER_AUTH_HEADERS,
        )

        with urlopen(request, timeout=3) as response:
            payload = json.loads(response.read().decode("utf-8"))

        self.assertEqual(response.status, 200)
        self.assertEqual(payload["summary"]["total"], 4)
        self.assertEqual(payload["summary"]["online"], 2)
        self.assertEqual(payload["summary"]["inTransit"], 2)
        self.assertEqual(payload["summary"]["alerting"], 1)
        self.assertEqual(payload["count"], 4)
        self.assertEqual(payload["vehicles"][0]["vehicleId"], "vehicle-demo-001")
        self.assertEqual(payload["vehicles"][0]["latestLocation"]["longitude"], 121.52)
        self.assertTrue(payload["vehicles"][0]["alertSummary"]["hasActiveAlert"])

    def test_dispatch_vehicle_distribution_filters_alerting_vehicles(self) -> None:
        request = Request(
            f"{self.base_url}/api/dispatch/vehicle-distribution?status=alert",
            headers=DISPATCHER_AUTH_HEADERS,
        )

        with urlopen(request, timeout=3) as response:
            payload = json.loads(response.read().decode("utf-8"))

        self.assertEqual(response.status, 200)
        self.assertEqual(payload["filters"]["status"], "alert")
        self.assertEqual(payload["count"], 1)
        self.assertEqual(payload["vehicles"][0]["vehicleId"], "vehicle-demo-001")

    def test_dispatch_vehicle_distribution_rejects_unscoped_or_invalid_requests(self) -> None:
        unscoped_request = Request(
            f"{self.base_url}/api/dispatch/vehicle-distribution",
            headers={
                **DISPATCHER_AUTH_HEADERS,
                "X-CargoFlow-Dispatch-Region-Ids": "north-china",
            },
        )
        with urlopen(unscoped_request, timeout=3) as response:
            unscoped = json.loads(response.read().decode("utf-8"))

        self.assertEqual(response.status, 200)
        self.assertEqual(unscoped["count"], 0)
        self.assertEqual(unscoped["summary"]["total"], 0)

        owner_request = Request(
            f"{self.base_url}/api/dispatch/vehicle-distribution",
            headers=DEMO_AUTH_HEADERS,
        )
        with self.assertRaises(HTTPError) as context:
            urlopen(owner_request, timeout=3)

        error = context.exception
        payload = json.loads(error.read().decode("utf-8"))
        self.assertEqual(error.code, 403)
        self.assertEqual(payload["error"], "dispatch_distribution_access_denied")

        invalid_filter_request = Request(
            f"{self.base_url}/api/dispatch/vehicle-distribution?status=offline",
            headers=DISPATCHER_AUTH_HEADERS,
        )
        with self.assertRaises(HTTPError) as context:
            urlopen(invalid_filter_request, timeout=3)

        error = context.exception
        payload = json.loads(error.read().decode("utf-8"))
        self.assertEqual(error.code, 400)
        self.assertEqual(payload["error"], "invalid_distribution_filter")

    def test_driver_tasks_route_returns_assigned_task_commands_and_reports(self) -> None:
        request = Request(
            f"{self.base_url}/api/driver/tasks",
            headers=DRIVER_AUTH_HEADERS,
        )

        with urlopen(request, timeout=3) as response:
            payload = json.loads(response.read().decode("utf-8"))

        self.assertEqual(response.status, 200)
        self.assertEqual(payload["count"], 1)
        task = payload["tasks"][0]
        self.assertEqual(task["taskId"], "task-demo-001")
        self.assertEqual(task["driverUserId"], "driver-demo")
        self.assertEqual(task["transportStatus"], "loaded")
        self.assertEqual(task["commands"][0]["status"], "delivered")
        self.assertEqual(task["summary"]["unconfirmedCommandCount"], 1)
        self.assertEqual(task["statusReports"][0]["reportStatus"], "loaded")

    def test_driver_command_acknowledge_and_status_report_routes_update_task(self) -> None:
        acknowledge_request = Request(
            f"{self.base_url}/api/driver/commands/cmd-demo-box-001/acknowledge",
            data=b"{}",
            headers={
                **DRIVER_AUTH_HEADERS,
                "Content-Type": "application/json",
            },
            method="POST",
        )
        with urlopen(acknowledge_request, timeout=3) as response:
            acknowledged = json.loads(response.read().decode("utf-8"))

        self.assertEqual(response.status, 200)
        self.assertEqual(acknowledged["command"]["status"], "acknowledged")
        self.assertIn("confirmedAt", acknowledged["command"])

        report_request = Request(
            f"{self.base_url}/api/driver/tasks/task-demo-001/status-reports",
            data=json.dumps(
                {
                    "reportStatus": "in_transit",
                    "note": "Departed warehouse gate.",
                    "attachmentUrls": ["https://example.com/pod/gate-photo.jpg"],
                }
            ).encode("utf-8"),
            headers={
                **DRIVER_AUTH_HEADERS,
                "Content-Type": "application/json",
            },
            method="POST",
        )
        with urlopen(report_request, timeout=3) as response:
            reported = json.loads(response.read().decode("utf-8"))

        self.assertEqual(response.status, 201)
        self.assertEqual(reported["statusReport"]["reportStatus"], "in_transit")
        self.assertEqual(reported["task"]["transportStatus"], "in_transit")
        self.assertEqual(reported["task"]["summary"]["unconfirmedCommandCount"], 0)
        self.assertEqual(reported["task"]["statusReports"][-1]["note"], "Departed warehouse gate.")

    def test_driver_routes_reject_other_roles_and_other_drivers(self) -> None:
        owner_request = Request(
            f"{self.base_url}/api/driver/tasks",
            headers=DEMO_AUTH_HEADERS,
        )
        with self.assertRaises(HTTPError) as context:
            urlopen(owner_request, timeout=3)

        error = context.exception
        payload = json.loads(error.read().decode("utf-8"))
        self.assertEqual(error.code, 403)
        self.assertEqual(payload["error"], "driver_access_denied")

        other_driver_request = Request(
            f"{self.base_url}/api/driver/commands/cmd-demo-box-001/acknowledge",
            data=b"{}",
            headers={
                **DRIVER_AUTH_HEADERS,
                "X-CargoFlow-User-Id": "driver-other",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        with self.assertRaises(HTTPError) as context:
            urlopen(other_driver_request, timeout=3)

        error = context.exception
        payload = json.loads(error.read().decode("utf-8"))
        self.assertEqual(error.code, 403)
        self.assertEqual(payload["error"], "driver_access_denied")

    def test_alert_log_route_filters_and_returns_chain_for_system_admin(self) -> None:
        request = Request(
            (
                f"{self.base_url}/api/alert-logs"
                "?type=box_opened&severity=high&status=pending"
                "&vehicleId=vehicle-demo-001&cargoId=cargo-demo-001"
                "&triggeredFrom=2026-05-13T10:00:00%2B00:00"
                "&triggeredTo=2026-05-13T10:30:00%2B00:00"
            ),
            headers=SYSTEM_ADMIN_AUTH_HEADERS,
        )

        with urlopen(request, timeout=3) as response:
            payload = json.loads(response.read().decode("utf-8"))

        self.assertEqual(response.status, 200)
        self.assertEqual(payload["count"], 1)
        self.assertEqual(payload["filters"]["type"], "box_opened")
        log = payload["logs"][0]
        self.assertEqual(log["alertId"], "alert-demo-box-001")
        self.assertEqual(log["notifications"][0]["status"], "sent")
        self.assertEqual(
            log["dispatchCommands"][0]["status"],
            "acknowledged",
        )
        self.assertEqual(log["chain"]["notificationCount"], 1)
        self.assertEqual(log["chain"]["dispatchCommandCount"], 1)

    def test_alert_log_export_route_returns_json_export_payload(self) -> None:
        request = Request(
            f"{self.base_url}/api/alert-logs/export?status=pending",
            headers=SYSTEM_ADMIN_AUTH_HEADERS,
        )

        with urlopen(request, timeout=3) as response:
            payload = json.loads(response.read().decode("utf-8"))

        self.assertEqual(response.status, 200)
        self.assertEqual(payload["export"]["format"], "json")
        self.assertEqual(payload["export"]["fileName"], "cargoflow-alert-logs.json")
        self.assertEqual(payload["count"], 1)

    def test_alert_log_route_rejects_dispatcher(self) -> None:
        request = Request(
            f"{self.base_url}/api/alert-logs",
            headers=DISPATCHER_AUTH_HEADERS,
        )

        with self.assertRaises(HTTPError) as context:
            urlopen(request, timeout=3)

        error = context.exception
        payload = json.loads(error.read().decode("utf-8"))
        self.assertEqual(error.code, 403)
        self.assertEqual(payload["error"], "alert_access_denied")

    def test_qa_ask_creates_record_with_sources_and_business_refs(self) -> None:
        request = Request(
            f"{self.base_url}/api/qa/ask",
            data=json.dumps(
                {
                    "question": "我的货物现在有哪些运输记录？",
                    "sessionId": "session-http-qa",
                    "requestedIds": ["CGF-DEMO-001"],
                }
            ).encode("utf-8"),
            headers={
                **DEMO_AUTH_HEADERS,
                "Content-Type": "application/json",
            },
            method="POST",
        )

        with urlopen(request, timeout=3) as response:
            answer = json.loads(response.read().decode("utf-8"))

        self.assertEqual(response.status, 201)
        self.assertTrue(answer["recordId"].startswith("qa-"))
        self.assertIn("已按你的权限找到", answer["answer"])
        self.assertGreaterEqual(len(answer["businessRefs"]), 1)
        self.assertEqual(answer["authorization"]["principal"]["role"], "cargo_owner")
        self.assertEqual(answer["sessionId"], "session-http-qa")
        self.assertIsNotNone(answer["answeredAt"])

        list_request = Request(
            f"{self.base_url}/api/qa/records?sessionId=session-http-qa",
            headers=DEMO_AUTH_HEADERS,
        )
        with urlopen(list_request, timeout=3) as response:
            listed = json.loads(response.read().decode("utf-8"))

        self.assertEqual(response.status, 200)
        self.assertEqual(listed["count"], 1)
        self.assertEqual(listed["records"][0]["recordId"], answer["recordId"])

    def test_qa_ask_returns_rule_answer_with_knowledge_source(self) -> None:
        request = Request(
            f"{self.base_url}/api/qa/ask",
            data=json.dumps({"question": "偏航告警怎么判定？"}).encode("utf-8"),
            headers={
                **DEMO_AUTH_HEADERS,
                "Content-Type": "application/json",
            },
            method="POST",
        )

        with urlopen(request, timeout=3) as response:
            answer = json.loads(response.read().decode("utf-8"))

        self.assertEqual(response.status, 201)
        self.assertEqual(answer["confidence"], "high")
        self.assertEqual(answer["sources"][0]["type"], "knowledge_doc")
        self.assertEqual(answer["sources"][0]["section"], "FR-04 异常报警")

    def test_qa_records_feedback_and_scope_are_user_limited(self) -> None:
        create_request = Request(
            f"{self.base_url}/api/qa/ask",
            data=json.dumps({"question": "司机如何确认调度指令？"}).encode("utf-8"),
            headers={
                **DEMO_AUTH_HEADERS,
                "Content-Type": "application/json",
            },
            method="POST",
        )
        with urlopen(create_request, timeout=3) as response:
            created = json.loads(response.read().decode("utf-8"))

        feedback_request = Request(
            f"{self.base_url}/api/qa/records/{created['recordId']}/feedback",
            data=json.dumps({"feedback": "helpful"}).encode("utf-8"),
            headers={
                **DEMO_AUTH_HEADERS,
                "Content-Type": "application/json",
            },
            method="POST",
        )
        with urlopen(feedback_request, timeout=3) as response:
            updated = json.loads(response.read().decode("utf-8"))

        self.assertEqual(response.status, 200)
        self.assertEqual(updated["record"]["feedback"], "helpful")

        other_user_request = Request(
            f"{self.base_url}/api/qa/records/{created['recordId']}",
            headers={**DEMO_AUTH_HEADERS, "X-CargoFlow-User-Id": "owner-other"},
        )
        with self.assertRaises(HTTPError) as context:
            urlopen(other_user_request, timeout=3)

        error = context.exception
        payload = json.loads(error.read().decode("utf-8"))
        self.assertEqual(error.code, 403)
        self.assertEqual(payload["error"], "qa_access_denied")

    def test_qa_ask_refuses_unauthorized_business_context(self) -> None:
        request = Request(
            f"{self.base_url}/api/qa/ask",
            data=json.dumps(
                {
                    "question": "帮我看看别人的货物",
                    "requestedIds": ["CGF-PENDING-001"],
                }
            ).encode("utf-8"),
            headers={
                **DEMO_AUTH_HEADERS,
                "Content-Type": "application/json",
            },
            method="POST",
        )

        with urlopen(request, timeout=3) as response:
            answer = json.loads(response.read().decode("utf-8"))

        self.assertEqual(response.status, 201)
        self.assertEqual(answer["failureReason"], "unauthorized_business_context")
        self.assertEqual(answer["businessRefs"], [])
        self.assertIn("不能查看", answer["answer"])

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
        self.assertEqual(created["vehicle"]["warehouseId"], "warehouse-shanghai")
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
            f"{self.base_url}/api/vehicles/vehicle-http-001/unbind",
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

    def test_vehicle_routes_filter_and_reject_out_of_scope_warehouse(self) -> None:
        other_warehouse_headers = {
            **WAREHOUSE_AUTH_HEADERS,
            "X-CargoFlow-User-Id": "warehouse-other",
            "X-CargoFlow-Warehouse-Ids": "warehouse-other",
        }
        list_request = Request(
            f"{self.base_url}/api/vehicles",
            headers=other_warehouse_headers,
        )
        with urlopen(list_request, timeout=3) as response:
            listed = json.loads(response.read().decode("utf-8"))

        self.assertEqual(response.status, 200)
        self.assertEqual(listed["count"], 0)

        patch_request = Request(
            f"{self.base_url}/api/vehicles/vehicle-demo-001",
            data=json.dumps({"plateNumber": "SH-OTHER-1"}).encode("utf-8"),
            headers={
                **other_warehouse_headers,
                "Content-Type": "application/json",
            },
            method="PATCH",
        )

        with self.assertRaises(HTTPError) as context:
            urlopen(patch_request, timeout=3)

        error = context.exception
        payload = json.loads(error.read().decode("utf-8"))
        self.assertEqual(error.code, 403)
        self.assertEqual(payload["error"], "vehicle_scope_denied")

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

    def test_vehicle_routes_reject_unbind_with_active_task(self) -> None:
        unbind_request = Request(
            f"{self.base_url}/api/vehicles/vehicle-demo-001/unbind",
            data=json.dumps({"reason": "attempt during active task"}).encode("utf-8"),
            headers={
                **WAREHOUSE_AUTH_HEADERS,
                "Content-Type": "application/json",
            },
            method="POST",
        )

        with self.assertRaises(HTTPError) as context:
            urlopen(unbind_request, timeout=3)

        error = context.exception
        payload = json.loads(error.read().decode("utf-8"))
        self.assertEqual(error.code, 409)
        self.assertEqual(payload["error"], "vehicle_has_active_task")
        self.assertIn("active transport task", payload["message"])

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

    def test_full_tracking_acceptance_flow_through_owner_signoff(self) -> None:
        create_vehicle_request = Request(
            f"{self.base_url}/api/vehicles",
            data=json.dumps(
                {
                    "vehicleId": "vehicle-acceptance-001",
                    "vehicleNumber": "VH-ACCEPT-001",
                    "plateNumber": "SH-ACCEPT-001",
                    "deviceId": "gps-acceptance-001",
                    "driverUserId": "driver-acceptance",
                }
            ).encode("utf-8"),
            headers={
                **WAREHOUSE_AUTH_HEADERS,
                "Content-Type": "application/json",
            },
            method="POST",
        )
        with urlopen(create_vehicle_request, timeout=3) as response:
            created_vehicle = json.loads(response.read().decode("utf-8"))

        self.assertEqual(response.status, 201)
        self.assertEqual(created_vehicle["vehicle"]["bindingStatus"], "available")

        bind_request = Request(
            f"{self.base_url}/api/cargo-bindings",
            data=json.dumps(
                {
                    "cargoId": "cargo-pending-001",
                    "vehicleId": "vehicle-acceptance-001",
                    "taskId": "task-acceptance-001",
                    "taskNumber": "TASK-ACCEPT-001",
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
        self.assertEqual(binding["binding"]["shipmentId"], "CGF-PENDING-001")
        self.assertEqual(binding["binding"]["transportStatus"], "bound")
        self.assertEqual(binding["binding"]["vehicle"]["bindingStatus"], "bound")

        driver_headers = {
            "X-CargoFlow-User-Id": "driver-acceptance",
            "X-CargoFlow-Role": "driver",
            "X-CargoFlow-Tenant-Id": "cgf-demo",
        }
        driver_tasks_request = Request(
            f"{self.base_url}/api/driver/tasks",
            headers=driver_headers,
        )
        with urlopen(driver_tasks_request, timeout=3) as response:
            driver_tasks = json.loads(response.read().decode("utf-8"))

        self.assertEqual(response.status, 200)
        self.assertEqual(driver_tasks["count"], 1)
        self.assertEqual(driver_tasks["tasks"][0]["taskId"], "task-acceptance-001")

        event_request = Request(
            f"{self.base_url}/api/device-events",
            data=json.dumps(
                {
                    "eventId": "evt-acceptance-gps-1",
                    "eventType": "gps",
                    "deviceId": "gps-acceptance-001",
                    "taskId": "task-acceptance-001",
                    "occurredAt": "2026-05-13T10:20:00+00:00",
                    "reportedAt": "2026-05-13T10:20:02+00:00",
                    "schemaVersion": 1,
                    "longitude": 121.35,
                    "latitude": 31.18,
                    "speedKph": 52,
                }
            ).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urlopen(event_request, timeout=3) as response:
            event = json.loads(response.read().decode("utf-8"))

        self.assertEqual(response.status, 202)
        self.assertTrue(event["latestLocationUpdated"])

        owner_beta_headers = {
            "X-CargoFlow-User-Id": "owner-beta",
            "X-CargoFlow-Role": "cargo_owner",
            "X-CargoFlow-Tenant-Id": "cgf-demo",
        }
        latest_request = Request(
            f"{self.base_url}/api/shipments/CGF-PENDING-001/latest-location",
            headers=owner_beta_headers,
        )
        with urlopen(latest_request, timeout=3) as response:
            latest = json.loads(response.read().decode("utf-8"))

        self.assertEqual(response.status, 200)
        self.assertEqual(latest["transportStatus"], "bound")
        self.assertEqual(latest["latestLocation"]["eventId"], "evt-acceptance-gps-1")

        eta_request = Request(
            f"{self.base_url}/api/shipments/CGF-PENDING-001/eta",
            headers=owner_beta_headers,
        )
        with urlopen(eta_request, timeout=3) as response:
            eta = json.loads(response.read().decode("utf-8"))

        self.assertEqual(response.status, 200)
        self.assertEqual(eta["eta"]["status"], "available")
        self.assertGreater(eta["eta"]["remainingDistanceKm"], 0)

        in_transit_request = Request(
            f"{self.base_url}/api/driver/tasks/task-acceptance-001/status-reports",
            data=json.dumps(
                {
                    "reportStatus": "in_transit",
                    "note": "Departed after cargo loading.",
                }
            ).encode("utf-8"),
            headers={
                **driver_headers,
                "Content-Type": "application/json",
            },
            method="POST",
        )
        with urlopen(in_transit_request, timeout=3) as response:
            in_transit = json.loads(response.read().decode("utf-8"))

        self.assertEqual(response.status, 201)
        self.assertEqual(in_transit["task"]["transportStatus"], "in_transit")

        trajectory_request = Request(
            f"{self.base_url}/api/shipments/CGF-PENDING-001/trajectory",
            headers=owner_beta_headers,
        )
        with urlopen(trajectory_request, timeout=3) as response:
            trajectory = json.loads(response.read().decode("utf-8"))

        self.assertEqual(response.status, 200)
        kinds = [point["kind"] for point in trajectory["trajectory"]]
        self.assertIn("gps", kinds)
        self.assertIn("status_report", kinds)
        self.assertTrue(trajectory["summary"]["hasStartPoint"])
        self.assertTrue(trajectory["summary"]["hasEndPoint"])

        delivered_request = Request(
            f"{self.base_url}/api/driver/tasks/task-acceptance-001/status-reports",
            data=json.dumps(
                {
                    "reportStatus": "delivered",
                    "note": "Cargo delivered to consignee dock.",
                    "attachmentUrls": ["https://example.com/pod/acceptance.jpg"],
                }
            ).encode("utf-8"),
            headers={
                **driver_headers,
                "Content-Type": "application/json",
            },
            method="POST",
        )
        with urlopen(delivered_request, timeout=3) as response:
            delivered = json.loads(response.read().decode("utf-8"))

        self.assertEqual(response.status, 201)
        self.assertEqual(delivered["task"]["transportStatus"], "delivered")
        self.assertEqual(delivered["statusReport"]["reportStatus"], "delivered")

        delivered_latest_request = Request(
            f"{self.base_url}/api/shipments/CGF-PENDING-001/latest-location",
            headers=owner_beta_headers,
        )
        with urlopen(delivered_latest_request, timeout=3) as response:
            delivered_latest = json.loads(response.read().decode("utf-8"))

        self.assertEqual(response.status, 200)
        self.assertEqual(delivered_latest["transportStatus"], "delivered")

        sign_request = Request(
            f"{self.base_url}/api/shipments/CGF-PENDING-001/sign",
            data=b"{}",
            headers={
                **owner_beta_headers,
                "Content-Type": "application/json",
            },
            method="POST",
        )
        with urlopen(sign_request, timeout=3) as response:
            signed = json.loads(response.read().decode("utf-8"))

        self.assertEqual(response.status, 200)
        self.assertEqual(signed["shipment"]["transportStatus"], "signed")
        self.assertEqual(signed["shipment"]["signedByUserId"], "owner-beta")

        signed_driver_tasks_request = Request(
            f"{self.base_url}/api/driver/tasks",
            headers=driver_headers,
        )
        with urlopen(signed_driver_tasks_request, timeout=3) as response:
            signed_driver_tasks = json.loads(response.read().decode("utf-8"))

        self.assertEqual(response.status, 200)
        self.assertEqual(signed_driver_tasks["count"], 0)

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
