from __future__ import annotations

import unittest

from cargoflow_api.access_control import (
    AuthenticationError,
    AuthorizationError,
    Principal,
    Role,
    ShipmentScope,
    parse_principal,
    require_shipment_access,
)


class PrincipalParsingTests(unittest.TestCase):
    def test_parses_development_auth_headers(self) -> None:
        principal = parse_principal(
            {
                "X-CargoFlow-User-Id": "owner-1",
                "X-CargoFlow-Role": "货主",
                "X-CargoFlow-Tenant-Id": "tenant-1",
            }
        )

        self.assertEqual(principal.user_id, "owner-1")
        self.assertEqual(principal.role, Role.CARGO_OWNER)
        self.assertEqual(principal.tenant_id, "tenant-1")

    def test_rejects_missing_required_headers(self) -> None:
        with self.assertRaises(AuthenticationError):
            parse_principal({"X-CargoFlow-Role": "driver"})

    def test_rejects_unknown_roles(self) -> None:
        with self.assertRaises(AuthenticationError):
            parse_principal(
                {
                    "X-CargoFlow-User-Id": "user-1",
                    "X-CargoFlow-Role": "auditor",
                    "X-CargoFlow-Tenant-Id": "tenant-1",
                }
            )


class ShipmentAccessTests(unittest.TestCase):
    def setUp(self) -> None:
        self.shipment = ShipmentScope(
            shipment_id="shipment-1",
            tenant_id="tenant-1",
            owner_user_id="owner-1",
            driver_user_id="driver-1",
            warehouse_ids=("warehouse-1",),
            dispatch_region_ids=("region-1",),
        )

    def test_allows_role_scoped_principals(self) -> None:
        allowed = (
            Principal("owner-1", Role.CARGO_OWNER, "tenant-1"),
            Principal("driver-1", Role.DRIVER, "tenant-1"),
            Principal(
                "warehouse-admin-1",
                Role.WAREHOUSE_ADMIN,
                "tenant-1",
                warehouse_ids=("warehouse-1",),
            ),
            Principal(
                "dispatcher-1",
                Role.DISPATCHER,
                "tenant-1",
                dispatch_region_ids=("region-1",),
            ),
            Principal("admin-1", Role.SYSTEM_ADMIN, "tenant-1"),
        )

        for principal in allowed:
            with self.subTest(role=principal.role):
                require_shipment_access(principal, self.shipment)

    def test_rejects_cross_tenant_access_before_role_checks(self) -> None:
        principal = Principal("admin-1", Role.SYSTEM_ADMIN, "tenant-2")

        with self.assertRaises(AuthorizationError):
            require_shipment_access(principal, self.shipment)

    def test_rejects_mismatched_role_scope(self) -> None:
        denied = (
            Principal("owner-2", Role.CARGO_OWNER, "tenant-1"),
            Principal("driver-2", Role.DRIVER, "tenant-1"),
            Principal(
                "warehouse-admin-1",
                Role.WAREHOUSE_ADMIN,
                "tenant-1",
                warehouse_ids=("warehouse-2",),
            ),
            Principal(
                "dispatcher-1",
                Role.DISPATCHER,
                "tenant-1",
                dispatch_region_ids=("region-2",),
            ),
        )

        for principal in denied:
            with self.subTest(role=principal.role, user=principal.user_id):
                with self.assertRaises(AuthorizationError):
                    require_shipment_access(principal, self.shipment)


if __name__ == "__main__":
    unittest.main()
