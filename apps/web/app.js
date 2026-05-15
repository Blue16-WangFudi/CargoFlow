const navigation = window.CargoFlowNavigation;
const urlParams = new URLSearchParams(window.location.search);
const apiBase =
  urlParams.get("api") ||
  window.localStorage.getItem("cargoflowApiBase") ||
  "http://127.0.0.1:8000";

window.localStorage.setItem("cargoflowApiBase", apiBase);

const initialRole =
  urlParams.get("role") ||
  window.localStorage.getItem("cargoflowRole") ||
  "dispatcher";

const getAuthHeaders = (role) => {
  const navHeaders = navigation?.authHeadersForRole(role);
  return Object.keys(navHeaders || {}).length > 0 ? navHeaders : dispatcherAuthHeaders;
};

const dispatcherAuthHeaders = {
  "X-CargoFlow-User-Id": "dispatcher-demo",
  "X-CargoFlow-Role": "dispatcher",
  "X-CargoFlow-Tenant-Id": "cgf-demo",
  "X-CargoFlow-Dispatch-Region-Ids": "east-china",
};

const cargoOwnerAuthHeaders = getAuthHeaders("cargo_owner");
const systemAdminAuthHeaders = getAuthHeaders("system_admin");
const driverAuthHeaders = getAuthHeaders("driver");

