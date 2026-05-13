"""Create core CargoFlow domain tables.

Revision ID: 20260513_0001
Revises:
Create Date: 2026-05-13
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260513_0001"
down_revision = None
branch_labels = None
depends_on = None


TASK_STATUSES = (
    "pending_binding",
    "bound",
    "loaded",
    "in_transit",
    "delivered",
    "signed",
    "canceled",
)
VEHICLE_ONLINE_STATUSES = ("offline", "online", "delayed")
VEHICLE_BINDING_STATUSES = ("available", "bound", "disabled")
ALERT_TYPES = ("route_deviation", "abnormal_stop", "box_opened")
ALERT_SEVERITIES = ("low", "medium", "high")
ALERT_STATUSES = ("pending", "processing", "closed", "false_positive")
COMMAND_STATUSES = (
    "pending_delivery",
    "sent",
    "delivered",
    "acknowledged",
    "failed",
    "revoked",
)
COMMAND_TARGET_TYPES = ("driver", "vehicle")
STATUS_REPORT_STATES = ("loaded", "in_transit", "delivered")
QA_FEEDBACK = ("helpful", "not_helpful")


def _check_values(column_name: str, values: tuple[str, ...]) -> str:
    allowed = ", ".join(f"'{value}'" for value in values)
    return f"{column_name} IN ({allowed})"


def upgrade() -> None:
    op.create_table(
        "cargos",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("cargo_number", sa.String(length=64), nullable=False),
        sa.Column("owner_user_id", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=160), nullable=False),
        sa.Column("origin", sa.String(length=255), nullable=False),
        sa.Column("destination", sa.String(length=255), nullable=False),
        sa.Column("planned_departure_at", sa.DateTime(timezone=True)),
        sa.Column("planned_arrival_at", sa.DateTime(timezone=True)),
        sa.Column(
            "current_status",
            sa.String(length=32),
            nullable=False,
            server_default="pending_binding",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint(
            _check_values("current_status", TASK_STATUSES),
            name="ck_cargos_current_status",
        ),
        sa.UniqueConstraint("cargo_number", name="uq_cargos_cargo_number"),
    )
    op.create_index("ix_cargos_owner_user_id", "cargos", ["owner_user_id"])

    op.create_table(
        "vehicles",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("vehicle_number", sa.String(length=64), nullable=False),
        sa.Column("plate_number", sa.String(length=32), nullable=False),
        sa.Column("device_id", sa.String(length=128), nullable=False),
        sa.Column("driver_user_id", sa.String(length=64)),
        sa.Column(
            "online_status",
            sa.String(length=32),
            nullable=False,
            server_default="offline",
        ),
        sa.Column(
            "binding_status",
            sa.String(length=32),
            nullable=False,
            server_default="available",
        ),
        sa.Column("last_seen_at", sa.DateTime(timezone=True)),
        sa.Column("notes", sa.Text()),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint(
            _check_values("online_status", VEHICLE_ONLINE_STATUSES),
            name="ck_vehicles_online_status",
        ),
        sa.CheckConstraint(
            _check_values("binding_status", VEHICLE_BINDING_STATUSES),
            name="ck_vehicles_binding_status",
        ),
        sa.UniqueConstraint("vehicle_number", name="uq_vehicles_vehicle_number"),
        sa.UniqueConstraint("plate_number", name="uq_vehicles_plate_number"),
        sa.UniqueConstraint("device_id", name="uq_vehicles_device_id"),
    )
    op.create_index("ix_vehicles_driver_user_id", "vehicles", ["driver_user_id"])

    op.create_table(
        "transport_tasks",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("task_number", sa.String(length=64), nullable=False),
        sa.Column(
            "cargo_id",
            sa.String(length=36),
            sa.ForeignKey("cargos.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "vehicle_id",
            sa.String(length=36),
            sa.ForeignKey("vehicles.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("driver_user_id", sa.String(length=64), nullable=False),
        sa.Column("origin", sa.String(length=255), nullable=False),
        sa.Column("destination", sa.String(length=255), nullable=False),
        sa.Column("planned_route", sa.JSON()),
        sa.Column(
            "status",
            sa.String(length=32),
            nullable=False,
            server_default="bound",
        ),
        sa.Column("planned_departure_at", sa.DateTime(timezone=True)),
        sa.Column("planned_arrival_at", sa.DateTime(timezone=True)),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint(
            _check_values("status", TASK_STATUSES),
            name="ck_transport_tasks_status",
        ),
        sa.UniqueConstraint("task_number", name="uq_transport_tasks_task_number"),
    )
    op.create_index("ix_transport_tasks_cargo_id", "transport_tasks", ["cargo_id"])
    op.create_index("ix_transport_tasks_vehicle_id", "transport_tasks", ["vehicle_id"])
    op.create_index(
        "ix_transport_tasks_driver_user_id",
        "transport_tasks",
        ["driver_user_id"],
    )
    op.create_index(
        "uq_transport_tasks_active_cargo",
        "transport_tasks",
        ["cargo_id"],
        unique=True,
        postgresql_where=sa.text("status NOT IN ('signed', 'canceled')"),
    )
    op.create_index(
        "uq_transport_tasks_active_vehicle",
        "transport_tasks",
        ["vehicle_id"],
        unique=True,
        postgresql_where=sa.text("status NOT IN ('signed', 'canceled')"),
    )

    op.create_table(
        "location_points",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column(
            "task_id",
            sa.String(length=36),
            sa.ForeignKey("transport_tasks.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "vehicle_id",
            sa.String(length=36),
            sa.ForeignKey("vehicles.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("device_id", sa.String(length=128), nullable=False),
        sa.Column("longitude", sa.Numeric(precision=10, scale=7), nullable=False),
        sa.Column("latitude", sa.Numeric(precision=9, scale=7), nullable=False),
        sa.Column("speed_kph", sa.Numeric(precision=8, scale=2)),
        sa.Column("heading_degrees", sa.Numeric(precision=6, scale=2)),
        sa.Column("captured_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("reported_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("event_id", sa.String(length=128)),
        sa.Column("raw_payload", sa.JSON()),
        sa.CheckConstraint(
            "longitude BETWEEN -180 AND 180",
            name="ck_location_points_longitude",
        ),
        sa.CheckConstraint(
            "latitude BETWEEN -90 AND 90",
            name="ck_location_points_latitude",
        ),
        sa.CheckConstraint(
            "speed_kph IS NULL OR speed_kph >= 0",
            name="ck_location_points_speed",
        ),
        sa.CheckConstraint(
            "heading_degrees IS NULL OR (heading_degrees >= 0 AND heading_degrees < 360)",
            name="ck_location_points_heading",
        ),
        sa.UniqueConstraint("event_id", name="uq_location_points_event_id"),
    )
    op.create_index(
        "ix_location_points_task_captured_at",
        "location_points",
        ["task_id", "captured_at"],
    )
    op.create_index(
        "ix_location_points_vehicle_captured_at",
        "location_points",
        ["vehicle_id", "captured_at"],
    )
    op.create_index("ix_location_points_device_id", "location_points", ["device_id"])

    op.create_table(
        "alerts",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("alert_number", sa.String(length=64), nullable=False),
        sa.Column(
            "task_id",
            sa.String(length=36),
            sa.ForeignKey("transport_tasks.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "cargo_id",
            sa.String(length=36),
            sa.ForeignKey("cargos.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "vehicle_id",
            sa.String(length=36),
            sa.ForeignKey("vehicles.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("alert_type", sa.String(length=32), nullable=False),
        sa.Column("severity", sa.String(length=16), nullable=False),
        sa.Column(
            "status",
            sa.String(length=32),
            nullable=False,
            server_default="pending",
        ),
        sa.Column("longitude", sa.Numeric(precision=10, scale=7)),
        sa.Column("latitude", sa.Numeric(precision=9, scale=7)),
        sa.Column("triggered_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("handled_by_user_id", sa.String(length=64)),
        sa.Column("handled_at", sa.DateTime(timezone=True)),
        sa.Column("closed_by_user_id", sa.String(length=64)),
        sa.Column("closed_at", sa.DateTime(timezone=True)),
        sa.Column("close_reason", sa.Text()),
        sa.Column("latest_evidence", sa.JSON()),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint(
            _check_values("alert_type", ALERT_TYPES),
            name="ck_alerts_alert_type",
        ),
        sa.CheckConstraint(
            _check_values("severity", ALERT_SEVERITIES),
            name="ck_alerts_severity",
        ),
        sa.CheckConstraint(
            _check_values("status", ALERT_STATUSES),
            name="ck_alerts_status",
        ),
        sa.CheckConstraint(
            "longitude IS NULL OR longitude BETWEEN -180 AND 180",
            name="ck_alerts_longitude",
        ),
        sa.CheckConstraint(
            "latitude IS NULL OR latitude BETWEEN -90 AND 90",
            name="ck_alerts_latitude",
        ),
        sa.UniqueConstraint("alert_number", name="uq_alerts_alert_number"),
    )
    op.create_index("ix_alerts_task_status", "alerts", ["task_id", "status"])
    op.create_index("ix_alerts_vehicle_status", "alerts", ["vehicle_id", "status"])
    op.create_index("ix_alerts_type_status", "alerts", ["alert_type", "status"])
    op.create_index(
        "uq_alerts_open_task_type",
        "alerts",
        ["task_id", "alert_type"],
        unique=True,
        postgresql_where=sa.text("status IN ('pending', 'processing')"),
    )

    op.create_table(
        "dispatch_commands",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("command_number", sa.String(length=64), nullable=False),
        sa.Column(
            "task_id",
            sa.String(length=36),
            sa.ForeignKey("transport_tasks.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("alert_id", sa.String(length=36), sa.ForeignKey("alerts.id")),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("created_by_user_id", sa.String(length=64), nullable=False),
        sa.Column("target_type", sa.String(length=16), nullable=False),
        sa.Column("target_id", sa.String(length=128), nullable=False),
        sa.Column(
            "status",
            sa.String(length=32),
            nullable=False,
            server_default="pending_delivery",
        ),
        sa.Column("issued_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("delivered_at", sa.DateTime(timezone=True)),
        sa.Column("confirmed_at", sa.DateTime(timezone=True)),
        sa.Column("failed_at", sa.DateTime(timezone=True)),
        sa.Column("revoked_at", sa.DateTime(timezone=True)),
        sa.Column("failure_reason", sa.Text()),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint(
            _check_values("target_type", COMMAND_TARGET_TYPES),
            name="ck_dispatch_commands_target_type",
        ),
        sa.CheckConstraint(
            _check_values("status", COMMAND_STATUSES),
            name="ck_dispatch_commands_status",
        ),
        sa.UniqueConstraint(
            "command_number",
            name="uq_dispatch_commands_command_number",
        ),
    )
    op.create_index(
        "ix_dispatch_commands_task_status",
        "dispatch_commands",
        ["task_id", "status"],
    )
    op.create_index(
        "ix_dispatch_commands_target",
        "dispatch_commands",
        ["target_type", "target_id"],
    )

    op.create_table(
        "status_reports",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column(
            "task_id",
            sa.String(length=36),
            sa.ForeignKey("transport_tasks.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("report_status", sa.String(length=32), nullable=False),
        sa.Column("reporter_user_id", sa.String(length=64), nullable=False),
        sa.Column("note", sa.Text()),
        sa.Column("attachment_urls", sa.JSON()),
        sa.Column("reported_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint(
            _check_values("report_status", STATUS_REPORT_STATES),
            name="ck_status_reports_report_status",
        ),
    )
    op.create_index(
        "ix_status_reports_task_reported_at",
        "status_reports",
        ["task_id", "reported_at"],
    )
    op.create_index("ix_status_reports_reporter", "status_reports", ["reporter_user_id"])

    op.create_table(
        "qa_records",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("user_id", sa.String(length=64), nullable=False),
        sa.Column("session_id", sa.String(length=128)),
        sa.Column("question", sa.Text(), nullable=False),
        sa.Column("answer", sa.Text()),
        sa.Column("citations", sa.JSON()),
        sa.Column("related_cargo_id", sa.String(length=36), sa.ForeignKey("cargos.id")),
        sa.Column(
            "related_task_id",
            sa.String(length=36),
            sa.ForeignKey("transport_tasks.id"),
        ),
        sa.Column("asked_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("answered_at", sa.DateTime(timezone=True)),
        sa.Column("feedback", sa.String(length=32)),
        sa.Column("failure_reason", sa.Text()),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint(
            "feedback IS NULL OR " + _check_values("feedback", QA_FEEDBACK),
            name="ck_qa_records_feedback",
        ),
    )
    op.create_index("ix_qa_records_user_session", "qa_records", ["user_id", "session_id"])
    op.create_index("ix_qa_records_related_cargo", "qa_records", ["related_cargo_id"])
    op.create_index("ix_qa_records_related_task", "qa_records", ["related_task_id"])


def downgrade() -> None:
    op.drop_table("qa_records")
    op.drop_table("status_reports")
    op.drop_table("dispatch_commands")
    op.drop_table("alerts")
    op.drop_table("location_points")
    op.drop_table("transport_tasks")
    op.drop_table("vehicles")
    op.drop_table("cargos")
