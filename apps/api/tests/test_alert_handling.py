from __future__ import annotations

import unittest
from datetime import UTC, datetime

from cargoflow_api.access_control import Principal, Role
from cargoflow_api.alert_handling import (
    AlertLogFilters,
    AlertNotificationRecord,
    AlertConflictError,
    AlertHandlingStore,
    AlertScope,
)
from cargoflow_api.alert_rules import AlertRuleStore
from cargoflow_api.domain import (
    Alert,
    AlertSeverity,
    AlertStatus,
    AlertType,
    DispatchCommand,
    DispatchCommandStatus,
    DispatchTargetType,
)


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
        self.admin = Principal("admin-1", Role.SYSTEM_ADMIN, "tenant-1")

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

    def test_system_admin_queries_alert_logs_with_filters_and_chain(self) -> None:
        self.store.add_notification(
            AlertNotificationRecord(
                id="notice-1",
                alert_id="alert-1",
                channel="in_app",
                recipient_user_id="dispatcher-1",
                status="sent",
                sent_at=datetime(2026, 5, 13, 10, 1, tzinfo=UTC),
                template="alert_medium_priority",
            )
        )
        self.store.add_dispatch_command(
            DispatchCommand(
                id="cmd-1",
                command_number="CMD-001",
                task_id="task-1",
                alert_id="alert-1",
                content="Check the route deviation.",
                created_by_user_id="dispatcher-1",
                target_type=DispatchTargetType.DRIVER,
                target_id="driver-1",
                status=DispatchCommandStatus.SENT,
                issued_at=datetime(2026, 5, 13, 10, 2, tzinfo=UTC),
            )
        )
        self.alert_store.save_alert(
            Alert(
                id="alert-other",
                alert_number="ALR-OTHER",
                task_id="task-1",
                cargo_id="cargo-other",
                vehicle_id="vehicle-other",
                alert_type=AlertType.BOX_OPENED,
                severity=AlertSeverity.HIGH,
                status=AlertStatus.PENDING,
                triggered_at=datetime(2026, 5, 13, 11, 0, tzinfo=UTC),
            )
        )

        payload = self.store.query_alert_logs(
            self.admin,
            AlertLogFilters(
                alert_type=AlertType.ROUTE_DEVIATION,
                severity=AlertSeverity.MEDIUM,
                status=AlertStatus.PENDING,
                vehicle_id="vehicle-1",
                cargo_id="cargo-1",
                triggered_from=datetime(2026, 5, 13, 9, 59, tzinfo=UTC),
                triggered_to=datetime(2026, 5, 13, 10, 1, tzinfo=UTC),
            ),
        )

        self.assertEqual(payload["count"], 1)
        self.assertEqual(payload["filters"]["type"], "route_deviation")
        log = payload["logs"][0]
        self.assertEqual(log["alertId"], "alert-1")
        self.assertEqual(log["notifications"][0]["notificationId"], "notice-1")
        self.assertEqual(log["dispatchCommands"][0]["commandNumber"], "CMD-001")
        self.assertEqual(log["chain"]["notificationCount"], 1)
        self.assertEqual(log["chain"]["dispatchCommandCount"], 1)

    def test_export_alert_logs_includes_export_metadata(self) -> None:
        payload = self.store.export_alert_logs(self.admin)

        self.assertEqual(payload["export"]["format"], "json")
        self.assertEqual(payload["export"]["fileName"], "cargoflow-alert-logs.json")
        self.assertEqual(payload["count"], 1)


if __name__ == "__main__":
    unittest.main()
