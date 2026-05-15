const roleProfiles = {
  cargo_owner: {
    label: "Cargo Owner",
    shortLabel: "Owner",
    userId: "owner-acme",
    tenantId: "cgf-demo",
    description: "Shipment visibility, ETA, trajectory, and alert review.",
  },
  warehouse_admin: {
    label: "Warehouse Admin",
    shortLabel: "Warehouse",
    userId: "warehouse-admin-demo",
    tenantId: "cgf-demo",
    warehouseIds: ["warehouse-shanghai"],
    description: "Vehicle maintenance, availability, and cargo binding.",
  },
  dispatcher: {
    label: "Dispatcher",
    shortLabel: "Dispatch",
    userId: "dispatcher-demo",
    tenantId: "cgf-demo",
    dispatchRegionIds: ["east-china"],
    description: "Alert triage, command creation, and vehicle distribution.",
  },
  driver: {
    label: "Driver",
    shortLabel: "Driver",
    userId: "driver-demo",
    tenantId: "cgf-demo",
    description: "Assigned tasks, command acknowledgements, and status reports.",
  },
  system_admin: {
    label: "System Admin",
    shortLabel: "Admin",
    userId: "system-admin-demo",
    tenantId: "cgf-demo",
    description: "Operations audit, alert logs, and system-wide oversight.",
  },
};

const workspaceEntries = [
  {
    id: "owner-shipment",
    view: "shipper",
    label: "Shipment Detail",
    eyebrow: "Cargo Owner",
    roles: ["cargo_owner"],
    endpoint: "/api/shipments/CGF-DEMO-001/latest-location",
    summary: "Latest location, ETA, vehicle status, trajectory, and alert entry points.",
    status: "Live API",
  },
  {
    id: "warehouse-vehicles",
    label: "Vehicles",
    eyebrow: "Warehouse",
    roles: ["warehouse_admin"],
    endpoint: "/api/vehicles",
    summary: "Maintain scoped vehicles and inspect device, plate, and binding state.",
    status: "Live API",
  },
  {
    id: "warehouse-binding",
    label: "Cargo Binding",
    eyebrow: "Warehouse",
    roles: ["warehouse_admin"],
    endpoint: "/api/cargo-bindings",
    summary: "Bind cargo to available vehicles and surface binding conflict states.",
    status: "Workflow",
  },
  {
    id: "dispatch-alerts",
    view: "alert-handling",
    label: "Alert Command Desk",
    eyebrow: "Dispatch",
    roles: ["dispatcher"],
    endpoint: "/api/alerts",
    summary: "Review scoped alerts, send driver commands, and close resolved incidents.",
    status: "Live API",
  },
  {
    id: "dispatch-map",
    view: "dispatch",
    label: "Vehicle Distribution",
    eyebrow: "Dispatch",
    roles: ["dispatcher"],
    endpoint: "/api/vehicles",
    summary: "Scan online vehicles, transport status, and active alert hotspots.",
    status: "Live UI",
  },
  {
    id: "driver-tasks",
    view: "driver",
    label: "Driver Tasks",
    eyebrow: "Driver",
    roles: ["driver"],
    endpoint: "/api/shipments/CGF-DEMO-001/trajectory",
    summary: "View assigned route context, command requests, and status reporting steps.",
    status: "Live UI",
  },
  {
    id: "admin-alert-logs",
    view: "admin-logs",
    label: "Alert Logs",
    eyebrow: "System Admin",
    roles: ["system_admin"],
    endpoint: "/api/alert-logs",
    summary: "Search alert history, handling chain, notification records, and exports.",
    status: "Live API",
  },
];

const roleOrder = [
  "cargo_owner",
  "warehouse_admin",
  "dispatcher",
  "driver",
  "system_admin",
];

const isKnownRole = (role) => Object.hasOwn(roleProfiles, role);

const visibleEntriesForRole = (role) => {
  if (!isKnownRole(role)) {
    return [];
  }
  return workspaceEntries.filter((entry) => entry.roles.includes(role));
};

const defaultEntryForRole = (role) => visibleEntriesForRole(role)[0] || null;

const resolveWorkspaceEntry = (role, requestedEntryId) => {
  if (!isKnownRole(role)) {
    return {
      authorized: false,
      reason: "unknown_role",
      entry: null,
      allowedEntries: [],
    };
  }

  const allowedEntries = visibleEntriesForRole(role);
  const requestedEntry = workspaceEntries.find((entry) => entry.id === requestedEntryId);
  if (requestedEntry && !requestedEntry.roles.includes(role)) {
    return {
      authorized: false,
      reason: "not_allowed_for_role",
      entry: requestedEntry,
      allowedEntries,
    };
  }

  return {
    authorized: true,
    reason: "allowed",
    entry: requestedEntry || allowedEntries[0] || null,
    allowedEntries,
  };
};

const authHeadersForRole = (role) => {
  const profile = roleProfiles[role];
  if (!profile) {
    return {};
  }
  const headers = {
    "X-CargoFlow-User-Id": profile.userId,
    "X-CargoFlow-Role": role,
    "X-CargoFlow-Tenant-Id": profile.tenantId,
  };
  if (profile.warehouseIds && profile.warehouseIds.length > 0) {
    headers["X-CargoFlow-Warehouse-Ids"] = profile.warehouseIds.join(",");
  }
  if (profile.dispatchRegionIds && profile.dispatchRegionIds.length > 0) {
    headers["X-CargoFlow-Dispatch-Region-Ids"] = profile.dispatchRegionIds.join(",");
  }
  return headers;
};

const navigationRules = {
  roleOrder,
  roleProfiles,
  workspaceEntries,
  visibleEntriesForRole,
  defaultEntryForRole,
  resolveWorkspaceEntry,
  authHeadersForRole,
};

if (typeof module !== "undefined" && module.exports) {
  module.exports = navigationRules;
}

if (typeof window !== "undefined") {
  window.CargoFlowNavigation = navigationRules;
}
