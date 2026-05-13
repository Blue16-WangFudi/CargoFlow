const apiBase =
  new URLSearchParams(window.location.search).get("api") ||
  window.localStorage.getItem("cargoflowApiBase") ||
  "http://127.0.0.1:8000";

window.localStorage.setItem("cargoflowApiBase", apiBase);

const demoAuthHeaders = {
  "X-CargoFlow-User-Id": "owner-acme",
  "X-CargoFlow-Role": "cargo_owner",
  "X-CargoFlow-Tenant-Id": "cgf-demo",
};

const text = (id, value) => {
  document.getElementById(id).textContent = value;
};

const setStatus = (label, state) => {
  const status = document.getElementById("service-status");
  status.dataset.state = state;
  text("status-label", label);
};

const formatDate = (value) => {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return value;
  }
  return new Intl.DateTimeFormat(undefined, {
    dateStyle: "medium",
    timeStyle: "short",
  }).format(date);
};

async function loadConsole() {
  try {
    const healthResponse = await fetch(`${apiBase}/health`);
    if (!healthResponse.ok) {
      throw new Error(`Health check failed: ${healthResponse.status}`);
    }
    const health = await healthResponse.json();
    setStatus(`${health.service} ${health.status}`, "ok");

    const shipmentResponse = await fetch(`${apiBase}/api/shipments/demo`, {
      headers: demoAuthHeaders,
    });
    if (!shipmentResponse.ok) {
      throw new Error(`Shipment API failed: ${shipmentResponse.status}`);
    }
    const shipment = await shipmentResponse.json();
    text("shipment-id", shipment.shipmentId);
    text("shipment-status", shipment.cargo.status.replaceAll("_", " "));
    text("vehicle", shipment.vehicle.plateNumber);
    text("driver", shipment.vehicle.driver);
    text("eta", formatDate(shipment.eta.estimatedArrival));
    text("distance", `${shipment.eta.remainingDistanceKm} km remaining`);
    text("longitude", shipment.latestLocation.longitude.toFixed(4));
    text("latitude", shipment.latestLocation.latitude.toFixed(4));
    text("recorded-at", formatDate(shipment.latestLocation.recordedAt));
  } catch (error) {
    setStatus(error.message, "error");
  }
}

loadConsole();
