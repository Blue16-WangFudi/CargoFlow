const apiBase =
  new URLSearchParams(window.location.search).get("api") ||
  window.localStorage.getItem("cargoflowApiBase") ||
  "http://127.0.0.1:8000";

window.localStorage.setItem("cargoflowApiBase", apiBase);

const dispatcherAuthHeaders = {
  "X-CargoFlow-User-Id": "dispatcher-demo",
  "X-CargoFlow-Role": "dispatcher",
  "X-CargoFlow-Tenant-Id": "cgf-demo",
  "X-CargoFlow-Dispatch-Region-Ids": "east-china",
};

const cargoOwnerAuthHeaders = {
  "X-CargoFlow-User-Id": "owner-acme",
  "X-CargoFlow-Role": "cargo_owner",
  "X-CargoFlow-Tenant-Id": "cgf-demo",
};

const systemAdminAuthHeaders = {
  "X-CargoFlow-User-Id": "admin-demo",
  "X-CargoFlow-Role": "system_admin",
  "X-CargoFlow-Tenant-Id": "cgf-demo",
};

const warehouseAuthHeaders = {
  "X-CargoFlow-User-Id": "warehouse-admin",
  "X-CargoFlow-Role": "warehouse_admin",
  "X-CargoFlow-Tenant-Id": "cgf-demo",
  "X-CargoFlow-Warehouse-Ids": "warehouse-shanghai",
};

const state = {
  alerts: [],
  vehicles: [],
  logs: [],
  selectedLogId: null,
  selectedAlertId: null,
  selectedVehicleId: null,
  statusFilter: "",
  vehicleFilter: "all",
  activeView: "dispatch",
  shipper: {
    shipmentId: "CGF-DEMO-001",
    snapshot: null,
    latest: null,
    eta: null,
    trajectory: null,
  },
};

const $ = (id) => document.getElementById(id);

const setText = (id, value) => {
  $(id).textContent = value;
};

const setStatus = (label, status) => {
  const serviceStatus = $("service-status");
  serviceStatus.dataset.state = status;
  setText("status-label", label);
};

const createElement = (tagName, className, textContent) => {
  const element = document.createElement(tagName);
  if (className) {
    element.className = className;
  }
  if (textContent !== undefined) {
    element.textContent = textContent;
  }
  return element;
};

const titleCase = (value) =>
  String(value || "-")
    .replaceAll("_", " ")
    .replace(/\b\w/g, (character) => character.toUpperCase());

const formatDate = (value) => {
  if (!value) {
    return "-";
  }
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return value;
  }
  return new Intl.DateTimeFormat(undefined, {
    dateStyle: "medium",
    timeStyle: "short",
  }).format(date);
};

const formatCoordinate = (location) => {
  if (!location || location.longitude === undefined || location.latitude === undefined) {
    return "-";
  }
  return `${Number(location.longitude).toFixed(4)}, ${Number(location.latitude).toFixed(4)}`;
};

const formatDistance = (value) => {
  if (value === null || value === undefined) {
    return "-";
  }
  return `${value} km remaining`;
};

const requestJson = async (path, options = {}) => {
  const response = await fetch(`${apiBase}${path}`, {
    ...options,
    headers: {
      ...(options.authHeaders || dispatcherAuthHeaders),
      ...(options.body ? { "Content-Type": "application/json" } : {}),
      ...(options.headers || {}),
    },
  });
  const payload = await response.json();
  if (!response.ok) {
    throw new Error(payload.message || `Request failed with ${response.status}`);
  }
  return payload;
};

const buildLogQuery = () => {
  const params = new URLSearchParams();
  [
    ["type", $("log-type").value],
    ["severity", $("log-severity").value],
    ["status", $("log-status").value],
    ["vehicleId", $("log-vehicle").value],
    ["cargoId", $("log-cargo").value],
    ["triggeredFrom", toIsoDateTime($("log-from").value)],
    ["triggeredTo", toIsoDateTime($("log-to").value)],
  ].forEach(([key, value]) => {
    if (value && String(value).trim()) {
      params.set(key, String(value).trim());
    }
  });
  const query = params.toString();
  return query ? `?${query}` : "";
};

const toIsoDateTime = (value) => {
  if (!value) {
    return "";
  }
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? "" : date.toISOString();
};

const loadAlerts = async () => {
  setStatus("Loading alerts", "loading");
  const query = state.statusFilter ? `?status=${encodeURIComponent(state.statusFilter)}` : "";
  const payload = await requestJson(`/api/alerts${query}`);
  state.alerts = payload.alerts || [];
  if (!state.selectedAlertId && state.alerts.length > 0) {
    state.selectedAlertId = state.alerts[0].alertId;
  }
  renderAlertList();
  if (state.selectedAlertId) {
    await loadAlertDetail(state.selectedAlertId);
  } else {
    renderEmptyDetail();
  }
  setStatus(`${payload.count} scoped alert${payload.count === 1 ? "" : "s"}`, "ok");
};

