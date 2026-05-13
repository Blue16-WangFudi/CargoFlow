from __future__ import annotations

import unittest
from datetime import UTC, datetime

from cargoflow_api.access_control import Principal, Role
from cargoflow_api.alert_handling import (
    AlertConflictError,
    AlertHandlingStore,
    AlertScope,
)
from cargoflow_api.alert_rules import AlertRuleStore
from cargoflow_api.domain import Alert, AlertSeverity, AlertStatus, AlertType


def make_alert() -> Alert:
    return Alert(
        id="alert-1",
        alert_number="ALR-001",
        task_id="task-1",
        cargo_id="cargo-1",
        vehicle_id="vehicle-1",
        alert_type=AlertType.ROUTE_DEVIATION,
        severity=AlertSeverity.MEDIUM,
        status=AlertStatus.PENDING,
        triggered_at=datetime(2026, 5, 13, 10, 0, tzinfo=UTC),
    )


class AlertHandlingStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.alert_store = AlertRuleStore((make_alert(),))
        self.store = AlertHandlingStore(
            self.alert_store,
            task_scopes={
                "task-1": AlertScope(
                    tenant_id="tenant-1",
                    dispatch_region_ids=("region-1",),
                )
            },
        )
        self.dispatcher = Principal(
            "dispatcher-1",
            Role.DISPATCHER,
            "tenant-1",
            dispatch_region_ids=("region-1",),
        )

    def test_process_and_close_alert_records_handler_and_close_audit(self) -> None:
        handled_at = datetime(2026, 5, 13, 10, 5, tzinfo=UTC)
        closed_at = datetime(2026, 5, 13, 10, 8, tzinfo=UTC)

        processing = self.store.start_processing(
            "alert-1",
            self.dispatcher,
            now=handled_at,
        )
        closed = self.store.close_alert(
            "alert-1",
            {"closeReason": "Driver confirmed route is back to normal."},
            self.dispatcher,
            now=closed_at,
        )

        self.assertEqual(processing.status, AlertStatus.PROCESSING)
        self.assertEqual(processing.handled_by_user_id, "dispatcher-1")
        self.assertEqual(closed.status, AlertStatus.CLOSED)
        self.assertEqual(closed.handled_at, handled_at)
        self.assertEqual(closed.closed_by_user_id, "dispatcher-1")
        self.assertEqual(closed.closed_at, closed_at)
        self.assertEqual(
            closed.close_reason,
            "Driver confirmed route is back to normal.",
        )
        self.assertEqual(self.alert_store.open_alerts("task-1"), ())

    def test_false_positive_is_terminal_and_cannot_be_closed_again(self) -> None:
        alert = self.store.mark_false_positive(
            "alert-1",
            {"reason": "Sensor reported stale open-box state."},
            self.dispatcher,
            now=datetime(2026, 5, 13, 10, 6, tzinfo=UTC),
        )

        self.assertEqual(alert.status, AlertStatus.FALSE_POSITIVE)
        self.assertEqual(alert.close_reason, "Sensor reported stale open-box state.")
        with self.assertRaises(AlertConflictError):
            self.store.close_alert(
                "alert-1",
                {"closeReason": "duplicate close"},
                self.dispatcher,
            )

    def test_list_alerts_filters_by_status_and_dispatch_scope(self) -> None:
        self.alert_store.save_alert(
            Alert(
                id="alert-2",
                alert_number="ALR-002",
                task_id="task-2",
                cargo_id="cargo-2",
                vehicle_id="vehicle-2",
                alert_type=AlertType.BOX_OPENED,
                severity=AlertSeverity.HIGH,
                status=AlertStatus.PENDING,
                triggered_at=datetime(2026, 5, 13, 10, 1, tzinfo=UTC),
            )
        )
        self.store.register_task_scope(
            "task-2",
            AlertScope(tenant_id="tenant-1", dispatch_region_ids=("region-2",)),
        )

        alerts = self.store.list_alerts(self.dispatcher, status="pending")

        self.assertEqual([alert.id for alert in alerts], ["alert-1"])


if __name__ == "__main__":
    unittest.main()
