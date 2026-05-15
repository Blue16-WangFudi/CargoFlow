"""Alert handling workflow for CargoFlow's current API skeleton."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import UTC, datetime
from http import HTTPStatus
from typing import Any, Mapping
from uuid import uuid4

from cargoflow_api.access_control import Principal, Role
from cargoflow_api.alert_rules import AlertRuleStore, alert_to_wire
from cargoflow_api.domain import (
    Alert,
    AlertSeverity,
    AlertStatus,
    AlertType,
    DispatchCommand,
    DispatchCommandStatus,
    DispatchTargetType,
)


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


@dataclass(frozen=True, slots=True)
class AlertNotificationRecord:
    id: str
    alert_id: str
    channel: str
    recipient_user_id: str
    status: str
    sent_at: datetime
    template: str


@dataclass(frozen=True, slots=True)
class AlertLogFilters:
    alert_type: AlertType | None = None
    severity: AlertSeverity | None = None
    status: AlertStatus | None = None
    vehicle_id: str | None = None
    cargo_id: str | None = None
    triggered_from: datetime | None = None
    triggered_to: datetime | None = None


class AlertHandlingStore:
    """In-memory alert handling state until CargoFlow persistence is wired."""

    def __init__(
        self,
        alert_store: AlertRuleStore,
        *,
        task_scopes: Mapping[str, AlertScope],
        notifications: tuple[AlertNotificationRecord, ...] = (),
        dispatch_commands: tuple[DispatchCommand, ...] = (),
    ) -> None:
        self.alert_store = alert_store
        self._task_scopes = dict(task_scopes)
        self._notifications_by_alert: dict[str, list[AlertNotificationRecord]] = {}
        self._commands_by_alert: dict[str, list[DispatchCommand]] = {}
        for notification in notifications:
            self.add_notification(notification)
        for command in dispatch_commands:
            self.add_dispatch_command(command)

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
        notification = AlertNotificationRecord(
            id="notice-demo-box-001",
            alert_id="alert-demo-box-001",
            channel="in_app",
            recipient_user_id="dispatcher-demo",
            status="sent",
            sent_at=_parse_datetime("2026-05-13T10:12:05+00:00"),
            template="alert_high_priority",
        )
        command = DispatchCommand(
            id="cmd-demo-box-001",
            command_number="CMD-DEMO-BOX-001",
            task_id="task-demo-001",
            alert_id="alert-demo-box-001",
            content="Inspect cargo seal and confirm box status.",
            created_by_user_id="dispatcher-demo",
            target_type=DispatchTargetType.DRIVER,
            target_id="driver-demo",
            status=DispatchCommandStatus.ACKNOWLEDGED,
            issued_at=_parse_datetime("2026-05-13T10:13:00+00:00"),
            delivered_at=_parse_datetime("2026-05-13T10:13:10+00:00"),
            confirmed_at=_parse_datetime("2026-05-13T10:14:00+00:00"),
        )
        return cls(
            store,
            task_scopes={"task-demo-001": demo_scope},
            notifications=(notification,),
            dispatch_commands=(command,),
        )

    def register_task_scope(self, task_id: str, scope: AlertScope) -> None:
        if not task_id.strip():
            raise AlertValidationError("taskId must be a non-empty string.")
        self._task_scopes[task_id] = scope

    def add_notification(self, notification: AlertNotificationRecord) -> None:
        self._notifications_by_alert.setdefault(notification.alert_id, []).append(
            notification
        )

    def add_dispatch_command(self, command: DispatchCommand) -> None:
        if command.alert_id is None:
            return
        self._commands_by_alert.setdefault(command.alert_id, []).append(command)

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

    def alert_detail_payload(self, alert_id: str, principal: Principal) -> dict[str, Any]:
        alert = self.get_alert(alert_id, principal)
        return _alert_detail_to_wire(alert, self)

    def query_alert_logs(
        self,
        principal: Principal,
        filters: AlertLogFilters | None = None,
    ) -> dict[str, Any]:
        _require_alert_log_role(principal)
        filters = filters or AlertLogFilters()
        alerts = [
            alert
            for alert in self.alert_store.alerts()
            if self._can_access(principal, alert)
            and _matches_alert_filters(alert, filters)
        ]
        alerts.sort(key=lambda alert: alert.triggered_at, reverse=True)
        return alert_logs_to_wire(alerts, self, filters)

    def export_alert_logs(
        self,
        principal: Principal,
        filters: AlertLogFilters | None = None,
    ) -> dict[str, Any]:
        payload = self.query_alert_logs(principal, filters)
        payload["export"] = {
            "format": "json",
            "generatedAt": _utc_now().isoformat(),
            "fileName": "cargoflow-alert-logs.json",
        }
        return payload

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

    def create_dispatch_command(
        self,
        alert_id: str,
        payload: Mapping[str, Any],
        principal: Principal,
        *,
        now: datetime | None = None,
    ) -> tuple[Alert, DispatchCommand]:
        alert = self._alert_for(alert_id)
        self._require_alert_access(principal, alert)
        if not alert.is_open:
            raise AlertConflictError("Terminal alerts cannot receive dispatch commands.")

        issued_at = _utc_now(now)
        command = DispatchCommand(
            id=f"cmd-{uuid4().hex}",
            command_number=_next_command_number(alert.id, self, issued_at),
            task_id=alert.task_id,
            alert_id=alert.id,
            content=_required_text(payload, "content"),
            created_by_user_id=principal.user_id,
            target_type=_target_type_from_wire(
                _value(payload, "targetType", default=DispatchTargetType.DRIVER.value)
            ),
            target_id=_required_text(payload, "targetId"),
            status=DispatchCommandStatus.SENT,
            issued_at=issued_at,
        )
        self.add_dispatch_command(command)

        if alert.status is AlertStatus.PENDING:
            alert = self.alert_store.save_alert(
                replace(
                    alert,
                    status=AlertStatus.PROCESSING,
                    handled_by_user_id=principal.user_id,
                    handled_at=issued_at,
                    updated_at=issued_at,
                )
            )
        return alert, command

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

    def _notification_records(self, alert_id: str) -> tuple[AlertNotificationRecord, ...]:
        return tuple(
            sorted(
                self._notifications_by_alert.get(alert_id, ()),
                key=lambda notification: notification.sent_at,
            )
        )

    def _dispatch_commands(self, alert_id: str) -> tuple[DispatchCommand, ...]:
        return tuple(
            sorted(
                self._commands_by_alert.get(alert_id, ()),
                key=lambda command: command.issued_at,
            )
        )


def alerts_to_wire(alerts: list[Alert]) -> dict[str, Any]:
    return {
        "alerts": [alert_to_wire(alert) for alert in alerts],
        "count": len(alerts),
    }


def alert_logs_to_wire(
    alerts: list[Alert],
    store: AlertHandlingStore,
    filters: AlertLogFilters,
) -> dict[str, Any]:
    return {
        "filters": _alert_log_filters_to_wire(filters),
        "logs": [_alert_log_to_wire(alert, store) for alert in alerts],
        "count": len(alerts),
    }


def alert_log_filters_from_query(params: Mapping[str, str]) -> AlertLogFilters:
    return AlertLogFilters(
        alert_type=_alert_type_from_wire(params["type"]) if "type" in params else None,
        severity=(
            _severity_from_wire(params["severity"]) if "severity" in params else None
        ),
        status=_status_from_wire(params["status"]) if "status" in params else None,
        vehicle_id=_optional_text(params.get("vehicleId") or params.get("vehicle_id")),
        cargo_id=_optional_text(params.get("cargoId") or params.get("cargo_id")),
        triggered_from=(
            _datetime_from_query(params["triggeredFrom"])
            if "triggeredFrom" in params
            else None
        ),
        triggered_to=(
            _datetime_from_query(params["triggeredTo"])
            if "triggeredTo" in params
            else None
        ),
    )


def _alert_log_to_wire(alert: Alert, store: AlertHandlingStore) -> dict[str, Any]:
    payload = _alert_detail_to_wire(alert, store)
    payload["chain"]["hasClosedAudit"] = (
        alert.closed_at is not None and alert.close_reason is not None
    )
    return payload


def _alert_detail_to_wire(alert: Alert, store: AlertHandlingStore) -> dict[str, Any]:
    payload = alert_to_wire(alert)
    payload["notifications"] = [
        _notification_to_wire(notification)
        for notification in store._notification_records(alert.id)
    ]
    payload["dispatchCommands"] = [
        _dispatch_command_to_wire(command) for command in store._dispatch_commands(alert.id)
    ]
    payload["chain"] = {
        "notificationCount": len(payload["notifications"]),
        "dispatchCommandCount": len(payload["dispatchCommands"]),
    }
    return payload


def _notification_to_wire(notification: AlertNotificationRecord) -> dict[str, Any]:
    return {
        "notificationId": notification.id,
        "alertId": notification.alert_id,
        "channel": notification.channel,
        "recipientUserId": notification.recipient_user_id,
        "status": notification.status,
        "sentAt": notification.sent_at.isoformat(),
        "template": notification.template,
    }


def _dispatch_command_to_wire(command: DispatchCommand) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "commandId": command.id,
        "commandNumber": command.command_number,
        "taskId": command.task_id,
        "alertId": command.alert_id,
        "content": command.content,
        "createdByUserId": command.created_by_user_id,
        "targetType": command.target_type.value,
        "targetId": command.target_id,
        "status": command.status.value,
        "issuedAt": command.issued_at.isoformat(),
    }
    if command.delivered_at is not None:
        payload["deliveredAt"] = command.delivered_at.isoformat()
    if command.confirmed_at is not None:
        payload["confirmedAt"] = command.confirmed_at.isoformat()
    if command.failed_at is not None:
        payload["failedAt"] = command.failed_at.isoformat()
    if command.revoked_at is not None:
        payload["revokedAt"] = command.revoked_at.isoformat()
    if command.failure_reason is not None:
        payload["failureReason"] = command.failure_reason
    return payload


def dispatch_command_to_wire(command: DispatchCommand) -> dict[str, Any]:
    return _dispatch_command_to_wire(command)


def _alert_log_filters_to_wire(filters: AlertLogFilters) -> dict[str, Any]:
    payload: dict[str, Any] = {}
    if filters.alert_type is not None:
        payload["type"] = filters.alert_type.value
    if filters.severity is not None:
        payload["severity"] = filters.severity.value
    if filters.status is not None:
        payload["status"] = filters.status.value
    if filters.vehicle_id is not None:
        payload["vehicleId"] = filters.vehicle_id
    if filters.cargo_id is not None:
        payload["cargoId"] = filters.cargo_id
    if filters.triggered_from is not None:
        payload["triggeredFrom"] = filters.triggered_from.isoformat()
    if filters.triggered_to is not None:
        payload["triggeredTo"] = filters.triggered_to.isoformat()
    return payload


def _matches_alert_filters(alert: Alert, filters: AlertLogFilters) -> bool:
    if filters.alert_type is not None and alert.alert_type is not filters.alert_type:
        return False
    if filters.severity is not None and alert.severity is not filters.severity:
        return False
    if filters.status is not None and alert.status is not filters.status:
        return False
    if filters.vehicle_id is not None and alert.vehicle_id != filters.vehicle_id:
        return False
    if filters.cargo_id is not None and alert.cargo_id != filters.cargo_id:
        return False
    if filters.triggered_from is not None and alert.triggered_at < filters.triggered_from:
        return False
    if filters.triggered_to is not None and alert.triggered_at > filters.triggered_to:
        return False
    return True


def _require_alert_handler_role(principal: Principal) -> None:
    if principal.role not in {Role.DISPATCHER, Role.SYSTEM_ADMIN}:
        raise AlertAuthorizationError(
            "Only dispatchers and system admins can handle alerts."
        )


def _require_alert_log_role(principal: Principal) -> None:
    if principal.role is not Role.SYSTEM_ADMIN:
        raise AlertAuthorizationError(
            "Only system admins can query and export alert logs."
        )


def _required_reason(payload: Mapping[str, Any]) -> str:
    value = _value(payload, "closeReason", default=None)
    if value is None:
        value = _value(payload, "reason", default=None)
    return _required_text({"closeReason": value}, "closeReason")


def _required_text(payload: Mapping[str, Any], name: str) -> str:
    value = _value(payload, name, default=None)
    if not isinstance(value, str) or not value.strip():
        raise AlertValidationError(f"{name} must be a non-empty string.")
    return value.strip()


def _status_from_wire(value: str) -> AlertStatus:
    normalized = value.strip().lower().replace("-", "_")
    try:
        return AlertStatus(normalized)
    except ValueError as exc:
        allowed = ", ".join(status.value for status in AlertStatus)
        raise AlertValidationError(f"status must be one of: {allowed}.") from exc


def _alert_type_from_wire(value: str) -> AlertType:
    normalized = value.strip().lower().replace("-", "_")
    try:
        return AlertType(normalized)
    except ValueError as exc:
        allowed = ", ".join(alert_type.value for alert_type in AlertType)
        raise AlertValidationError(f"type must be one of: {allowed}.") from exc


def _severity_from_wire(value: str) -> AlertSeverity:
    normalized = value.strip().lower().replace("-", "_")
    try:
        return AlertSeverity(normalized)
    except ValueError as exc:
        allowed = ", ".join(severity.value for severity in AlertSeverity)
        raise AlertValidationError(f"severity must be one of: {allowed}.") from exc


def _target_type_from_wire(value: str) -> DispatchTargetType:
    normalized = value.strip().lower().replace("-", "_")
    try:
        return DispatchTargetType(normalized)
    except ValueError as exc:
        allowed = ", ".join(target_type.value for target_type in DispatchTargetType)
        raise AlertValidationError(f"targetType must be one of: {allowed}.") from exc


def _next_command_number(
    alert_id: str,
    store: AlertHandlingStore,
    issued_at: datetime,
) -> str:
    sequence = len(store._dispatch_commands(alert_id)) + 1
    return f"CMD-{issued_at:%Y%m%d}-{sequence:03d}"


def _datetime_from_query(value: str) -> datetime:
    if not value.strip():
        raise AlertValidationError("time filters must not be blank.")
    try:
        return _utc_now(datetime.fromisoformat(value.strip()))
    except ValueError as exc:
        raise AlertValidationError("time filters must use ISO 8601 format.") from exc


def _optional_text(value: str | None) -> str | None:
    if value is None:
        return None
    stripped = value.strip()
    return stripped or None


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
