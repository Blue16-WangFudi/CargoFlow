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

const initialEntryId =
  urlParams.get("entry") ||
  window.localStorage.getItem("cargoflowEntry") ||
  navigation.defaultEntryForRole(initialRole)?.id ||
  "";

const state = {
  role: initialRole,
  entryId: initialEntryId,
  alerts: [],
  selectedAlertId: null,
  statusFilter: "",
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

const show = (id, visible) => {
  $(id).hidden = !visible;
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

const requestJson = async (path, options = {}) => {
  const response = await fetch(`${apiBase}${path}`, {
    ...options,
    headers: {
      ...navigation.authHeadersForRole(state.role),
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

const persistWorkspace = () => {
  window.localStorage.setItem("cargoflowRole", state.role);
  window.localStorage.setItem("cargoflowEntry", state.entryId);
};

const renderRoleOptions = () => {
  const select = $("role-select");
  select.replaceChildren();
  navigation.roleOrder.forEach((role) => {
    const profile = navigation.roleProfiles[role];
    const option = document.createElement("option");
    option.value = role;
    option.textContent = profile.label;
    option.selected = role === state.role;
    select.append(option);
  });
};

const renderRoleProfile = () => {
  const profile = navigation.roleProfiles[state.role];
  const container = $("role-profile");
  container.replaceChildren();
  if (!profile) {
    container.append(emptyMessage("Unknown role. Choose an authorized CargoFlow role."));
    return;
  }

  container.append(
    createElement("span", "badge", profile.shortLabel),
    createElement("p", "", profile.description),
  );
};

const renderEntryList = (allowedEntries) => {
  const list = $("entry-list");
  list.replaceChildren();
  if (allowedEntries.length === 0) {
    list.append(emptyMessage("No entries are assigned to this role."));
    return;
  }

  allowedEntries.forEach((entry) => {
    const button = document.createElement("button");
    button.className = "entry-card";
    button.type = "button";
    button.dataset.entryId = entry.id;
    button.setAttribute("aria-current", String(entry.id === state.entryId));
    button.append(
      createElement("span", "entry-card-eyebrow", entry.eyebrow),
      createElement("strong", "", entry.label),
      createElement("span", "entry-card-meta", entry.status),
    );
    button.addEventListener("click", () => {
      state.entryId = entry.id;
      state.selectedAlertId = null;
      persistWorkspace();
      renderWorkspace().catch(handleError);
    });
    list.append(button);
  });
};

const renderEntryOverview = (entry) => {
  const overview = $("entry-overview");
  overview.replaceChildren();
  if (!entry) {
    return;
  }

  const summary = createElement("article", "info-panel entry-summary");
  summary.append(
    createElement("span", "badge", entry.status),
    createElement("h3", "", entry.label),
    createElement("p", "", entry.summary),
  );

  const endpoint = createElement("dl", "info-list");
  const endpointGroup = document.createElement("div");
  endpointGroup.append(
    createElement("dt", "", "API Contract"),
    createElement("dd", "", entry.endpoint),
  );
  const roleGroup = document.createElement("div");
  roleGroup.append(
    createElement("dt", "", "Visible To"),
    createElement(
      "dd",
      "",
      entry.roles.map((role) => navigation.roleProfiles[role].label).join(", "),
    ),
  );
  endpoint.append(endpointGroup, roleGroup);

  overview.append(summary, endpoint);
};

const renderDenied = (resolution) => {
  show("access-denied", true);
  renderEntryOverview(null);
  setText("detail-eyebrow", "Permission Boundary");
  setText("detail-title", "Unauthorized Entry");
  setText("alert-summary", navigation.roleProfiles[state.role]?.label || "Unknown role");
  const requested = resolution.entry?.label || state.entryId || "requested entry";
  if (resolution.reason === "unknown_role") {
    setText("denied-title", "Unknown role");
    setText("denied-message", "Choose a CargoFlow role before opening a workspace entry.");
  } else {
    setText("denied-title", `${requested} is not available`);
    setText(
      "denied-message",
      `${navigation.roleProfiles[state.role].label} can only open the entries listed in the navigation panel.`,
    );
  }
  renderAlertShell(false);
  setStatus("Entry denied by role rules", "error");
};

const renderAllowedEntry = async (entry) => {
  show("access-denied", false);
  setText("detail-eyebrow", entry.eyebrow);
  setText("detail-title", entry.label);
  setText("alert-summary", navigation.roleProfiles[state.role].label);
  renderEntryOverview(entry);
  renderAlertShell(entry.id === "dispatch-alerts");

  if (entry.id === "dispatch-alerts") {
    await loadAlerts();
    return;
  }

  setStatus(`${entry.label} visible for ${navigation.roleProfiles[state.role].shortLabel}`, "ok");
};

const renderAlertShell = (visible) => {
  show("dispatch-workspace", visible);
  show("alert-detail-grid", visible);
  show("alert-workflow", visible);
  show("command-panel", visible);
  if (!visible) {
    state.alerts = [];
    state.selectedAlertId = null;
    $("alert-list").replaceChildren();
    renderEmptyDetail();
  }
};

const renderWorkspace = async () => {
  renderRoleOptions();
  renderRoleProfile();
  const resolution = navigation.resolveWorkspaceEntry(state.role, state.entryId);
  renderEntryList(resolution.allowedEntries);
  if (!resolution.authorized) {
    renderDenied(resolution);
    return;
  }

  state.entryId = resolution.entry?.id || "";
  persistWorkspace();
  renderEntryList(resolution.allowedEntries);
  await renderAllowedEntry(resolution.entry);
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

const renderAlertList = () => {
  const list = $("alert-list");
  list.replaceChildren();
  if (state.alerts.length === 0) {
    list.append(emptyMessage("No scoped alerts match this filter."));
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
    meta.textContent = `${titleCase(alert.alertType)} - ${alert.vehicleId}`;

    const foot = document.createElement("span");
    foot.className = "alert-card-foot";
    foot.textContent = `${titleCase(alert.status)} - ${formatDate(alert.triggeredAt)}`;

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
  setText("alert-summary", `${alert.alertNumber} - ${titleCase(alert.alertType)}`);
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

const emptyMessage = (message) => {
  const element = document.createElement("p");
  element.className = "empty-state";
  element.textContent = message;
  return element;
};

const createCommand = async (event) => {
  event.preventDefault();
  if (!state.selectedAlertId || state.entryId !== "dispatch-alerts") {
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
  if (!state.selectedAlertId || state.entryId !== "dispatch-alerts") {
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
  $("refresh-workspace").addEventListener("click", () => renderWorkspace().catch(handleError));
  $("role-select").addEventListener("change", (event) => {
    state.role = event.target.value;
    state.entryId = navigation.defaultEntryForRole(state.role)?.id || "";
    state.selectedAlertId = null;
    persistWorkspace();
    renderWorkspace().catch(handleError);
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

const handleError = (error) => {
  setStatus(error.message, "error");
};

wireEvents();
renderWorkspace().catch(handleError);
