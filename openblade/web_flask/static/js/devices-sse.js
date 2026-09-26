(() => {
  const page = document.body.dataset.page;
  if (page !== "devices-index") {
    return;
  }

  const summaryNode = document.getElementById("device-summary");
  const warningNode = document.getElementById("sse-warning");
  const updateNode = document.getElementById("devices-last-update");
  if (!summaryNode || !warningNode || !updateNode) {
    return;
  }

  let retryDelayMs = 1000;
  let currentSource = null;

  function setWarning(visible) {
    warningNode.classList.toggle("hidden", !visible);
  }

  function updateSummary(summary) {
    ["online", "warning", "offline", "unknown", "total"].forEach((key) => {
      const node = summaryNode.querySelector(`[data-summary="${key}"]`);
      if (node && typeof summary[key] === "number") {
        node.textContent = String(summary[key]);
      }
    });
  }

  function updateDeviceCards(devices) {
    devices.forEach((device) => {
      const card = document.querySelector(`[data-device-id="${device.id}"]`);
      if (!card) {
        return;
      }
      const statusNode = card.querySelector('[data-device-field="status"]');
      const nameNode = card.querySelector('[data-device-field="name"]');
      if (statusNode) {
        statusNode.textContent = String(device.status || "unknown");
        statusNode.className = `status ${statusClass(device.status)}`;
      }
      if (nameNode) {
        nameNode.textContent = String(device.name || "Unknown");
      }
    });
  }

  function statusClass(status) {
    const normalized = String(status || "").toLowerCase();
    if (normalized === "online") {
      return "status-online";
    }
    if (normalized === "degraded" || normalized === "warning") {
      return "status-warning";
    }
    if (normalized === "offline" || normalized === "failed" || normalized === "error") {
      return "status-offline";
    }
    return "status-unknown";
  }

  function connect() {
    currentSource = new EventSource("/events/devices");

    currentSource.addEventListener("devices.snapshot", (event) => {
      try {
        const payload = JSON.parse(event.data);
        updateSummary(payload.summary || {});
        updateDeviceCards(payload.devices || []);
        setWarning(false);
        const timestamp = Number(payload.updated_at || 0);
        updateNode.textContent = timestamp > 0 ? new Date(timestamp * 1000).toLocaleTimeString() : "just now";
        retryDelayMs = 1000;
      } catch (_err) {
        setWarning(true);
      }
    });

    currentSource.addEventListener("devices.error", () => {
      setWarning(true);
    });

    currentSource.onerror = () => {
      setWarning(true);
      if (currentSource) {
        currentSource.close();
      }
      setTimeout(connect, retryDelayMs);
      retryDelayMs = Math.min(retryDelayMs * 2, 30000);
    };
  }

  connect();
})();

