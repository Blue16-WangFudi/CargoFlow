"""ETA calculation helpers for CargoFlow shipment tracking."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from math import asin, cos, radians, sin, sqrt
from typing import Any

from cargoflow_api.location_ingest import LatestLocationSnapshot


@dataclass(frozen=True, slots=True)
class Destination:
    name: str
    longitude: float
    latitude: float

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise ValueError("destination name must not be blank")
        if not -180 <= self.longitude <= 180:
            raise ValueError("destination longitude must be between -180 and 180")
        if not -90 <= self.latitude <= 90:
            raise ValueError("destination latitude must be between -90 and 90")

    def to_wire(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "longitude": self.longitude,
            "latitude": self.latitude,
        }


@dataclass(frozen=True, slots=True)
class EtaResult:
    status: str
    estimated_arrival_at: datetime | None
    remaining_distance_km: float | None
    updated_at: datetime | None
    calculated_at: datetime
    destination: Destination | None
    reason: str | None = None
    message: str | None = None
    provider: str = "offline_straight_line"
    average_speed_kph: float | None = None

    def to_wire(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "status": self.status,
            "estimatedArrival": (
                self.estimated_arrival_at.isoformat()
                if self.estimated_arrival_at is not None
                else None
            ),
            "remainingDistanceKm": self.remaining_distance_km,
            "updatedAt": self.updated_at.isoformat() if self.updated_at else None,
            "calculatedAt": self.calculated_at.isoformat(),
            "destination": self.destination.to_wire() if self.destination else None,
            "provider": self.provider,
        }
        if self.average_speed_kph is not None:
            payload["averageSpeedKph"] = self.average_speed_kph
        if self.reason is not None:
            payload["reason"] = self.reason
        if self.message is not None:
            payload["message"] = self.message
        return payload


class EtaService:
    """Deterministic offline ETA implementation for local development."""

    def __init__(self, average_speed_kph: float = 60.0) -> None:
        if average_speed_kph <= 0:
            raise ValueError("average_speed_kph must be positive")
        self.average_speed_kph = average_speed_kph

    def estimate(
        self,
        latest: LatestLocationSnapshot | None,
        destination: Destination | None,
        *,
        calculated_at: datetime | None = None,
    ) -> EtaResult:
        calculated_at = _as_utc(calculated_at or datetime.now(UTC))
        if latest is None:
            return self.unavailable(
                "missing_location",
                "ETA cannot be calculated until an accepted current location exists.",
                destination=destination,
                updated_at=None,
                calculated_at=calculated_at,
            )
        if destination is None:
            return self.unavailable(
                "missing_destination",
                "ETA cannot be calculated because the transport task has no destination.",
                destination=None,
                updated_at=latest.reported_at,
                calculated_at=calculated_at,
            )

        remaining_distance_km = _haversine_km(
            latest.longitude,
            latest.latitude,
            destination.longitude,
            destination.latitude,
        )
        travel_seconds = int(round(remaining_distance_km / self.average_speed_kph * 3600))
        return EtaResult(
            status="available",
            estimated_arrival_at=calculated_at + timedelta(seconds=travel_seconds),
            remaining_distance_km=round(remaining_distance_km, 2),
            updated_at=latest.reported_at,
            calculated_at=calculated_at,
            destination=destination,
            provider="offline_straight_line",
            average_speed_kph=self.average_speed_kph,
        )

    def unavailable(
        self,
        reason: str,
        message: str,
        *,
        destination: Destination | None,
        updated_at: datetime | None,
        calculated_at: datetime | None = None,
    ) -> EtaResult:
        return EtaResult(
            status="unavailable",
            estimated_arrival_at=None,
            remaining_distance_km=None,
            updated_at=updated_at,
            calculated_at=_as_utc(calculated_at or datetime.now(UTC)),
            destination=destination,
            reason=reason,
            message=message,
        )


def _haversine_km(
    start_longitude: float,
    start_latitude: float,
    end_longitude: float,
    end_latitude: float,
) -> float:
    earth_radius_km = 6371.0
    start_lat = radians(start_latitude)
    end_lat = radians(end_latitude)
    delta_lat = radians(end_latitude - start_latitude)
    delta_lon = radians(end_longitude - start_longitude)
    angle = (
        sin(delta_lat / 2) ** 2
        + cos(start_lat) * cos(end_lat) * sin(delta_lon / 2) ** 2
    )
    return 2 * earth_radius_km * asin(sqrt(angle))


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC).replace(microsecond=0)
