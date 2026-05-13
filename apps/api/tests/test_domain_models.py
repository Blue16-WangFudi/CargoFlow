from __future__ import annotations

import pathlib
import unittest
from datetime import UTC, datetime

from cargoflow_api.domain import (
    Alert,
    AlertSeverity,
    AlertStatus,
    AlertType,
    DispatchCommand,
    DispatchCommandStatus,
    DispatchTargetType,
    LocationPoint,
    QaRecord,
    StatusReportState,
    TransportTask,
    TransportTaskStatus,
)


class DomainModelTests(unittest.TestCase):
    def test_terminal_task_statuses_do_not_accept_location_updates(self) -> None:
        active = TransportTask(
            id="task-1",
            task_number="T-001",
            cargo_id="cargo-1",
            vehicle_id="vehicle-1",
            driver_user_id="driver-1",
            origin="Shanghai",
            destination="Suzhou",
            status=TransportTaskStatus.IN_TRANSIT,
        )
        signed = TransportTask(
            id="task-2",
            task_number="T-002",
            cargo_id="cargo-2",
            vehicle_id="vehicle-2",
            driver_user_id="driver-2",
            origin="Shanghai",
            destination="Hangzhou",
            status=TransportTaskStatus.SIGNED,
        )

        self.assertTrue(active.accepts_location_updates)
        self.assertFalse(signed.accepts_location_updates)

    def test_location_point_validates_gps_bounds(self) -> None:
        captured_at = datetime(2026, 5, 13, 8, tzinfo=UTC)

        point = LocationPoint(
            id="loc-1",
            task_id="task-1",
            vehicle_id="vehicle-1",
            device_id="gps-1",
            longitude=121.4737,
            latitude=31.2304,
            captured_at=captured_at,
            reported_at=captured_at,
        )

        self.assertEqual(point.device_id, "gps-1")
        with self.assertRaises(ValueError):
            LocationPoint(
                id="loc-2",
                task_id="task-1",
                vehicle_id="vehicle-1",
                device_id="gps-1",
                longitude=181,
                latitude=31.2304,
                captured_at=captured_at,
                reported_at=captured_at,
            )

    def test_alert_and_command_open_terminal_helpers(self) -> None:
        alert = Alert(
            id="alert-1",
            alert_number="A-001",
            task_id="task-1",
            cargo_id="cargo-1",
            vehicle_id="vehicle-1",
            alert_type=AlertType.ROUTE_DEVIATION,
            severity=AlertSeverity.MEDIUM,
            status=AlertStatus.PROCESSING,
        )
        command = DispatchCommand(
            id="cmd-1",
            command_number="D-001",
            task_id="task-1",
            content="Check route deviation",
            created_by_user_id="dispatcher-1",
            target_type=DispatchTargetType.DRIVER,
            target_id="driver-1",
            status=DispatchCommandStatus.ACKNOWLEDGED,
        )

        self.assertTrue(alert.is_open)
        self.assertTrue(command.is_terminal)

    def test_status_reports_have_prd_order(self) -> None:
        self.assertEqual(
            StatusReportState.ordered_values(),
            ("loaded", "in_transit", "delivered"),
        )

    def test_qa_record_requires_question(self) -> None:
        with self.assertRaises(ValueError):
            QaRecord(id="qa-1", user_id="owner-1", question=" ")


class MigrationCoverageTests(unittest.TestCase):
    def test_initial_migration_covers_core_domain_tables(self) -> None:
        root = pathlib.Path(__file__).resolve().parents[1]
        migration = root / "migrations" / "versions" / "20260513_0001_core_domain_models.py"
        text = migration.read_text(encoding="utf-8")

        for table_name in (
            "cargos",
            "vehicles",
            "transport_tasks",
            "location_points",
            "alerts",
            "dispatch_commands",
            "status_reports",
            "qa_records",
        ):
            self.assertIn(f'"{table_name}"', text)

        self.assertIn("uq_transport_tasks_active_cargo", text)
        self.assertIn("uq_transport_tasks_active_vehicle", text)
        self.assertIn("uq_alerts_open_task_type", text)


if __name__ == "__main__":
    unittest.main()