const loadAlertDetail = async (alertId) => {
  const payload = await requestJson(`/api/alerts/${encodeURIComponent(alertId)}`);
  renderAlertDetail(payload.alert);
};

const loadLogs = async () => {
  setStatus("Loading alert logs", "loading");
  const payload = await requestJson(`/api/alert-logs${buildLogQuery()}`, {
    authHeaders: systemAdminAuthHeaders,
  });
  state.logs = payload.logs || [];
  if (!state.logs.some((log) => log.alertId === state.selectedLogId)) {
    state.selectedLogId = state.logs[0]?.alertId || null;
  }
  renderLogs();
  renderSelectedLogChain();
  setStatus(`${payload.count} alert log${payload.count === 1 ? "" : "s"}`, "ok");
};

const loadShipperDetail = async () => {
  setStatus("Loading cargo detail", "loading");
  const snapshot = await requestJson("/api/shipments/demo", {
    authHeaders: cargoOwnerAuthHeaders,
  });
  const shipmentId = snapshot.shipmentId || state.shipper.shipmentId;
  const [latest, eta, trajectory] = await Promise.all([
    requestJson(`/api/shipments/${encodeURIComponent(shipmentId)}/latest-location`, {
      authHeaders: cargoOwnerAuthHeaders,
    }),
    requestJson(`/api/shipments/${encodeURIComponent(shipmentId)}/eta`, {
      authHeaders: cargoOwnerAuthHeaders,
    }),
    requestJson(`/api/shipments/${encodeURIComponent(shipmentId)}/trajectory`, {
      authHeaders: cargoOwnerAuthHeaders,
    }),
  ]);
  state.shipper = {
    shipmentId,
    snapshot,
    latest,
    eta,
    trajectory,
  };
  renderShipperDetail();
  setStatus(`${shipmentId} cargo detail ready`, "ok");
};

const loadWarehouse = async () => {
  setStatus("Loading vehicles", "loading");
  const payload = await requestJson("/api/vehicles", {
    authHeaders: warehouseAuthHeaders,
  });
  state.vehicles = payload.vehicles || [];
  if (!state.vehicles.some((vehicle) => vehicle.vehicleId === state.selectedVehicleId)) {
    state.selectedVehicleId = state.vehicles[0]?.vehicleId || null;
  }
  renderWarehouse();
  setStatus(`${payload.count} warehouse vehicle${payload.count === 1 ? "" : "s"}`, "ok");
};

const renderAlertList = () => {
  const list = $("alert-list");
  list.replaceChildren();
  if (state.alerts.length === 0) {
    const empty = document.createElement("p");
    empty.className = "empty-state";
    empty.textContent = "No scoped alerts match this filter.";
    list.append(empty);
    return;
  }

  state.alerts.forEach((alert) => {
    const button = document.createElement("button");
    button.className = "alert-card";
    button.type = "button";
    button.dataset.alertId = alert.alertId;
    button.setAttribute("aria-pressed", String(alert.alertId === state.selectedAlertId));

    const top = createElement("span", "alert-card-top");
    top.append(
      createElement("strong", "", alert.alertNumber),
      createElement("span", `badge ${alert.severity}`, titleCase(alert.severity)),
    );

    const meta = document.createElement("span");
    meta.className = "alert-card-meta";
    meta.textContent = `${titleCase(alert.alertType)} · ${alert.vehicleId}`;

    const foot = document.createElement("span");
    foot.className = "alert-card-foot";
    foot.textContent = `${titleCase(alert.status)} · ${formatDate(alert.triggeredAt)}`;

    button.append(top, meta, foot);
    button.addEventListener("click", async () => {
      state.selectedAlertId = alert.alertId;
      renderAlertList();
      await loadAlertDetail(alert.alertId);
    });
    list.append(button);
  });
};

const renderAlertDetail = (alert) => {
  state.selectedAlertId = alert.alertId;
  setText("alert-summary", `${alert.alertNumber} · ${titleCase(alert.alertType)}`);
  setText("alert-status", titleCase(alert.status));
  $("alert-status").className = `badge ${alert.status}`;
  setText("alert-number", alert.alertNumber);
  setText("alert-severity", titleCase(alert.severity));
  setText("alert-type", titleCase(alert.alertType));
  setText("alert-vehicle", alert.vehicleId);
  setText("alert-cargo", alert.cargoId);
  setText("alert-triggered", formatDate(alert.triggeredAt));
  renderEvidence(alert.latestEvidence || {});
  renderCommands(alert.dispatchCommands || []);
  const isOpen = ["pending", "processing"].includes(alert.status);
  $("send-command").disabled = !isOpen;
  $("close-alert").disabled = !isOpen;
};

const renderEmptyDetail = () => {
  setText("alert-summary", "No alert selected");
  setText("alert-status", "-");
  setText("alert-number", "-");
  setText("alert-severity", "-");
  setText("alert-type", "-");
  setText("alert-vehicle", "-");
  setText("alert-cargo", "-");
  setText("alert-triggered", "-");
  $("evidence-list").replaceChildren(emptyMessage("Select an alert to inspect evidence."));
  renderCommands([]);
};

