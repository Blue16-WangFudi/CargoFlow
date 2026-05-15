const assert = require("node:assert/strict");

const {
  roleProfiles,
  visibleEntriesForRole,
  resolveWorkspaceEntry,
  authHeadersForRole,
} = require("../navigation-rules.js");

const expectedEntryIds = {
  cargo_owner: ["owner-shipment"],
  warehouse_admin: ["warehouse-vehicles", "warehouse-binding"],
  dispatcher: ["dispatch-alerts", "dispatch-map"],
  driver: ["driver-tasks"],
  system_admin: ["admin-alert-logs"],
};

for (const [role, expectedIds] of Object.entries(expectedEntryIds)) {
  const actualIds = visibleEntriesForRole(role).map((entry) => entry.id);
  assert.deepEqual(actualIds, expectedIds, `${role} should see only authorized entries`);
}

const dispatcherEntry = resolveWorkspaceEntry("dispatcher", "dispatch-alerts");
assert.equal(dispatcherEntry.authorized, true);
assert.equal(dispatcherEntry.entry.id, "dispatch-alerts");

const unauthorizedOwnerEntry = resolveWorkspaceEntry("cargo_owner", "dispatch-alerts");
assert.equal(unauthorizedOwnerEntry.authorized, false);
assert.equal(unauthorizedOwnerEntry.reason, "not_allowed_for_role");
assert.deepEqual(
  unauthorizedOwnerEntry.allowedEntries.map((entry) => entry.id),
  ["owner-shipment"],
);

const fallbackEntry = resolveWorkspaceEntry("cargo_owner", "missing-route");
assert.equal(fallbackEntry.authorized, true);
assert.equal(fallbackEntry.entry.id, "owner-shipment");

const unknownRoleEntry = resolveWorkspaceEntry("unknown", "owner-shipment");
assert.equal(unknownRoleEntry.authorized, false);
assert.equal(unknownRoleEntry.reason, "unknown_role");

const driverHeaders = authHeadersForRole("driver");
assert.equal(driverHeaders["X-CargoFlow-Role"], "driver");
assert.equal(driverHeaders["X-CargoFlow-User-Id"], roleProfiles.driver.userId);
assert.equal(driverHeaders["X-CargoFlow-Tenant-Id"], "cgf-demo");
assert.ok(!Object.hasOwn(driverHeaders, "X-CargoFlow-Dispatch-Region-Ids"));

const dispatcherHeaders = authHeadersForRole("dispatcher");
assert.equal(dispatcherHeaders["X-CargoFlow-Dispatch-Region-Ids"], "east-china");

console.log("navigation rules ok");