const state = {
  role: initialRole,
  alerts: [],
  logs: [],
  distribution: {
    vehicles: [],
    selectedVehicleId: null,
    statusFilter: "",
    summary: {
      total: 0,
      online: 0,
      inTransit: 0,
      alerting: 0,
    },
  },
  selectedLogId: null,
  selectedAlertId: null,
  statusFilter: "",
  activeView: "dispatch",
  shipper: {
    shipmentId: "CGF-DEMO-001",
    snapshot: null,
    latest: null,
    eta: null,
    trajectory: null,
  },
  driver: {
    tasks: [],
    selectedTaskId: null,
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

const statusLabel = (vehicle) => {
  if (!vehicle) {
    return "-";
  }
  if (vehicle.alertSummary?.hasActiveAlert) {
    return "Active Alert";
  }
  if (vehicle.transportStatus === "in_transit") {
    return "In Transit";
  }
  return titleCase(vehicle.onlineStatus || vehicle.bindingStatus);
};

const requestJson = async (path, options = {}) => {
  const response = await fetch(`${apiBase}${path}`, {
    ...options,
    headers: {
      ...(options.authHeaders || getAuthHeaders(state.role)),
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

const loadDistribution = async () => {
  setStatus("Loading vehicle distribution", "loading");
  const query = state.distribution.statusFilter
    ? `?status=${encodeURIComponent(state.distribution.statusFilter)}`
    : "";
  const payload = await requestJson(`/api/dispatch/vehicle-distribution${query}`);
  state.distribution.vehicles = payload.vehicles || [];
  state.distribution.summary = payload.summary || {
    total: 0,
    online: 0,
    inTransit: 0,
    alerting: 0,
  };
  if (
    !state.distribution.vehicles.some(
      (vehicle) => vehicle.vehicleId === state.distribution.selectedVehicleId,
    )
  ) {
    state.distribution.selectedVehicleId =
      state.distribution.vehicles.find((vehicle) => vehicle.alertSummary?.hasActiveAlert)
        ?.vehicleId ||
      state.distribution.vehicles[0]?.vehicleId ||
      null;
  }
  renderDistribution();
  setStatus(`${payload.count} mapped vehicle${payload.count === 1 ? "" : "s"}`, "ok");
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

const loadDriverTasks = async () => {
  setStatus("Loading driver tasks", "loading");
  const payload = await requestJson("/api/driver/tasks", {
    authHeaders: driverAuthHeaders,
  });
  state.driver.tasks = payload.tasks || [];
  if (!state.driver.tasks.some((task) => task.taskId === state.driver.selectedTaskId)) {
    state.driver.selectedTaskId =
      state.driver.tasks.find((task) => task.summary?.unconfirmedCommandCount > 0)?.taskId ||
      state.driver.tasks[0]?.taskId ||
      null;
  }
  renderDriverWorkspace();
  setStatus(`${payload.count} driver task${payload.count === 1 ? "" : "s"}`, "ok");
};

const renderDistribution = () => {
  const summary = state.distribution.summary;
  setText("distribution-total", summary.total ?? 0);
  setText("distribution-online", summary.online ?? 0);
  setText("distribution-in-transit", summary.inTransit ?? 0);
  setText("distribution-alerting", summary.alerting ?? 0);
  setText(
    "distribution-summary-label",
    `${state.distribution.vehicles.length} visible · ${summary.alerting ?? 0} alerting`,
  );
  setText(
    "map-count",
    `${state.distribution.vehicles.length} vehicle${state.distribution.vehicles.length === 1 ? "" : "s"}`,
  );
  renderVehicleList();
  renderVehicleMap();
  renderSelectedVehicle();
};

const renderVehicleList = () => {
  const list = $("vehicle-list");
  list.replaceChildren();
  if (state.distribution.vehicles.length === 0) {
    list.append(emptyMessage("No vehicles match this distribution filter."));
    return;
  }
  state.distribution.vehicles.forEach((vehicle) => {
    const button = document.createElement("button");
    button.className = "vehicle-card";
    button.type = "button";
    button.dataset.vehicleId = vehicle.vehicleId;
    button.setAttribute(
      "aria-pressed",
      String(vehicle.vehicleId === state.distribution.selectedVehicleId),
    );

    const top = createElement("span", "vehicle-card-top");
    top.append(
      createElement("strong", "", vehicle.vehicleNumber),
      createElement("span", `badge ${vehicleBadgeClass(vehicle)}`, statusLabel(vehicle)),
    );
    const meta = createElement(
      "span",
      "vehicle-card-meta",
      `${vehicle.plateNumber} · ${vehicle.cargoId || "No cargo"}`,
    );
    const foot = createElement(
      "span",
      "vehicle-card-foot",
      `${formatCoordinate(vehicle.latestLocation)} · ${formatDate(vehicle.latestLocation?.updatedAt)}`,
    );
    button.append(top, meta, foot);
    button.addEventListener("click", () => {
      state.distribution.selectedVehicleId = vehicle.vehicleId;
      renderDistribution();
    });
    list.append(button);
  });
};

const renderVehicleMap = () => {
  const map = $("vehicle-map");
  map.replaceChildren();
  if (state.distribution.vehicles.length === 0) {
    map.append(emptyMessage("No vehicle coordinates are available for this filter."));
    return;
  }

  const bounds = coordinateBounds(state.distribution.vehicles);
  state.distribution.vehicles.forEach((vehicle) => {
    const marker = document.createElement("button");
    marker.className = `vehicle-marker ${vehicleBadgeClass(vehicle)}`;
    marker.type = "button";
    marker.dataset.vehicleId = vehicle.vehicleId;
    marker.setAttribute(
      "aria-label",
      `${vehicle.vehicleNumber} ${statusLabel(vehicle)} at ${formatCoordinate(vehicle.latestLocation)}`,
    );
    marker.setAttribute(
      "aria-pressed",
      String(vehicle.vehicleId === state.distribution.selectedVehicleId),
    );
    const position = mapPosition(vehicle.latestLocation, bounds);
    marker.style.left = `${position.x}%`;
    marker.style.top = `${position.y}%`;
    marker.append(
      createElement("span", "marker-dot", ""),
      createElement("strong", "", vehicle.vehicleNumber),
    );
    marker.addEventListener("click", () => {
      state.distribution.selectedVehicleId = vehicle.vehicleId;
      renderDistribution();
    });
    map.append(marker);
  });
};

const renderSelectedVehicle = () => {
  const vehicle = state.distribution.vehicles.find(
    (item) => item.vehicleId === state.distribution.selectedVehicleId,
  );
  if (!vehicle) {
    setText("selected-vehicle-number", "-");
    setText("selected-vehicle-plate", "-");
    setText("selected-vehicle-cargo", "-");
    setText("selected-vehicle-driver", "-");
    setText("selected-vehicle-location", "-");
    setText("selected-vehicle-updated", "-");
    setText("selected-vehicle-state", "-");
    $("selected-vehicle-state").className = "badge";
    $("open-vehicle-alert").disabled = true;
    return;
  }

  setText("selected-vehicle-number", vehicle.vehicleNumber);
  setText("selected-vehicle-plate", vehicle.plateNumber);
  setText("selected-vehicle-cargo", vehicle.cargoId || "-");
  setText("selected-vehicle-driver", vehicle.driverUserId || "-");
  setText("selected-vehicle-location", formatCoordinate(vehicle.latestLocation));
  setText("selected-vehicle-updated", formatDate(vehicle.latestLocation?.updatedAt));
  setText("selected-vehicle-state", statusLabel(vehicle));
  $("selected-vehicle-state").className = `badge ${vehicleBadgeClass(vehicle)}`;
  $("open-vehicle-alert").disabled = !vehicle.alertSummary?.hasActiveAlert;
};

const coordinateBounds = (vehicles) => {
  const longitudes = vehicles.map((vehicle) => Number(vehicle.latestLocation?.longitude));
  const latitudes = vehicles.map((vehicle) => Number(vehicle.latestLocation?.latitude));
  return {
    minLng: Math.min(...longitudes),
    maxLng: Math.max(...longitudes),
    minLat: Math.min(...latitudes),
    maxLat: Math.max(...latitudes),
  };
};

const mapPosition = (location, bounds) => {
  const longitudeRange = bounds.maxLng - bounds.minLng || 1;
  const latitudeRange = bounds.maxLat - bounds.minLat || 1;
  const x = 10 + ((Number(location.longitude) - bounds.minLng) / longitudeRange) * 80;
  const y = 90 - ((Number(location.latitude) - bounds.minLat) / latitudeRange) * 80;
  return {
    x: Math.max(7, Math.min(93, x)),
    y: Math.max(8, Math.min(92, y)),
  };
};

const vehicleBadgeClass = (vehicle) => {
  if (vehicle.alertSummary?.hasActiveAlert) {
    return vehicle.alertSummary.highestSeverity || "high";
  }
  if (vehicle.onlineStatus === "delayed") {
    return "delayed";
  }
  if (vehicle.onlineStatus === "online") {
    return "online";
  }
  return vehicle.onlineStatus || vehicle.bindingStatus || "offline";
};

const selectedDriverTask = () =>
  state.driver.tasks.find((task) => task.taskId === state.driver.selectedTaskId);

const renderDriverWorkspace = () => {
  renderDriverTaskList();
  renderSelectedDriverTask();
};

const renderDriverTaskList = () => {
  const list = $("driver-task-list");
  list.replaceChildren();
  const totalCommands = state.driver.tasks.reduce(
    (sum, task) => sum + (task.summary?.unconfirmedCommandCount || 0),
    0,
  );
  setText(
    "driver-task-summary",
    `${state.driver.tasks.length} active · ${totalCommands} requiring confirmation`,
  );
  if (state.driver.tasks.length === 0) {
    list.append(emptyMessage("No active transport tasks are assigned to this driver."));
    return;
  }

  state.driver.tasks.forEach((task) => {
    const button = document.createElement("button");
    button.className = "driver-task-card";
    button.type = "button";
    button.dataset.taskId = task.taskId;
    button.setAttribute("aria-pressed", String(task.taskId === state.driver.selectedTaskId));

    const top = createElement("span", "driver-task-card-top");
    top.append(
      createElement("strong", "", task.taskNumber),
      createElement("span", `badge ${task.transportStatus}`, titleCase(task.transportStatus)),
    );
    const meta = createElement(
      "span",
      "driver-task-card-meta",
      `${task.cargoNumber} · ${task.vehicleNumber}`,
    );
    const foot = createElement(
      "span",
      "driver-task-card-foot",
      `${task.origin} -> ${task.destination}`,
    );
    const commandHint = createElement(
      "span",
      "driver-task-card-meta",
      `${task.summary?.unconfirmedCommandCount || 0} pending command · ${task.summary?.reportCount || 0} reports`,
    );
    button.append(top, meta, foot, commandHint);
    button.addEventListener("click", () => {
      state.driver.selectedTaskId = task.taskId;
      renderDriverWorkspace();
    });
    list.append(button);
  });
};

const renderSelectedDriverTask = () => {
  const task = selectedDriverTask();
  if (!task) {
    setText("driver-task-number", "-");
    setText("driver-shipment-id", "-");
    setText("driver-cargo-name", "-");
    setText("driver-vehicle-label", "-");
    setText("driver-planned-arrival", "-");
    setText("driver-route-label", "-");
    setText("driver-task-status", "-");
    $("driver-task-status").className = "badge";
    $("submit-driver-report").disabled = true;
    renderDriverCommands([]);
    renderDriverReports([]);
    return;
  }

  setText("driver-task-number", task.taskNumber);
  setText("driver-shipment-id", task.shipmentId);
  setText("driver-cargo-name", task.cargoName || task.cargoNumber);
  setText("driver-vehicle-label", `${task.vehicleNumber} · ${task.plateNumber}`);
  setText("driver-planned-arrival", formatDate(task.plannedArrivalAt));
  setText("driver-route-label", `${task.origin} -> ${task.destination}`);
  setText("driver-task-status", titleCase(task.transportStatus));
  $("driver-task-status").className = `badge ${task.transportStatus}`;
  renderDriverCommands(task.commands || []);
  renderDriverReports(task.statusReports || []);
  syncDriverReportOptions(task);
  $("submit-driver-report").disabled = (task.summary?.nextAllowedReports || []).length === 0;
};

const renderDriverCommands = (commands) => {
  const list = $("driver-command-list");
  list.replaceChildren();
  setText("driver-command-count", `${commands.length} command${commands.length === 1 ? "" : "s"}`);
  if (commands.length === 0) {
    list.append(emptyMessage("No dispatch commands are assigned to this task."));
    return;
  }

  commands.forEach((command) => {
    const item = createElement("article", "driver-command-item");
    const main = createElement("div", "driver-command-main");
    main.append(
      createElement("span", `badge ${command.status}`, titleCase(command.status)),
      createElement("strong", "", command.commandNumber),
      createElement("p", "", command.content),
      createElement("small", "", `Issued ${formatDate(command.issuedAt)}`),
    );
    const action = createElement(
      "button",
      "neutral-button compact-button",
      driverCommandActionLabel(command),
    );
    action.type = "button";
    action.disabled = isTerminalDriverCommand(command);
    action.addEventListener("click", () => acknowledgeDriverCommand(command.commandId));
    item.append(main, action);
    list.append(item);
  });
};

const renderDriverReports = (reports) => {
  const list = $("driver-report-list");
  list.replaceChildren();
  setText("driver-report-count", `${reports.length} report${reports.length === 1 ? "" : "s"}`);
  if (reports.length === 0) {
    list.append(emptyMessage("No status reports have been submitted for this task."));
    return;
  }
  reports.forEach((report) => {
    const item = createElement("article", "driver-report-item");
    item.append(
      createElement("span", `badge ${report.reportStatus}`, titleCase(report.reportStatus)),
      createElement("strong", "", formatDate(report.reportedAt)),
      createElement("p", "", report.note || "No note"),
    );
    if ((report.attachmentUrls || []).length > 0) {
      item.append(createElement("small", "", `${report.attachmentUrls.length} attachment URL`));
    }
    list.append(item);
  });
};

const syncDriverReportOptions = (task) => {
  const allowed = task.summary?.nextAllowedReports || [];
  const select = $("driver-report-status");
  Array.from(select.options).forEach((option) => {
    option.disabled = allowed.length > 0 && !allowed.includes(option.value);
  });
  if (!allowed.includes(select.value) && allowed.length > 0) {
    select.value = allowed[0];
  }
};

const driverCommandActionLabel = (command) => {
  if (command.status === "acknowledged") {
    return "Confirmed";
  }
  if (command.status === "failed" || command.status === "revoked") {
    return titleCase(command.status);
  }
  return "Confirm";
};

const isTerminalDriverCommand = (command) =>
  ["acknowledged", "failed", "revoked"].includes(command.status);

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

const openAlertEntry = async (alertId) => {
  if (!alertId) {
    return;
  }
  state.statusFilter = "";
  document.querySelectorAll(".alert-filter-button").forEach((button) => {
    button.classList.toggle("is-active", button.dataset.status === "");
  });
  state.selectedAlertId = alertId;
  switchView("alert-handling");
  await loadAlerts();
  $("alert-handling-view").scrollIntoView({ block: "start", behavior: "auto" });
};

const openSelectedVehicleAlert = async () => {
  const vehicle = state.distribution.vehicles.find(
    (item) => item.vehicleId === state.distribution.selectedVehicleId,
  );
  const alertId = vehicle?.alertSummary?.alertIds?.[0];
  if (!alertId) {
    return;
  }
  await openAlertEntry(alertId);
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

const acknowledgeDriverCommand = async (commandId) => {
  setStatus("Confirming command", "loading");
  await requestJson(`/api/driver/commands/${encodeURIComponent(commandId)}/acknowledge`, {
    method: "POST",
    body: JSON.stringify({}),
    authHeaders: driverAuthHeaders,
  });
  await loadDriverTasks();
  setStatus("Command confirmed", "ok");
};

const submitDriverReport = async (event) => {
  event.preventDefault();
  const task = selectedDriverTask();
  if (!task) {
    return;
  }
  setStatus("Submitting driver report", "loading");
  const attachmentUrl = $("driver-attachment-url").value.trim();
  const payload = {
    reportStatus: $("driver-report-status").value,
    note: $("driver-report-note").value,
    attachmentUrls: attachmentUrl ? [attachmentUrl] : [],
  };
  await requestJson(`/api/driver/tasks/${encodeURIComponent(task.taskId)}/status-reports`, {
    method: "POST",
    body: JSON.stringify(payload),
    authHeaders: driverAuthHeaders,
  });
  $("driver-attachment-url").value = "";
  await loadDriverTasks();
  setStatus("Driver report submitted", "ok");
};

const updateRoleTabVisibility = () => {
  if (!navigation) {
    return;
  }
  const visibleEntries = navigation.visibleEntriesForRole(state.role) || [];
  const visibleViews = new Set(visibleEntries.map((entry) => entry.view || entry.id));
  document.querySelectorAll(".mode-tab").forEach((button) => {
    const view = button.dataset.view;
    button.style.display = visibleViews.has(view) ? "" : "none";
  });
  if (!visibleViews.has(state.activeView) && visibleEntries.length > 0) {
    switchView(visibleEntries[0].view || visibleEntries[0].id || "dispatch");
  }
};

const wireEvents = () => {
  document.querySelectorAll(".mode-tab").forEach((button) => {
    button.addEventListener("click", () => switchView(button.dataset.view));
  });
  $("role-select").addEventListener("change", (event) => {
    state.role = event.target.value;
    window.localStorage.setItem("cargoflowRole", state.role);
    updateRoleTabVisibility();
    switchView(navigation?.defaultEntryForRole(state.role)?.view || "dispatch");
  });
  $("refresh-distribution").addEventListener("click", () => loadDistribution().catch(handleError));
  $("refresh-alerts").addEventListener("click", () => loadAlerts().catch(handleError));
  $("refresh-logs").addEventListener("click", () => loadLogs().catch(handleError));
  $("refresh-shipper").addEventListener("click", () => loadShipperDetail().catch(handleError));
  $("refresh-driver").addEventListener("click", () => loadDriverTasks().catch(handleError));
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
  $("open-vehicle-alert").addEventListener("click", () =>
    openSelectedVehicleAlert().catch(handleError),
  );
  $("command-form").addEventListener("submit", (event) =>
    createCommand(event).catch(handleError),
  );
  $("close-form").addEventListener("submit", (event) =>
    closeAlert(event).catch(handleError),
  );
  $("driver-report-form").addEventListener("submit", (event) =>
    submitDriverReport(event).catch(handleError),
  );
  document.querySelectorAll(".alert-filter-button").forEach((button) => {
    button.addEventListener("click", () => {
      document
        .querySelectorAll(".alert-filter-button")
        .forEach((item) => item.classList.remove("is-active"));
      button.classList.add("is-active");
      state.statusFilter = button.dataset.status || "";
      state.selectedAlertId = null;
      loadAlerts().catch(handleError);
    });
  });
  document.querySelectorAll(".distribution-filter-button").forEach((button) => {
    button.addEventListener("click", () => {
      document
        .querySelectorAll(".distribution-filter-button")
        .forEach((item) => item.classList.remove("is-active"));
      button.classList.add("is-active");
      state.distribution.statusFilter = button.dataset.distributionStatus || "";
      state.distribution.selectedVehicleId = null;
      loadDistribution().catch(handleError);
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
  if (state.activeView === "dispatch" && state.distribution.vehicles.length === 0) {
    loadDistribution().catch(handleError);
  } else if (state.activeView === "dispatch") {
    setStatus(
      `${state.distribution.vehicles.length} mapped vehicle${state.distribution.vehicles.length === 1 ? "" : "s"}`,
      "ok",
    );
  }
  if (
    state.activeView === "alert-handling" &&
    state.alerts.length === 0 &&
    !state.selectedAlertId
  ) {
    loadAlerts().catch(handleError);
  } else if (state.activeView === "alert-handling") {
    setStatus(`${state.alerts.length} scoped alert${state.alerts.length === 1 ? "" : "s"}`, "ok");
  }
  if (state.activeView === "admin-logs" && state.logs.length === 0) {
    loadLogs().catch(handleError);
  }
  if (state.activeView === "driver" && state.driver.tasks.length === 0) {
    loadDriverTasks().catch(handleError);
  } else if (state.activeView === "driver") {
    setStatus(
      `${state.driver.tasks.length} driver task${state.driver.tasks.length === 1 ? "" : "s"}`,
      "ok",
    );
  }
  if (state.activeView === "shipper" && !state.shipper.snapshot) {
    loadShipperDetail().catch(handleError);
  } else if (state.activeView === "shipper" && state.shipper.snapshot) {
    setStatus(`${state.shipper.shipmentId} cargo detail ready`, "ok");
  }
};

const handleError = (error) => {
  setStatus(error.message, "error");
};

const populateRoleSelect = () => {
  const select = $("role-select");
  if (!navigation?.roleOrder) {
    return;
  }
  navigation.roleOrder.forEach((role) => {
    const profile = navigation.roleProfiles[role];
    const option = document.createElement("option");
    option.value = role;
    option.textContent = profile?.label || role;
    option.selected = role === state.role;
    select.append(option);
  });
};

$("role-select").value = initialRole;
populateRoleSelect();
updateRoleTabVisibility();
wireEvents();
loadDistribution().catch(handleError);
