from __future__ import annotations

import unittest
from dataclasses import replace
from datetime import UTC, datetime

from cargoflow_api.alert_rules import (
    AlertRuleEngine,
    AlertRuleStore,
    RoutePoint,
    TaskAlertContext,
)
from cargoflow_api.domain import AlertSeverity, AlertStatus, AlertType, TransportTaskStatus
from cargoflow_api.location_ingest import DeviceEventType, LatestLocationSnapshot


def location(
    event_id: str,
    *,
    captured_at: datetime,
    longitude: float = 121.0,
    latitude: float = 31.02,
    speed_kph: float | None = 40,
) -> LatestLocationSnapshot:
    return LatestLocationSnapshot(
        task_id="task-1",
        vehicle_id="vehicle-1",
        device_id="gps-1",
        longitude=longitude,
        latitude=latitude,
        captured_at=captured_at,
        reported_at=captured_at,
        event_id=event_id,
        speed_kph=speed_kph,
    )


class AlertRuleEngineTests(unittest.TestCase):
    def setUp(self) -> None:
        self.store = AlertRuleStore()
        self.engine = AlertRuleEngine(self.store)
        self.context = TaskAlertContext(
            task_id="task-1",
            cargo_id="cargo-1",
            vehicle_id="vehicle-1",
            status=TransportTaskStatus.IN_TRANSIT,
            route=(RoutePoint(121.0, 31.0), RoutePoint(121.1, 31.0)),
        )

    def test_route_deviation_requires_threshold_duration_and_merges_open_alert(self) -> None:
        first = self.engine.evaluate_location(
            self.context,
            location(
                "evt-gps-1",
                captured_at=datetime(2026, 5, 13, 10, 0, tzinfo=UTC),
            ),
        )
        second = self.engine.evaluate_location(
            self.context,
            location(
                "evt-gps-2",
                captured_at=datetime(2026, 5, 13, 10, 3, tzinfo=UTC),
                longitude=121.01,
                latitude=31.02,
            ),
        )
        third = self.engine.evaluate_location(
            self.context,
            location(
                "evt-gps-3",
                captured_at=datetime(2026, 5, 13, 10, 5, tzinfo=UTC),
                longitude=121.02,
                latitude=31.02,
            ),
        )

        self.assertEqual(first, ())
        self.assertEqual(len(second), 1)
        self.assertEqual(second[0].alert_type, AlertType.ROUTE_DEVIATION)
        self.assertEqual(second[0].severity, AlertSeverity.MEDIUM)
        self.assertEqual(second[0].triggered_at, datetime(2026, 5, 13, 10, 0, tzinfo=UTC))
        self.assertEqual(second[0].latest_evidence["durationSeconds"], 180)
        self.assertEqual(len(third), 1)
        self.assertEqual(third[0].id, second[0].id)
        self.assertEqual(third[0].latest_evidence["eventId"], "evt-gps-3")
        self.assertEqual(third[0].latest_evidence["ruleRegion"], "route-segment:0")
        self.assertEqual(len(self.store.open_alerts("task-1")), 1)

    def test_route_deviation_keeps_open_alerts_separate_by_route_region(self) -> None:
        multi_segment_context = TaskAlertContext(
            task_id="task-1",
            cargo_id="cargo-1",
            vehicle_id="vehicle-1",
            status=TransportTaskStatus.IN_TRANSIT,
            route=(
                RoutePoint(121.0, 31.0),
                RoutePoint(121.1, 31.0),
                RoutePoint(121.1, 31.1),
            ),
        )

        self.engine.evaluate_location(
            multi_segment_context,
            location(
                "evt-dev-seg0-1",
                captured_at=datetime(2026, 5, 13, 10, 0, tzinfo=UTC),
                longitude=121.05,
                latitude=31.02,
            ),
        )
        first_region_alert = self.engine.evaluate_location(
            multi_segment_context,
            location(
                "evt-dev-seg0-2",
                captured_at=datetime(2026, 5, 13, 10, 3, tzinfo=UTC),
                longitude=121.05,
                latitude=31.02,
            ),
        )[0]
        self.engine.evaluate_location(
            multi_segment_context,
            location(
                "evt-dev-seg1-1",
                captured_at=datetime(2026, 5, 13, 10, 5, tzinfo=UTC),
                longitude=121.12,
                latitude=31.05,
            ),
        )
        second_region_alert = self.engine.evaluate_location(
            multi_segment_context,
            location(
                "evt-dev-seg1-2",
                captured_at=datetime(2026, 5, 13, 10, 8, tzinfo=UTC),
                longitude=121.12,
                latitude=31.05,
            ),
        )[0]

        self.assertNotEqual(first_region_alert.id, second_region_alert.id)
        self.assertEqual(
            first_region_alert.latest_evidence["ruleRegion"],
            "route-segment:0",
        )
        self.assertEqual(
            second_region_alert.latest_evidence["ruleRegion"],
            "route-segment:1",
        )
        self.assertEqual(len(self.store.open_alerts("task-1")), 2)

    def test_abnormal_stop_triggers_after_default_stop_threshold(self) -> None:
        self.engine.evaluate_location(
            self.context,
            location(
                "evt-stop-1",
                captured_at=datetime(2026, 5, 13, 10, 0, tzinfo=UTC),
                longitude=121.05,
                latitude=31.0,
                speed_kph=2,
            ),
        )
        alerts = self.engine.evaluate_location(
            self.context,
            location(
                "evt-stop-2",
                captured_at=datetime(2026, 5, 13, 10, 30, tzinfo=UTC),
                longitude=121.0501,
                latitude=31.0,
                speed_kph=0,
            ),
        )

        self.assertEqual(len(alerts), 1)
        self.assertEqual(alerts[0].alert_type, AlertType.ABNORMAL_STOP)
        self.assertEqual(alerts[0].severity, AlertSeverity.MEDIUM)
        self.assertEqual(alerts[0].latest_evidence["durationSeconds"], 1800)
        self.assertEqual(alerts[0].latest_evidence["ruleRegion"], "grid:121.05:31.00")

    def test_abnormal_stop_near_authorized_stop_does_not_trigger_alert(self) -> None:
        authorized_context = TaskAlertContext(
            task_id="task-1",
            cargo_id="cargo-1",
            vehicle_id="vehicle-1",
            status=TransportTaskStatus.IN_TRANSIT,
            route=(),
            authorized_stops=(RoutePoint(121.05, 31.02),),
        )

        self.engine.evaluate_location(
            authorized_context,
            location(
                "evt-authorized-stop-1",
                captured_at=datetime(2026, 5, 13, 10, 0, tzinfo=UTC),
                longitude=121.05,
                latitude=31.02,
                speed_kph=0,
            ),
        )
        alerts = self.engine.evaluate_location(
            authorized_context,
            location(
                "evt-authorized-stop-2",
                captured_at=datetime(2026, 5, 13, 10, 45, tzinfo=UTC),
                longitude=121.0501,
                latitude=31.0201,
                speed_kph=0,
            ),
        )

        self.assertEqual(alerts, ())
        self.assertEqual(self.store.open_alerts("task-1"), ())

    def test_box_open_event_triggers_high_severity_alert_for_active_transport(self) -> None:
        alerts = self.engine.evaluate_security_event(
            self.context,
            event_type=DeviceEventType.BOX_OPENED,
            event_id="evt-box-1",
            occurred_at=datetime(2026, 5, 13, 10, 8, tzinfo=UTC),
        )

        self.assertEqual(len(alerts), 1)
        self.assertEqual(alerts[0].alert_type, AlertType.BOX_OPENED)
        self.assertEqual(alerts[0].severity, AlertSeverity.HIGH)

    def test_closed_alert_does_not_receive_future_merge_updates(self) -> None:
        first = self.engine.evaluate_security_event(
            self.context,
            event_type=DeviceEventType.BOX_OPENED,
            event_id="evt-box-1",
            occurred_at=datetime(2026, 5, 13, 10, 8, tzinfo=UTC),
        )[0]
        self.store.save_alert(
            replace(
                first,
                status=AlertStatus.CLOSED,
                close_reason="Handled by dispatcher.",
            )
        )

        second = self.engine.evaluate_security_event(
            self.context,
            event_type=DeviceEventType.BOX_OPENED,
            event_id="evt-box-2",
            occurred_at=datetime(2026, 5, 13, 10, 9, tzinfo=UTC),
        )

        self.assertEqual(len(second), 1)
        self.assertNotEqual(second[0].id, first.id)

    def test_terminal_tasks_do_not_trigger_new_alerts(self) -> None:
        signed_context = TaskAlertContext(
            task_id="task-1",
            cargo_id="cargo-1",
            vehicle_id="vehicle-1",
            status=TransportTaskStatus.SIGNED,
            route=self.context.route,
        )

        alerts = self.engine.evaluate_security_event(
            signed_context,
            event_type=DeviceEventType.BOX_OPENED,
            event_id="evt-box-1",
            occurred_at=datetime(2026, 5, 13, 10, 8, tzinfo=UTC),
        )

        self.assertEqual(alerts, ())

    def test_pending_binding_tasks_do_not_trigger_new_alerts(self) -> None:
        pending_context = TaskAlertContext(
            task_id="task-1",
            cargo_id="cargo-1",
            vehicle_id="vehicle-1",
            status=TransportTaskStatus.PENDING_BINDING,
            route=self.context.route,
        )

        alerts = self.engine.evaluate_security_event(
            pending_context,
            event_type=DeviceEventType.BOX_OPENED,
            event_id="evt-box-1",
            occurred_at=datetime(2026, 5, 13, 10, 8, tzinfo=UTC),
        )

        self.assertEqual(alerts, ())


if __name__ == "__main__":
    unittest.main()
