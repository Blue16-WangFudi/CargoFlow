"""Alert handling workflow for CargoFlow's current API skeleton."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import UTC, datetime
from http import HTTPStatus
from typing import Any, Mapping

from cargoflow_api.access_control import Principal, Role
from cargoflow_api.alert_rules import AlertRuleStore, alert_to_wire
from cargoflow_api.domain import Alert, AlertSeverity, AlertStatus, AlertType


class AlertHandlingError(Exception):
    """Raised when an alert handling request cannot be completed."""

    def __init__(self, error_code: str, message: str, status: HTTPStatus) -> None:
        super().__init__(message)
        self.error_code = error_code
        self.message = message
        self.status = status


class AlertValidationError(AlertHandlingError):
    def __init__(self, message: str) -> None:
        super().__init__("invalid_alert_action", message, HTTPStatus.BAD_REQUEST)


class AlertAuthorizationError(AlertHandlingError):
    def __init__(self, message: str) -> None:
        super().__init__("alert_access_denied", message, HTTPStatus.FORBIDDEN)


class AlertConflictError(AlertHandlingError):
    def __init__(self, message: str) -> None:
        super().__init__("alert_state_conflict", message, HTTPStatus.CONFLICT)


class AlertNotFoundError(AlertHandlingError):
    def __init__(self, alert_id: str) -> None:
        super().__init__(
            "alert_not_found",
            f"No alert found for {alert_id}.",
            HTTPStatus.NOT_FOUND,
        )


@dataclass(frozen=True, slots=True)
class AlertScope:
    tenant_id: str
    dispatch_region_ids: tuple[str, ...]
    warehouse_ids: tuple[str, ...] = ()


class AlertHandlingStore:
    """In-memory alert handling state until CargoFlow persistence is wired."""

    def __init__(
        self,
        alert_store: AlertRuleStore,
        *,
        task_scopes: Mapping[str, AlertScope],
    ) -> None:
        self.alert_store = alert_store
        self._task_scopes = dict(task_scopes)

    @classmethod
    def demo(cls, alert_store: AlertRuleStore | None = None) -> "AlertHandlingStore":
        store = alert_store or AlertRuleStore()
        demo_scope = AlertScope(
            tenant_id="cgf-demo",
            dispatch_region_ids=("east-china",),
            warehouse_ids=("warehouse-shanghai",),
        )
        if store.get_alert("alert-demo-box-001") is None:
            triggered_at = _parse_datetime("2026-05-13T10:12:00+00:00")
            store.save_alert(
                Alert(
                    id="alert-demo-box-001",
                    alert_number="ALR-DEMO-BOX-001",
                    task_id="task-demo-001",
                    cargo_id="cargo-demo-001",
                    vehicle_id="vehicle-demo-001",
                    alert_type=AlertType.BOX_OPENED,
                    severity=AlertSeverity.HIGH,
                    longitude=121.52,
                    latitude=31.26,
                    status=AlertStatus.PENDING,
                    triggered_at=triggered_at,
                    latest_evidence={
                        "eventType": "box_opened",
                        "reason": "Unauthorized box opening during active transport.",
                        "ruleRegion": "box-security",
                    },
                    created_at=triggered_at,
                    updated_at=triggered_at,
                )
            )
        return cls(store, task_scopes={"task-demo-001": demo_scope})

    def register_task_scope(self, task_id: str, scope: AlertScope) -> None:
        if not task_id.strip():
            raise AlertValidationError("taskId must be a non-empty string.")
        self._task_scopes[task_id] = scope

    def list_alerts(
        self,
        principal: Principal,
        *,
        status: str | None = None,
    ) -> list[Alert]:
        _require_alert_handler_role(principal)
        status_filter = _status_from_wire(status) if status else None
        alerts = [
            alert
            for alert in self.alert_store.alerts()
            if (status_filter is None or alert.status is status_filter)
            and self._can_access(principal, alert)
        ]
        return sorted(alerts, key=lambda alert: alert.triggered_at, reverse=True)

    def get_alert(self, alert_id: str, principal: Principal) -> Alert:
        alert = self._alert_for(alert_id)
        self._require_alert_access(principal, alert)
        return alert

    def start_processing(
        self,
        alert_id: str,
        principal: Principal,
        *,
        now: datetime | None = None,
    ) -> Alert:
        alert = self._alert_for(alert_id)
        self._require_alert_access(principal, alert)
        if not alert.is_open:
            raise AlertConflictError("Terminal alerts cannot be moved to processing.")
        if alert.status is AlertStatus.PROCESSING and alert.handled_by_user_id:
            return alert

        handled_at = _utc_now(now)
        updated = replace(
            alert,
            status=AlertStatus.PROCESSING,
            handled_by_user_id=principal.user_id,
            handled_at=handled_at,
            updated_at=handled_at,
        )
        return self.alert_store.save_alert(updated)

    def close_alert(
        self,
        alert_id: str,
        payload: Mapping[str, Any],
        principal: Principal,
        *,
        now: datetime | None = None,
    ) -> Alert:
        return self._finish_alert(
            alert_id,
            payload,
            principal,
            terminal_status=AlertStatus.CLOSED,
            now=now,
        )

    def mark_false_positive(
        self,
        alert_id: str,
        payload: Mapping[str, Any],
        principal: Principal,
        *,
        now: datetime | None = None,
    ) -> Alert:
        return self._finish_alert(
            alert_id,
            payload,
            principal,
            terminal_status=AlertStatus.FALSE_POSITIVE,
            now=now,
        )

    def _finish_alert(
        self,
        alert_id: str,
        payload: Mapping[str, Any],
        principal: Principal,
        *,
        terminal_status: AlertStatus,
        now: datetime | None,
    ) -> Alert:
        alert = self._alert_for(alert_id)
        self._require_alert_access(principal, alert)
        if not alert.is_open:
            raise AlertConflictError("Terminal alerts cannot be closed again.")

        reason = _required_reason(payload)
        closed_at = _utc_now(now)
        updated = replace(
            alert,
            status=terminal_status,
            handled_by_user_id=alert.handled_by_user_id or principal.user_id,
            handled_at=alert.handled_at or closed_at,
            closed_by_user_id=principal.user_id,
            closed_at=closed_at,
            close_reason=reason,
            updated_at=closed_at,
        )
        return self.alert_store.save_alert(updated)

    def _alert_for(self, alert_id: str) -> Alert:
        alert = self.alert_store.get_alert(alert_id)
        if alert is None:
            raise AlertNotFoundError(alert_id)
        return alert

    def _require_alert_access(self, principal: Principal, alert: Alert) -> None:
        _require_alert_handler_role(principal)
        if not self._can_access(principal, alert):
            raise AlertAuthorizationError(
                "Only scoped dispatchers and system admins can handle this alert."
            )

    def _can_access(self, principal: Principal, alert: Alert) -> bool:
        scope = self._task_scopes.get(alert.task_id)
        if scope is None:
            return False
        if principal.tenant_id != scope.tenant_id:
            return False
        if principal.role is Role.SYSTEM_ADMIN:
            return True
        return principal.role is Role.DISPATCHER and _intersects(
            principal.dispatch_region_ids,
            scope.dispatch_region_ids,
        )


def alerts_to_wire(alerts: list[Alert]) -> dict[str, Any]:
    return {
        "alerts": [alert_to_wire(alert) for alert in alerts],
        "count": len(alerts),
    }


def _require_alert_handler_role(principal: Principal) -> None:
    if principal.role not in {Role.DISPATCHER, Role.SYSTEM_ADMIN}:
        raise AlertAuthorizationError(
            "Only dispatchers and system admins can handle alerts."
        )


def _required_reason(payload: Mapping[str, Any]) -> str:
    value = _value(payload, "closeReason", default=None)
    if value is None:
        value = _value(payload, "reason", default=None)
    if not isinstance(value, str) or not value.strip():
        raise AlertValidationError("closeReason must be a non-empty string.")
    return value.strip()


def _status_from_wire(value: str) -> AlertStatus:
    normalized = value.strip().lower().replace("-", "_")
    try:
        return AlertStatus(normalized)
    except ValueError as exc:
        allowed = ", ".join(status.value for status in AlertStatus)
        raise AlertValidationError(f"status must be one of: {allowed}.") from exc


def _value(payload: Mapping[str, Any], name: str, default: Any = ...) -> Any:
    if name in payload:
        return payload[name]
    snake_name = _camel_to_snake(name)
    if snake_name in payload:
        return payload[snake_name]
    if default is ...:
        raise AlertValidationError(f"Missing required field: {name}.")
    return default


def _camel_to_snake(value: str) -> str:
    output: list[str] = []
    for character in value:
        if character.isupper():
            output.append("_")
            output.append(character.lower())
        else:
            output.append(character)
    return "".join(output).lstrip("_")


def _intersects(left: tuple[str, ...], right: tuple[str, ...]) -> bool:
    return bool(set(left).intersection(right))


def _utc_now(value: datetime | None = None) -> datetime:
    value = value or datetime.now(UTC)
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC).replace(microsecond=0)


def _parse_datetime(value: str) -> datetime:
    return _utc_now(datetime.fromisoformat(value))