const renderEvidence = (evidence) => {
  const list = $("evidence-list");
  list.replaceChildren();
  const entries = Object.entries(evidence);
  if (entries.length === 0) {
    list.append(emptyMessage("No evidence payload is attached."));
    return;
  }
  entries.forEach(([key, value]) => {
    const item = document.createElement("div");
    item.className = "evidence-item";
    const label = document.createElement("span");
    label.textContent = titleCase(key);
    const content = document.createElement("strong");
    content.textContent = typeof value === "object" ? JSON.stringify(value) : value;
    item.append(label, content);
    list.append(item);
  });
};

const renderCommands = (commands) => {
  const list = $("command-list");
  list.replaceChildren();
  setText("command-count", `${commands.length} command${commands.length === 1 ? "" : "s"}`);
  if (commands.length === 0) {
    list.append(emptyMessage("No dispatch commands have been created."));
    return;
  }

  commands.forEach((command) => {
    const item = document.createElement("article");
    item.className = "command-item";
    const main = createElement("div", "command-main");
    main.append(
      createElement("span", `badge ${command.status}`, titleCase(command.status)),
      createElement("strong", "", command.commandNumber),
      createElement("p", "", command.content),
    );
    const timeline = createElement("dl", "timeline");
    [
      ["Issued", command.issuedAt],
      ["Delivered", command.deliveredAt],
      ["Confirmed", command.confirmedAt],
      ["Failed", command.failedAt],
    ].forEach(([label, value]) => {
      const group = document.createElement("div");
      group.append(createElement("dt", "", label), createElement("dd", "", formatDate(value)));
      timeline.append(group);
    });
    item.append(main, timeline);
    list.append(item);
  });
};

const renderLogs = () => {
  const table = $("log-table");
  table.replaceChildren();
  setText("log-count", `${state.logs.length} log${state.logs.length === 1 ? "" : "s"}`);
  if (state.logs.length === 0) {
    table.append(emptyMessage("No alert logs match these filters."));
    return;
  }

  const header = createElement("div", "log-row log-header");
  ["Alert", "Status", "Asset", "Triggered", "Chain"].forEach((label) => {
    header.append(createElement("span", "", label));
  });
  table.append(header);

  state.logs.forEach((log) => {
    const row = document.createElement("button");
    row.className = "log-row";
    row.type = "button";
    row.dataset.logId = log.alertId;
    row.setAttribute("aria-pressed", String(log.alertId === state.selectedLogId));

    const alertCell = createElement("span", "log-main");
    alertCell.append(
      createElement("strong", "", log.alertNumber),
      createElement("small", "", titleCase(log.alertType)),
    );

    const statusCell = createElement("span", "log-status-cell");
    statusCell.append(
      createElement("span", `badge ${log.severity}`, titleCase(log.severity)),
      createElement("span", `badge ${log.status}`, titleCase(log.status)),
    );

    const assetCell = createElement("span", "log-main");
    assetCell.append(
      createElement("strong", "", log.vehicleId),
      createElement("small", "", log.cargoId),
    );

    const triggeredCell = createElement("span", "", formatDate(log.triggeredAt));
    const chainCell = createElement(
      "span",
      "chain-count",
      `${log.chain?.notificationCount || 0} notice / ${log.chain?.dispatchCommandCount || 0} command`,
    );

    row.append(alertCell, statusCell, assetCell, triggeredCell, chainCell);
    row.addEventListener("click", () => {
      state.selectedLogId = log.alertId;
      renderLogs();
      renderSelectedLogChain();
    });
    table.append(row);
  });
};

const renderShipperDetail = () => {
  const { snapshot, latest, eta, trajectory } = state.shipper;
  if (!snapshot || !latest || !eta || !trajectory) {
    renderEmptyShipperDetail();
    return;
  }

  const cargo = snapshot.cargo || {};
  const vehicle = latest.vehicle || trajectory.vehicle || snapshot.vehicle || {};
  const location = latest.latestLocation;
  const delay = latest.delayHint || {};
  const etaPayload = eta.eta || {};
  const points = trajectory.trajectory || [];
  const alertPoints = points.filter((point) => point.kind === "alert");

  setText("shipper-shipment-id", latest.shipmentId || snapshot.shipmentId || "-");
  setText("shipper-cargo-label", latest.cargoId || snapshot.cargo?.id || "cargo-demo-001");
  setText("shipper-cargo-owner", cargo.owner || "-");
  setText("shipper-summary", `${titleCase(latest.transportStatus)} - ${vehicle.plateNumber || "-"}`);
  setText("shipper-transport-status", titleCase(latest.transportStatus));
  setText("shipper-vehicle-number", vehicle.vehicleNumber || "-");
  setText("shipper-plate-number", vehicle.plateNumber || "-");
  setText("shipper-device-id", vehicle.deviceId || "-");
  setText("shipper-location-updated", formatDate(location?.updatedAt || location?.reportedAt));
  setText("shipper-location-coordinate", formatCoordinate(location));
  setText("shipper-eta-arrival", formatDate(etaPayload.estimatedArrival));
  setText("shipper-eta-distance", formatDistance(etaPayload.remainingDistanceKm));
  setText("shipper-delay-status", titleCase(delay.status));
  setText("shipper-delay-message", delay.message || "-");
  setText("shipper-location-state", location ? titleCase(delay.status || "tracked") : "Missing");
  $("shipper-location-state").className = `badge ${location ? delay.status || "current" : "missing"}`;
  setText("shipper-eta-status", titleCase(etaPayload.status));
  setText("shipper-cargo-description", cargo.description || latest.cargoId || "-");
  setText("shipper-destination", etaPayload.destination?.name || "-");
  setText("shipper-driver", vehicle.driverUserId || snapshot.vehicle?.driver || "-");
  setText("shipper-eta-calculated", formatDate(etaPayload.calculatedAt));

  renderRouteCanvas(points, location, etaPayload.destination);
  renderTrajectory(points);
  renderShipperAlerts(alertPoints);
};

