# CargoFlow Design Constraints

CargoFlow UI work must optimize for operational logistics workflows: dense
information, clear status, predictable navigation, and fast repeated action.

## Baseline Rules

- Build role-specific workspaces for cargo owners, warehouse admins,
  dispatchers, drivers, and system admins.
- Keep pages work-focused. Do not add marketing-style landing pages for product
  workflows.
- Surface stale location data, unavailable ETA, active alarms, and permission
  denials as first-class states.
- Use maps for shipment location, vehicle distribution, trajectories, route
  context, and alarm locations.
- Keep role access visible in navigation, but treat backend authorization as the
  security boundary.

Detailed component and visual guidance should be expanded before the first
frontend implementation slice.
