"""Role and resource access rules for CargoFlow API endpoints."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Mapping


class AccessControlError(Exception):
    """Base class for request authorization failures."""

    error_code = "access_denied"
    status_code = 403

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class AuthenticationError(AccessControlError):
    error_code = "unauthorized"
    status_code = 401


class AuthorizationError(AccessControlError):
    error_code = "forbidden"
    status_code = 403


class Role(StrEnum):
    CARGO_OWNER = "cargo_owner"
    DRIVER = "driver"
    WAREHOUSE_ADMIN = "warehouse_admin"
    DISPATCHER = "dispatcher"
    SYSTEM_ADMIN = "system_admin"

    @classmethod
    def from_wire(cls, value: str) -> "Role":
        normalized = value.strip().lower().replace("-", "_")
        aliases = {
            "owner": cls.CARGO_OWNER,
            "cargo_owner": cls.CARGO_OWNER,
            "货主": cls.CARGO_OWNER,
            "driver": cls.DRIVER,
            "司机": cls.DRIVER,
            "warehouse": cls.WAREHOUSE_ADMIN,
            "warehouse_admin": cls.WAREHOUSE_ADMIN,
            "仓库管理员": cls.WAREHOUSE_ADMIN,
            "dispatcher": cls.DISPATCHER,
            "调度员": cls.DISPATCHER,
            "admin": cls.SYSTEM_ADMIN,
            "system_admin": cls.SYSTEM_ADMIN,
            "系统管理员": cls.SYSTEM_ADMIN,
        }
        try:
            return aliases[normalized]
        except KeyError as exc:
            raise AuthenticationError(f"Unsupported role: {value}") from exc


@dataclass(frozen=True, slots=True)
class Principal:
    user_id: str
    role: Role
    tenant_id: str
    warehouse_ids: tuple[str, ...] = ()
    dispatch_region_ids: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ShipmentScope:
    shipment_id: str
    tenant_id: str
    owner_user_id: str
    driver_user_id: str
    warehouse_ids: tuple[str, ...]
    dispatch_region_ids: tuple[str, ...]


def parse_principal(headers: Mapping[str, str]) -> Principal:
    """Build a request principal from the current development auth headers."""

    user_id = _required_header(headers, "X-CargoFlow-User-Id")
    role = Role.from_wire(_required_header(headers, "X-CargoFlow-Role"))
    tenant_id = _required_header(headers, "X-CargoFlow-Tenant-Id")
    return Principal(
        user_id=user_id,
        role=role,
        tenant_id=tenant_id,
        warehouse_ids=_split_header(headers.get("X-CargoFlow-Warehouse-Ids")),
        dispatch_region_ids=_split_header(
            headers.get("X-CargoFlow-Dispatch-Region-Ids")
        ),
    )


def require_shipment_access(principal: Principal, shipment: ShipmentScope) -> None:
    """Raise unless the principal can read a shipment-scoped API resource."""

    if principal.tenant_id != shipment.tenant_id:
        raise AuthorizationError("Principal is outside the shipment tenant scope")

    if principal.role is Role.SYSTEM_ADMIN:
        return
    if principal.role is Role.CARGO_OWNER and principal.user_id == shipment.owner_user_id:
        return
    if principal.role is Role.DRIVER and principal.user_id == shipment.driver_user_id:
        return
    if principal.role is Role.WAREHOUSE_ADMIN and _intersects(
        principal.warehouse_ids, shipment.warehouse_ids
    ):
        return
    if principal.role is Role.DISPATCHER and _intersects(
        principal.dispatch_region_ids, shipment.dispatch_region_ids
    ):
        return

    raise AuthorizationError("Principal is not allowed to read this shipment")


def _required_header(headers: Mapping[str, str], name: str) -> str:
    value = headers.get(name, "").strip()
    if not value:
        raise AuthenticationError(f"Missing required auth header: {name}")
    return value


def _split_header(value: str | None) -> tuple[str, ...]:
    if not value:
        return ()
    return tuple(item.strip() for item in value.split(",") if item.strip())


def _intersects(left: tuple[str, ...], right: tuple[str, ...]) -> bool:
    return bool(set(left).intersection(right))