const renderEmptyShipperDetail = () => {
  [
    "shipper-transport-status",
    "shipper-vehicle-number",
    "shipper-plate-number",
    "shipper-device-id",
    "shipper-location-updated",
    "shipper-location-coordinate",
    "shipper-eta-arrival",
    "shipper-eta-distance",
    "shipper-delay-status",
    "shipper-delay-message",
    "shipper-location-state",
    "shipper-eta-status",
    "shipper-cargo-description",
    "shipper-destination",
    "shipper-driver",
    "shipper-eta-calculated",
  ].forEach((id) => setText(id, "-"));
  $("route-canvas").replaceChildren(emptyMessage("Shipment route is not loaded."));
  $("shipper-trajectory-list").replaceChildren(emptyMessage("No trajectory points loaded."));
  $("shipper-alert-list").replaceChildren(emptyMessage("No alert entries loaded."));
};

const renderRouteCanvas = (points, latestLocation, destination) => {
  const canvas = $("route-canvas");
  canvas.replaceChildren();
  const track = createElement("div", "route-track");
  const routePoints = [
    points.find((point) => point.kind === "start"),
    latestLocation ? { ...latestLocation, kind: "current" } : null,
    destination ? { ...destination, kind: "end" } : points.find((point) => point.kind === "end"),
  ].filter(Boolean);

  if (routePoints.length === 0) {
    canvas.append(emptyMessage("No route coordinates are available."));
    return;
  }

  routePoints.forEach((point, index) => {
    const node = createElement("div", `route-node ${point.kind}`);
    node.style.left = `${routePoints.length === 1 ? 50 : (index / (routePoints.length - 1)) * 100}%`;
    node.append(
      createElement("span", "route-pin", ""),
      createElement("strong", "", titleCase(point.kind)),
      createElement("small", "", point.name || formatCoordinate(point)),
    );
    track.append(node);
  });

  const alertCount = points.filter((point) => point.kind === "alert").length;
  if (alertCount > 0) {
    const alertNode = createElement("div", "route-node alert");
    alertNode.style.left = "58%";
    alertNode.append(
      createElement("span", "route-pin", ""),
      createElement("strong", "", `${alertCount} Alert${alertCount === 1 ? "" : "s"}`),
      createElement("small", "", "Key node"),
    );
    track.append(alertNode);
  }

  canvas.append(track);
};

const renderTrajectory = (points) => {
  const list = $("shipper-trajectory-list");
  list.replaceChildren();
  setText("shipper-trajectory-count", `${points.length} point${points.length === 1 ? "" : "s"}`);
  if (points.length === 0) {
    list.append(emptyMessage("No trajectory points are available."));
    return;
  }

  points.forEach((point) => {
    const item = createElement("article", `trajectory-item ${point.kind}`);
    const badge = createElement("span", `badge ${trajectoryBadgeClass(point)}`, titleCase(point.kind));
    const content = createElement("div", "trajectory-content");
    content.append(
      createElement("strong", "", trajectoryTitle(point)),
      createElement("span", "", trajectoryDetail(point)),
    );
    item.append(badge, content, createElement("time", "", formatDate(point.occurredAt || point.reportedAt)));
    list.append(item);
  });
};

const trajectoryBadgeClass = (point) => {
  if (point.kind === "alert") {
    return point.severity || "high";
  }
  if (point.kind === "status_report") {
    return "acknowledged";
  }
  return point.kind;
};

const trajectoryTitle = (point) => {
  if (point.kind === "alert") {
    return `${titleCase(point.alertType)} - ${titleCase(point.status)}`;
  }
  if (point.kind === "status_report") {
    return titleCase(point.reportStatus);
  }
  if (point.kind === "gps") {
    return point.eventId || "GPS point";
  }
  return point.name || titleCase(point.kind);
};

