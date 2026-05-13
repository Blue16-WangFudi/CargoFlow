from __future__ import annotations

import unittest
from datetime import UTC, datetime

from cargoflow_api.access_control import Principal, Role, ShipmentScope
from cargoflow_api.domain import StatusReportState, TransportTaskStatus
from cargoflow_api.driver_status_reporting import (
    DriverStatusReportAuthorizationError,
    DriverStatusReportConflictError,
    DriverStatusReportValidationError,
    ensure_forward_transition,
    parse_driver_status_report_payload,
    require_assigned_driver_report_access,
)


class DriverStatusReportPayloadTests(unittest.TestCase):
    def test_parses_status_note_reported_time_and_attachments(self) -> None:
        payload = parse_driver_status_report_payload(
            {
                "reportStatus": "已装货",
                "reportedAt": "2026-05-13T10:15:00Z",
                "note": "Loaded at dock 3",
                "attachmentUrls": [
                    "https://files.example.com/loading-photo.jpg",
                    "https://files.example.com/seal-photo.jpg",
                ],
            },
            now=datetime(2026, 5, 13, 10, tzinfo=UTC),
        )

        self.assertEqual(payload.report_status, StatusReportState.LOADED)
        self.assertEqual(
            payload.reported_at,
            datetime(2026, 5, 13, 10, 15, tzinfo=UTC),
        )
        self.assertEqual(payload.note, "Loaded at dock 3")
        self.assertEqual(len(payload.attachment_urls), 2)

    def test_defaults_reported_time_to_now(self) -> None:
        payload = parse_driver_status_report_payload(
            {"reportStatus": "in_transit"},
            now=datetime(2026, 5, 13, 11, 30, 15, tzinfo=UTC),
        )

        self.assertEqual(payload.report_status, StatusReportState.IN_TRANSIT)
        self.assertEqual(
            payload.reported_at,
            datetime(2026, 5, 13, 11, 30, 15, tzinfo=UTC),
        )

    def test_rejects_invalid_attachment_url(self) -> None:
        with self.assertRaises(DriverStatusReportValidationError):
            parse_driver_status_report_payload(
                {
                    "reportStatus": "loaded",
                    "attachmentUrls": ["not-a-url"],
                },
                now=datetime(2026, 5, 13, 10, tzinfo=UTC),
            )

    def test_rejects_unknown_status(self) -> None:
        with self.assertRaises(DriverStatusReportValidationError):
            parse_driver_status_report_payload(
                {"reportStatus": "signed"},
                now=datetime(2026, 5, 13, 10, tzinfo=UTC),
            )


class DriverStatusTransitionTests(unittest.TestCase):
    def test_accepts_only_next_forward_status(self) -> None:
        self.assertEqual(
            ensure_forward_transition(
                TransportTaskStatus.BOUND,
                StatusReportState.LOADED,
            ),
            TransportTaskStatus.LOADED,
        )
        self.assertEqual(
            ensure_forward_transition(
                TransportTaskStatus.LOADED,
                StatusReportState.IN_TRANSIT,
            ),
            TransportTaskStatus.IN_TRANSIT,
        )
        self.assertEqual(
            ensure_forward_transition(
                TransportTaskStatus.IN_TRANSIT,
                StatusReportState.DELIVERED,
            ),
            TransportTaskStatus.DELIVERED,
        )

    def test_rejects_repeated_skipped_and_terminal_statuses(self) -> None:
        for current, requested in (
            (TransportTaskStatus.LOADED, StatusReportState.LOADED),
            (TransportTaskStatus.BOUND, StatusReportState.IN_TRANSIT),
            (TransportTaskStatus.DELIVERED, StatusReportState.IN_TRANSIT),
            (TransportTaskStatus.SIGNED, StatusReportState.DELIVERED),
        ):
            with self.subTest(current=current, requested=requested):
                with self.assertRaises(DriverStatusReportConflictError):
                    ensure_forward_transition(current, requested)


class DriverStatusAccessTests(unittest.TestCase):
    def setUp(self) -> None:
        self.shipment = ShipmentScope(
            shipment_id="shipment-1",
            tenant_id="tenant-1",
            owner_user_id="owner-1",
            driver_user_id="driver-1",
            warehouse_ids=("warehouse-1",),
            dispatch_region_ids=("region-1",),
        )

    def test_allows_only_assigned_driver(self) -> None:
        require_assigned_driver_report_access(
            Principal("driver-1", Role.DRIVER, "tenant-1"),
            self.shipment,
        )

        denied = (
            Principal("driver-2", Role.DRIVER, "tenant-1"),
            Principal("driver-1", Role.DRIVER, "tenant-2"),
            Principal("owner-1", Role.CARGO_OWNER, "tenant-1"),
            Principal("admin-1", Role.SYSTEM_ADMIN, "tenant-1"),
        )
        for principal in denied:
            with self.subTest(principal=principal):
                with self.assertRaises(DriverStatusReportAuthorizationError):
                    require_assigned_driver_report_access(principal, self.shipment)


if __name__ == "__main__":
    unittest.main()