const trajectoryDetail = (point) => {
  if (point.kind === "status_report") {
    return `Reporter ${point.reporterUserId}`;
  }
  if (point.kind === "alert") {
    return point.alertId || "Alert key node";
  }
  if (point.longitude !== undefined && point.latitude !== undefined) {
    return formatCoordinate(point);
  }
  return point.isKeyNode ? "Key route node" : "-";
};

const renderShipperAlerts = (alertPoints) => {
  const list = $("shipper-alert-list");
  list.replaceChildren();
  setText("shipper-alert-count", `${alertPoints.length} alert${alertPoints.length === 1 ? "" : "s"}`);
  if (alertPoints.length === 0) {
    list.append(emptyMessage("No alert nodes are attached to this cargo trajectory."));
    return;
  }

  alertPoints.forEach((alert) => {
    const item = createElement("article", "shipper-alert-item");
    const main = createElement("div", "shipper-alert-main");
    main.append(
      createElement("span", `badge ${alert.severity || "high"}`, titleCase(alert.severity || "alert")),
      createElement("strong", "", titleCase(alert.alertType)),
      createElement("small", "", `${titleCase(alert.status)} - ${formatDate(alert.occurredAt)}`),
    );
    const action = createElement("button", "neutral-button compact-button", "Open");
    action.type = "button";
    action.addEventListener("click", () => openAlertEntry(alert.alertId));
    item.append(main, action);
    list.append(item);
  });
};

const renderWarehouse = () => {
  renderWarehouseSummary();
  renderVehicleList();
  renderVehicleDetail();
  renderAvailableVehicleSelect();
};

const renderWarehouseSummary = () => {
  const available = state.vehicles.filter((vehicle) => vehicle.bindingStatus === "available");
  const bound = state.vehicles.filter((vehicle) => vehicle.bindingStatus === "bound");
  const disabled = state.vehicles.filter((vehicle) => vehicle.bindingStatus === "disabled");
  setText("warehouse-available-count", String(available.length));
  setText("warehouse-bound-count", String(bound.length));
  setText("warehouse-disabled-count", String(disabled.length));
  setText(
    "warehouse-summary",
    `${available.length} available · ${bound.length} bound · ${disabled.length} disabled`,
  );
};

const filteredVehicles = () => {
  if (state.vehicleFilter === "all") {
    return state.vehicles;
  }
  return state.vehicles.filter((vehicle) => vehicle.bindingStatus === state.vehicleFilter);
};

const selectedVehicle = () =>
  state.vehicles.find((vehicle) => vehicle.vehicleId === state.selectedVehicleId) || null;

const renderVehicleList = () => {
  const list = $("warehouse-vehicle-list");
  list.replaceChildren();
  const vehicles = filteredVehicles();
  if (vehicles.length === 0) {
    list.append(emptyMessage("No vehicles match this filter."));
    return;
  }

  vehicles.forEach((vehicle) => {
    const button = document.createElement("button");
    button.className = "vehicle-card";
    button.type = "button";
    button.dataset.vehicleId = vehicle.vehicleId;
    button.setAttribute("aria-pressed", String(vehicle.vehicleId === state.selectedVehicleId));

    const top = createElement("span", "alert-card-top");
    top.append(
      createElement("strong", "", vehicle.vehicleNumber),
      createElement("span", `badge ${vehicle.bindingStatus}`, titleCase(vehicle.bindingStatus)),
    );

    const meta = createElement("span", "alert-card-meta");
    meta.textContent = `${vehicle.plateNumber} · ${vehicle.deviceId}`;

    const foot = createElement("span", "alert-card-foot");
    foot.textContent = `${titleCase(vehicle.onlineStatus)} · ${vehicle.driverUserId || "No driver"}`;

    button.append(top, meta, foot);
    button.addEventListener("click", () => {
      state.selectedVehicleId = vehicle.vehicleId;
      populateVehicleForm(vehicle);
      renderWarehouse();
    });
    list.append(button);
  });
};

const renderVehicleDetail = () => {
  const vehicle = selectedVehicle();
  if (!vehicle) {
    [
      "warehouse-detail-number",
      "warehouse-detail-plate",
      "warehouse-detail-device",
      "warehouse-detail-driver",
      "warehouse-detail-warehouse",
      "warehouse-detail-last-seen",
    ].forEach((id) => setText(id, "-"));
    setText("warehouse-selected-status", "-");
    $("warehouse-selected-status").className = "badge";
    $("disable-warehouse-vehicle").disabled = true;
    $("unbind-warehouse-vehicle").disabled = true;
    return;
  }

  setText("warehouse-selected-status", titleCase(vehicle.bindingStatus));
  $("warehouse-selected-status").className = `badge ${vehicle.bindingStatus}`;
  setText("warehouse-detail-number", vehicle.vehicleNumber);
  setText("warehouse-detail-plate", vehicle.plateNumber);
  setText("warehouse-detail-device", vehicle.deviceId);
  setText("warehouse-detail-driver", vehicle.driverUserId || "-");
  setText("warehouse-detail-warehouse", vehicle.warehouseId);
  setText("warehouse-detail-last-seen", formatDate(vehicle.lastSeenAt));
  $("disable-warehouse-vehicle").disabled = vehicle.bindingStatus === "disabled";
  $("unbind-warehouse-vehicle").disabled = vehicle.bindingStatus !== "bound";
};

const renderAvailableVehicleSelect = () => {
  const select = $("warehouse-available-vehicle");
  const available = state.vehicles.filter((vehicle) => vehicle.bindingStatus === "available");
  select.replaceChildren();
  if (available.length === 0) {
    const option = document.createElement("option");
    option.value = "";
    option.textContent = "No available vehicles";
    select.append(option);
    select.disabled = true;
    $("bind-cargo").disabled = true;
    setText("warehouse-binding-hint", "Create or unbind a vehicle first");
    return;
  }

  available.forEach((vehicle) => {
    const option = document.createElement("option");
    option.value = vehicle.vehicleId;
    option.textContent = `${vehicle.vehicleNumber} · ${vehicle.plateNumber} · ${vehicle.driverUserId || "driver required"}`;
    select.append(option);
  });
  if (selectedVehicle()?.bindingStatus === "available") {
    select.value = state.selectedVehicleId;
  }
  select.disabled = false;
  $("bind-cargo").disabled = false;
  setText("warehouse-binding-hint", `${available.length} vehicle${available.length === 1 ? "" : "s"} ready`);
};

const populateVehicleForm = (vehicle) => {
  $("warehouse-edit-vehicle-id").value = vehicle.vehicleId;
  $("warehouse-vehicle-id").value = vehicle.vehicleId;
  $("warehouse-vehicle-id").disabled = true;
  $("warehouse-vehicle-number").value = vehicle.vehicleNumber;
  $("warehouse-plate-number").value = vehicle.plateNumber;
  $("warehouse-device-id").value = vehicle.deviceId;
  $("warehouse-driver-id").value = vehicle.driverUserId || "";
  $("warehouse-online-status").value = vehicle.onlineStatus;
  $("warehouse-notes").value = vehicle.notes || "";
  setText("warehouse-form-mode", "Update");
};

const resetVehicleForm = () => {
  $("warehouse-vehicle-form").reset();
  $("warehouse-edit-vehicle-id").value = "";
  $("warehouse-vehicle-id").disabled = false;
  setText("warehouse-form-mode", "Create");
};

const vehiclePayloadFromForm = () => {
  const payload = {
    vehicleNumber: $("warehouse-vehicle-number").value.trim(),
    plateNumber: $("warehouse-plate-number").value.trim(),
    deviceId: $("warehouse-device-id").value.trim(),
    driverUserId: $("warehouse-driver-id").value.trim() || null,
    onlineStatus: $("warehouse-online-status").value,
    notes: $("warehouse-notes").value.trim() || null,
  };
  if (!$("warehouse-edit-vehicle-id").value && $("warehouse-vehicle-id").value.trim()) {
    payload.vehicleId = $("warehouse-vehicle-id").value.trim();
  }
  return payload;
};

const saveVehicle = async (event) => {
  event.preventDefault();
  setStatus("Saving vehicle", "loading");
  const editingVehicleId = $("warehouse-edit-vehicle-id").value;
  const saved = await requestJson(
    editingVehicleId
      ? `/api/vehicles/${encodeURIComponent(editingVehicleId)}`
      : "/api/vehicles",
    {
      method: editingVehicleId ? "PATCH" : "POST",
      body: JSON.stringify(vehiclePayloadFromForm()),
      authHeaders: warehouseAuthHeaders,
    },
  );
  state.selectedVehicleId = saved.vehicle.vehicleId;
  populateVehicleForm(saved.vehicle);
  await loadWarehouse();
  renderWarehouseResult(`${saved.vehicle.vehicleNumber} saved`, "ok");
};

const disableSelectedVehicle = async () => {
  const vehicle = selectedVehicle();
  if (!vehicle) {
    return;
  }
  setStatus("Disabling vehicle", "loading");
  const payload = await requestJson(`/api/vehicles/${encodeURIComponent(vehicle.vehicleId)}/disable`, {
    method: "POST",
    body: JSON.stringify({ reason: "Warehouse maintenance hold" }),
    authHeaders: warehouseAuthHeaders,
  });
  state.selectedVehicleId = payload.vehicle.vehicleId;
  await loadWarehouse();
  renderWarehouseResult(`${payload.vehicle.vehicleNumber} disabled`, "warning");
};

const unbindSelectedVehicle = async () => {
  const vehicle = selectedVehicle();
  if (!vehicle) {
    return;
  }
  setStatus("Unbinding vehicle", "loading");
  const payload = await requestJson(`/api/vehicles/${encodeURIComponent(vehicle.vehicleId)}/unbind`, {
    method: "POST",
    body: JSON.stringify({ reason: "Warehouse manual release" }),
    authHeaders: warehouseAuthHeaders,
  });
  state.selectedVehicleId = payload.vehicle.vehicleId;
  await loadWarehouse();
  renderWarehouseResult(`${payload.vehicle.vehicleNumber} is available`, "ok");
};

const bindCargo = async (event) => {
  event.preventDefault();
  setStatus("Binding cargo", "loading");
  const payload = {
    cargoId: $("warehouse-cargo-id").value.trim(),
    vehicleId: $("warehouse-available-vehicle").value,
  };
  const driverUserId = $("warehouse-binding-driver").value.trim();
  const taskNumber = $("warehouse-task-number").value.trim();
  if (driverUserId) {
    payload.driverUserId = driverUserId;
  }
  if (taskNumber) {
    payload.taskNumber = taskNumber;
  }
  const result = await requestJson("/api/cargo-bindings", {
    method: "POST",
    body: JSON.stringify(payload),
    authHeaders: warehouseAuthHeaders,
  });
  state.selectedVehicleId = result.binding.vehicle.vehicleId;
  await loadWarehouse();
  renderWarehouseResult(
    `${result.binding.cargoNumber} bound to ${result.binding.vehicle.vehicleNumber} as ${result.binding.taskNumber}`,
    "ok",
  );
};

const renderWarehouseResult = (message, tone) => {
  const result = $("warehouse-result");
  result.hidden = false;
  result.dataset.tone = tone;
  result.textContent = message;
};

const openAlertEntry = async (alertId) => {
  if (!alertId) {
    return;
  }
  switchView("dispatch");
  state.statusFilter = "";
  document.querySelectorAll(".filter-button").forEach((button) => {
    button.classList.toggle("is-active", button.dataset.status === "");
  });
  state.selectedAlertId = alertId;
  await loadAlerts();
  $("dispatch-view").scrollIntoView({ block: "start", behavior: "auto" });
};

const renderSelectedLogChain = () => {
  const chain = $("chain-grid");
  chain.replaceChildren();
  const log = state.logs.find((item) => item.alertId === state.selectedLogId);
  if (!log) {
    setText("chain-summary", "Select a log");
    chain.append(emptyMessage("Select an alert log to inspect notifications, commands, and closure audit."));
    return;
  }

  setText(
    "chain-summary",
    `${log.alertNumber} · ${log.chain?.notificationCount || 0} notifications · ${log.chain?.dispatchCommandCount || 0} commands`,
  );
  chain.append(
    chainSection(
      "Notifications",
      log.notifications || [],
      (notification) =>
        `${titleCase(notification.channel)} · ${titleCase(notification.status)} · ${formatDate(notification.sentAt)}`,
      (notification) => `Recipient ${notification.recipientUserId} · ${notification.template}`,
    ),
    chainSection(
      "Dispatch Commands",
      log.dispatchCommands || [],
      (command) =>
        `${command.commandNumber} · ${titleCase(command.status)} · ${formatDate(command.issuedAt)}`,
      (command) => `${titleCase(command.targetType)} ${command.targetId} · ${command.content}`,
    ),
    closureAuditSection(log),
  );
};

const chainSection = (title, items, summaryFor, detailFor) => {
  const section = createElement("article", "chain-section");
  section.append(createElement("h4", "", title));
  if (items.length === 0) {
    section.append(emptyMessage(`No ${title.toLowerCase()} recorded.`));
    return section;
  }
  items.forEach((item) => {
    const node = createElement("div", "chain-node");
    node.append(createElement("strong", "", summaryFor(item)), createElement("span", "", detailFor(item)));
    section.append(node);
  });
  return section;
};

const closureAuditSection = (log) => {
  const section = createElement("article", "chain-section");
  section.append(createElement("h4", "", "Closure Audit"));
  if (!log.chain?.hasClosedAudit) {
    section.append(emptyMessage("No closure audit has been recorded."));
    return section;
  }
  const node = createElement("div", "chain-node");
  node.append(
    createElement("strong", "", `${titleCase(log.status)} · ${formatDate(log.closedAt)}`),
    createElement("span", "", `${log.closedByUserId || "-"} · ${log.closeReason || "-"}`),
  );
  section.append(node);
  return section;
};

const exportLogs = async () => {
  setStatus("Preparing export", "loading");
  const payload = await requestJson(`/api/alert-logs/export${buildLogQuery()}`, {
    authHeaders: systemAdminAuthHeaders,
  });
  $("export-panel").hidden = false;
  setText(
    "export-meta",
    `${payload.export.fileName} · ${payload.count} log${payload.count === 1 ? "" : "s"}`,
  );
  $("export-preview").textContent = JSON.stringify(payload, null, 2);
  setStatus("Export payload ready", "ok");
};

const emptyMessage = (message) => {
  const element = document.createElement("p");
  element.className = "empty-state";
  element.textContent = message;
  return element;
};

const createCommand = async (event) => {
  event.preventDefault();
  if (!state.selectedAlertId) {
    return;
  }
  setStatus("Sending command", "loading");
  const payload = {
    targetType: "driver",
    targetId: $("command-target").value,
    content: $("command-content").value,
  };
  const created = await requestJson(
    `/api/alerts/${encodeURIComponent(state.selectedAlertId)}/dispatch-commands`,
    {
      method: "POST",
      body: JSON.stringify(payload),
    },
  );
  renderAlertDetail(created.alert);
  await loadAlerts();
  setStatus(`Command ${created.dispatchCommand.commandNumber} sent`, "ok");
};

const closeAlert = async (event) => {
  event.preventDefault();
  if (!state.selectedAlertId) {
    return;
  }
  setStatus("Closing alert", "loading");
  const closed = await requestJson(`/api/alerts/${encodeURIComponent(state.selectedAlertId)}/close`, {
    method: "POST",
    body: JSON.stringify({ closeReason: $("close-reason").value }),
  });
  renderAlertDetail({ ...closed.alert, dispatchCommands: [], notifications: [] });
  await loadAlerts();
  setStatus(`${closed.alert.alertNumber} closed`, "ok");
};

const wireEvents = () => {
  document.querySelectorAll(".mode-tab").forEach((button) => {
    button.addEventListener("click", () => switchView(button.dataset.view));
  });
  $("refresh-alerts").addEventListener("click", () => loadAlerts().catch(handleError));
  $("refresh-warehouse").addEventListener("click", () => loadWarehouse().catch(handleError));
  $("refresh-logs").addEventListener("click", () => loadLogs().catch(handleError));
  $("refresh-shipper").addEventListener("click", () => loadShipperDetail().catch(handleError));
  $("show-trajectory").addEventListener("click", () => {
    $("shipper-trajectory-section").scrollIntoView({ block: "start", behavior: "smooth" });
  });
  $("show-alerts").addEventListener("click", () => {
    $("shipper-alerts-section").scrollIntoView({ block: "start", behavior: "smooth" });
  });
  $("log-filter-form").addEventListener("submit", (event) => {
    event.preventDefault();
    state.selectedLogId = null;
    $("export-panel").hidden = true;
    loadLogs().catch(handleError);
  });
  $("reset-log-filters").addEventListener("click", () => {
    $("log-filter-form").reset();
    state.selectedLogId = null;
    $("export-panel").hidden = true;
    loadLogs().catch(handleError);
  });
  $("export-logs").addEventListener("click", () => exportLogs().catch(handleError));
  $("warehouse-vehicle-form").addEventListener("submit", (event) =>
    saveVehicle(event).catch(handleError),
  );
  $("reset-warehouse-vehicle").addEventListener("click", resetVehicleForm);
  $("disable-warehouse-vehicle").addEventListener("click", () =>
    disableSelectedVehicle().catch(handleError),
  );
  $("unbind-warehouse-vehicle").addEventListener("click", () =>
    unbindSelectedVehicle().catch(handleError),
  );
  $("warehouse-binding-form").addEventListener("submit", (event) =>
    bindCargo(event).catch(handleError),
  );
  document.querySelectorAll(".warehouse-filter").forEach((button) => {
    button.addEventListener("click", () => {
      document
        .querySelectorAll(".warehouse-filter")
        .forEach((item) => item.classList.remove("is-active"));
      button.classList.add("is-active");
      state.vehicleFilter = button.dataset.filter || "all";
      renderVehicleList();
    });
  });
  $("command-form").addEventListener("submit", (event) =>
    createCommand(event).catch(handleError),
  );
  $("close-form").addEventListener("submit", (event) =>
    closeAlert(event).catch(handleError),
  );
  document.querySelectorAll(".filter-button").forEach((button) => {
    button.addEventListener("click", () => {
      document
        .querySelectorAll(".filter-button")
        .forEach((item) => item.classList.remove("is-active"));
      button.classList.add("is-active");
      state.statusFilter = button.dataset.status || "";
      state.selectedAlertId = null;
      loadAlerts().catch(handleError);
    });
  });
};

const switchView = (view) => {
  state.activeView = view || "dispatch";
  document.querySelectorAll(".mode-tab").forEach((button) => {
    button.classList.toggle("is-active", button.dataset.view === state.activeView);
  });
  document.querySelectorAll(".view-panel").forEach((panel) => {
    panel.classList.toggle("is-active", panel.id === `${state.activeView}-view`);
  });
  if (state.activeView === "admin-logs" && state.logs.length === 0) {
    loadLogs().catch(handleError);
  }
  if (state.activeView === "warehouse" && state.vehicles.length === 0) {
    loadWarehouse().catch(handleError);
  } else if (state.activeView === "warehouse") {
    setStatus(`${state.vehicles.length} warehouse vehicle${state.vehicles.length === 1 ? "" : "s"}`, "ok");
  }
  if (state.activeView === "shipper" && !state.shipper.snapshot) {
    loadShipperDetail().catch(handleError);
  } else if (state.activeView === "shipper" && state.shipper.snapshot) {
    setStatus(`${state.shipper.shipmentId} cargo detail ready`, "ok");
  }
};

const handleError = (error) => {
  setStatus(error.message, "error");
  if (state.activeView === "warehouse") {
    renderWarehouseResult(error.message, "error");
  }
};

wireEvents();
loadAlerts().catch(handleError);
