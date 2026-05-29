const defaults = {
  baseUrl: window.location.origin,
  prefix: "/library-1",
};

const endpointCandidates = {
  library: ["/api/aml/library", "/aml/library"],
  inventory: ["/api/aml/library/inventory", "/aml/library/inventory"],
  drives: ["/api/aml/drives", "/aml/drives"],
  media: ["/api/aml/media", "/aml/media"],
  magazines: ["/api/aml/magazines", "/aml/magazines"],
};

const state = {
  token: "",
};

const elements = {
  baseUrl: document.querySelector("#api-base-url"),
  prefix: document.querySelector("#proxy-prefix"),
  username: document.querySelector("#username"),
  password: document.querySelector("#password"),
  token: document.querySelector("#token"),
  useBasicAuth: document.querySelector("#use-basic-auth"),
  loginButton: document.querySelector("#login-button"),
  refreshButton: document.querySelector("#refresh-button"),
  clearTokenButton: document.querySelector("#clear-token-button"),
  statusLine: document.querySelector("#status-line"),
  librarySummary: document.querySelector("#library-summary"),
  drivesSummary: document.querySelector("#drives-summary"),
  drivesTableBody: document.querySelector("#drives-table-body"),
  slotSummary: document.querySelector("#slot-summary"),
  slotGrid: document.querySelector("#slot-grid"),
  magazineSummary: document.querySelector("#magazine-summary"),
  magazinesTableBody: document.querySelector("#magazines-table-body"),
  docsLink: document.querySelector("#docs-link"),
  redocLink: document.querySelector("#redoc-link"),
  openapiLink: document.querySelector("#openapi-link"),
  docsMode: document.querySelector("#docs-mode"),
  docsFrame: document.querySelector("#api-docs-frame"),
  playgroundMethod: document.querySelector("#playground-method"),
  playgroundPath: document.querySelector("#playground-path"),
  playgroundBody: document.querySelector("#playground-body"),
  playgroundSend: document.querySelector("#playground-send"),
  playgroundResponse: document.querySelector("#playground-response"),
};

class ApiError extends Error {
  constructor(status, message, body) {
    super(message);
    this.status = status;
    this.body = body;
  }
}

function setStatus(message, kind = "neutral") {
  elements.statusLine.textContent = message;
  elements.statusLine.className = `status ${kind}`;
}

function normalizeBaseUrl(value) {
  return (value || "").trim().replace(/\/+$/, "");
}

function normalizePrefix(value) {
  const trimmed = (value || "").trim();
  if (!trimmed) {
    return "";
  }
  return trimmed.startsWith("/") ? trimmed.replace(/\/+$/, "") : `/${trimmed.replace(/\/+$/, "")}`;
}

function buildUrl(path) {
  const base = normalizeBaseUrl(elements.baseUrl.value);
  const prefix = normalizePrefix(elements.prefix.value);
  return `${base}${prefix}${path}`;
}

function buildAuthHeaders() {
  if (state.token) {
    return { Authorization: `Bearer ${state.token}` };
  }
  if (elements.useBasicAuth.checked) {
    const username = elements.username.value.trim();
    const password = elements.password.value;
    if (username) {
      return { Authorization: `Basic ${btoa(`${username}:${password}`)}` };
    }
  }
  return {};
}

async function requestJson(url, options = {}) {
  const controller = new AbortController();
  const timeoutId = window.setTimeout(() => controller.abort(), 9000);
  try {
    const response = await fetch(url, {
      ...options,
      signal: controller.signal,
      headers: {
        Accept: "application/json",
        ...(options.headers || {}),
      },
    });
    const text = await response.text();
    let payload;
    try {
      payload = text ? JSON.parse(text) : {};
    } catch {
      payload = { raw: text };
    }
    if (!response.ok) {
      const detail = payload?.error || payload?.message || payload?.result?.text || response.statusText;
      throw new ApiError(response.status, `HTTP ${response.status}: ${detail}`, payload);
    }
    return payload;
  } catch (error) {
    if (error.name === "AbortError") {
      throw new Error(`Request timeout for ${url}`);
    }
    throw error;
  } finally {
    window.clearTimeout(timeoutId);
  }
}

async function requestWithFallback(paths, options = {}) {
  let latestError = null;
  for (const path of paths) {
    try {
      return await requestJson(buildUrl(path), options);
    } catch (error) {
      latestError = error;
      if (error instanceof ApiError && ![401, 403, 404].includes(error.status)) {
        throw error;
      }
    }
  }
  throw latestError || new Error("Request failed");
}

function isObject(value) {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

function unwrapPayload(payload) {
  if (!isObject(payload)) {
    return payload;
  }

  let current = payload;
  if (Array.isArray(current.data)) {
    const values = current.data
      .map((entry) => (isObject(entry) && "value" in entry ? entry.value : entry))
      .filter((entry) => entry !== undefined && entry !== null);
    if (values.length === 1) {
      current = values[0];
    } else if (values.length > 1 && values.every(isObject)) {
      current = Object.assign({}, ...values);
    } else if (values.length > 0) {
      current = values;
    }
  }

  if (isObject(current) && Object.keys(current).length === 1 && "value" in current) {
    return current.value;
  }

  return current;
}

function extractList(payload, candidateKeys) {
  const data = unwrapPayload(payload);
  if (Array.isArray(data)) {
    return data;
  }
  if (!isObject(data)) {
    return [];
  }
  for (const key of candidateKeys) {
    const value = data[key];
    if (Array.isArray(value)) {
      return value;
    }
    if (isObject(value)) {
      if (Array.isArray(value.drive)) {
        return value.drive;
      }
      if (Array.isArray(value.slot)) {
        return value.slot;
      }
      if (Array.isArray(value.magazine)) {
        return value.magazine;
      }
      const nestedArray = Object.values(value).find((item) => Array.isArray(item));
      if (Array.isArray(nestedArray)) {
        return nestedArray;
      }
    }
  }
  return [];
}

function parseSlotCoordinate(value) {
  const raw = String(value || "").trim();
  const parts = raw.split(",");
  if (parts.length !== 3) {
    return null;
  }
  const [libraryRaw, bayRaw, slotRaw] = parts;
  const library = Number(libraryRaw);
  const bay = Number(bayRaw);
  const slot = Number(slotRaw);
  if (![library, bay, slot].every((item) => Number.isInteger(item) && item > 0)) {
    return null;
  }
  return { library, bay, slot };
}

function getSlotNumber(slot, index) {
  const coordinate = parseSlotCoordinate(slot?.slotAddress || slot?.address || slot?.location);
  if (coordinate) {
    const slotsPerBay = 25;
    return (coordinate.bay - 1) * slotsPerBay + coordinate.slot;
  }
  const raw = slot?.slot || slot?.slotId || slot?.id || slot?.location || slot?.address || index + 1;
  const numeric = Number(raw);
  return Number.isFinite(numeric) ? numeric : index + 1;
}

function getSlotCoordinate(slot) {
  const direct = slot?.slotAddress || slot?.address || slot?.location || "";
  const parsed = parseSlotCoordinate(direct);
  if (parsed) {
    return `${parsed.library},${parsed.bay},${parsed.slot}`;
  }

  const slotId = Number(slot?.slot || slot?.slotId || slot?.id);
  if (Number.isInteger(slotId) && slotId > 0) {
    const bay = slotId <= 25 ? 1 : 2;
    const slotInBay = bay === 1 ? slotId : slotId - 25;
    return `1,${bay},${slotInBay}`;
  }
  return "";
}

function getDriveName(drive, index) {
  return String(drive?.name || drive?.driveId || drive?.id || `drive-${index + 1}`);
}

function getDriveStatus(drive) {
  return String(drive?.status || drive?.state || (drive?.online ? "online" : "unknown"));
}

function getBarcode(item) {
  return item?.barcode || item?.label || item?.volumeLabel || item?.tapeBarcode || item?.loadedBarcode || "";
}

function renderLibrarySummary(libraryPayload, slots, drives, media) {
  const raw = unwrapPayload(libraryPayload);
  const library = isObject(raw?.library) ? raw.library : (isObject(raw) ? raw : {});
  const occupied = slots.filter((slot) => Boolean(slot?.occupied) || Boolean(getBarcode(slot))).length;
  const totalSlots = Number(library?.slotsTotal || slots.length || 0);
  const data = [
    ["Library ID", library?.libraryId || library?.id || "-"],
    ["Library name", library?.name || library?.libraryName || "-"],
    ["Status", library?.status || library?.state || "unknown"],
    ["Slots total", totalSlots || "-"],
    ["Slots occupied", library?.slotsOccupied ?? occupied],
    ["Slots empty", library?.slotsEmpty ?? (totalSlots > 0 ? totalSlots - occupied : "-")],
    ["Drives total", drives.length],
    ["Media records", media.length],
  ];

  elements.librarySummary.innerHTML = data.map(([key, value]) => `<div><dt>${key}</dt><dd>${value}</dd></div>`).join("");
}

function renderDrives(drives) {
  const loadedCount = drives.filter((drive) => Boolean(drive?.loaded) || Boolean(getBarcode(drive))).length;
  const onlineCount = drives.filter((drive) => {
    const status = getDriveStatus(drive).toLowerCase();
    return status.includes("online") || status.includes("ready") || drive?.online === true;
  }).length;
  elements.drivesSummary.textContent = `${drives.length} drives, ${loadedCount} loaded, ${onlineCount} online/ready`;

  if (drives.length === 0) {
    elements.drivesTableBody.innerHTML = "<tr><td colspan='4'>No drives found.</td></tr>";
    return;
  }

  elements.drivesTableBody.innerHTML = drives
    .map((drive, index) => {
      const barcode = getBarcode(drive) || "-";
      const loaded = Boolean(drive?.loaded) || Boolean(getBarcode(drive)) ? "yes" : "no";
      return `<tr>
        <td>${getDriveName(drive, index)}</td>
        <td>${getDriveStatus(drive)}</td>
        <td>${loaded}</td>
        <td>${barcode}</td>
      </tr>`;
    })
    .join("");
}

function renderSlots(slots, media) {
  const mediaByCoordinate = new Map();
  media.forEach((item) => {
    const coordinate = getSlotCoordinate(item);
    if (coordinate) {
      mediaByCoordinate.set(coordinate, getBarcode(item));
    }
  });

  const sortedSlots = slots
    .map((slot, index) => ({ ...slot, __slotNumber: getSlotNumber(slot, index) }))
    .sort((a, b) => a.__slotNumber - b.__slotNumber);

  const occupiedCount = sortedSlots.filter((slot) => {
    const coordinate = getSlotCoordinate(slot);
    const barcode = getBarcode(slot) || mediaByCoordinate.get(coordinate) || "";
    return Boolean(slot?.occupied) || Boolean(barcode);
  }).length;
  elements.slotSummary.textContent = `${sortedSlots.length} slots, ${occupiedCount} occupied, ${sortedSlots.length - occupiedCount} empty`;

  if (sortedSlots.length === 0) {
    elements.slotGrid.innerHTML = "<p>No slot layout data available.</p>";
    return;
  }

  elements.slotGrid.innerHTML = sortedSlots
    .map((slot) => {
      const coordinate = getSlotCoordinate(slot);
      const barcode = getBarcode(slot) || mediaByCoordinate.get(coordinate) || "";
      const occupied = Boolean(slot?.occupied) || Boolean(barcode);
      return `<div class="slot ${occupied ? "occupied" : "empty"}">
        <div class="slot-id">Slot ${slot.__slotNumber}</div>
        <div class="coordinate">${coordinate || "-"}</div>
        <div class="barcode">${barcode || "EMPTY"}</div>
      </div>`;
    })
    .join("");
}

function renderMagazines(magazines) {
  if (!Array.isArray(magazines) || magazines.length === 0) {
    elements.magazineSummary.textContent = "No magazines found.";
    elements.magazinesTableBody.innerHTML = "<tr><td colspan='6'>No magazines found.</td></tr>";
    return;
  }

  const occupied = magazines.reduce((sum, item) => sum + Number(item?.occupiedSlots || 0), 0);
  const total = magazines.reduce((sum, item) => sum + Number(item?.slotCount || 0), 0);
  elements.magazineSummary.textContent = `${magazines.length} magazines, ${occupied}/${total} occupied`;

  elements.magazinesTableBody.innerHTML = magazines
    .map((magazine, index) => {
      const id = String(magazine?.id || `MAG-${index + 1}`);
      const bay = magazine?.bay ?? "-";
      const slotCount = Number(magazine?.slotCount || 0);
      const occupiedSlots = Number(magazine?.occupiedSlots || 0);
      const slotAddresses = Array.isArray(magazine?.slotAddresses) ? magazine.slotAddresses : [];
      const range = slotAddresses.length > 0 ? `${slotAddresses[0]} → ${slotAddresses[slotAddresses.length - 1]}` : "-";
      const barcodeRule = magazine?.barcodeFormat || "^[A-Z0-9]{8}$";
      return `<tr>
        <td>${id}</td>
        <td>${bay}</td>
        <td>${slotCount}</td>
        <td>${occupiedSlots}</td>
        <td>${range}</td>
        <td><code>${barcodeRule}</code></td>
      </tr>`;
    })
    .join("");
}

function updateDocsLinks() {
  elements.docsLink.href = buildUrl("/docs");
  elements.redocLink.href = buildUrl("/redoc");
  elements.openapiLink.href = buildUrl("/openapi.json");
  const docsPath = elements.docsMode && elements.docsMode.value === "redoc" ? "/redoc" : "/docs";
  if (elements.docsFrame) {
    elements.docsFrame.src = buildUrl(docsPath);
  }
}

async function runPlaygroundRequest() {
  const method = elements.playgroundMethod.value.toUpperCase();
  const path = elements.playgroundPath.value.trim();
  if (!path.startsWith("/")) {
    elements.playgroundResponse.textContent = "Path must start with '/'.";
    return;
  }

  const headers = { ...buildAuthHeaders() };
  const options = { method, headers };
  if (!["GET", "DELETE"].includes(method)) {
    const rawBody = elements.playgroundBody.value.trim();
    if (rawBody) {
      try {
        const parsed = JSON.parse(rawBody);
        options.body = JSON.stringify(parsed);
        options.headers["Content-Type"] = "application/json";
      } catch {
        elements.playgroundResponse.textContent = "Invalid JSON body.";
        return;
      }
    }
  }

  elements.playgroundSend.disabled = true;
  elements.playgroundResponse.textContent = "Sending request...";
  try {
    const payload = await requestJson(buildUrl(path), options);
    elements.playgroundResponse.textContent = JSON.stringify(payload, null, 2);
  } catch (error) {
    if (error instanceof ApiError) {
      elements.playgroundResponse.textContent = `HTTP ${error.status}\n${JSON.stringify(error.body, null, 2)}`;
    } else {
      elements.playgroundResponse.textContent = String(error.message || error);
    }
  } finally {
    elements.playgroundSend.disabled = false;
  }
}

async function login() {
  const username = elements.username.value.trim();
  const password = elements.password.value;
  if (!username) {
    setStatus("Username is required for login.", "error");
    return;
  }

  elements.loginButton.disabled = true;
  setStatus("Logging in...", "neutral");
  try {
    const payload = await requestWithFallback(
      ["/api/aml/auth/login", "/aml/auth/login"],
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ username, password }),
      },
    );
    const token = payload?.token || payload?.access_token || payload?.sessionToken;
    if (!token) {
      throw new Error("Login response did not contain a token.");
    }
    state.token = String(token);
    elements.token.value = state.token;
    setStatus("Login succeeded.", "ok");
  } catch (error) {
    setStatus(`Login failed: ${error.message}`, "error");
  } finally {
    elements.loginButton.disabled = false;
  }
}

async function refresh() {
  state.token = elements.token.value.trim();
  elements.refreshButton.disabled = true;
  setStatus("Fetching AML status...", "neutral");
  updateDocsLinks();

  const headers = buildAuthHeaders();
  const requests = [
    requestWithFallback(endpointCandidates.library, { headers }),
    requestWithFallback(endpointCandidates.inventory, { headers }),
    requestWithFallback(endpointCandidates.drives, { headers }),
    requestWithFallback(endpointCandidates.media, { headers }),
    requestWithFallback(endpointCandidates.magazines, { headers }),
  ];

  const [libraryResult, inventoryResult, drivesResult, mediaResult, magazinesResult] = await Promise.allSettled(requests);
  const errors = [];

  if (libraryResult.status === "rejected") {
    errors.push(`library: ${libraryResult.reason.message}`);
  }
  if (inventoryResult.status === "rejected") {
    errors.push(`inventory: ${inventoryResult.reason.message}`);
  }
  if (drivesResult.status === "rejected") {
    errors.push(`drives: ${drivesResult.reason.message}`);
  }
  if (mediaResult.status === "rejected") {
    errors.push(`media: ${mediaResult.reason.message}`);
  }
  if (magazinesResult.status === "rejected") {
    errors.push(`magazines: ${magazinesResult.reason.message}`);
  }

  const inventoryPayload = inventoryResult.status === "fulfilled" ? inventoryResult.value : {};
  const drivesPayload = drivesResult.status === "fulfilled" ? drivesResult.value : {};
  const mediaPayload = mediaResult.status === "fulfilled" ? mediaResult.value : {};
  const magazinesPayload = magazinesResult.status === "fulfilled" ? magazinesResult.value : {};
  const slots = extractList(inventoryPayload, ["slots", "slotList", "storageSlots", "librarySlots"]);
  const drives = extractList(drivesPayload, ["drives", "tapeDrives", "driveList"]);
  const media = extractList(mediaPayload, ["media", "cartridges", "tapes", "items", "volumes"]);
  const magazines = extractList(magazinesPayload, ["magazines", "magazineList", "magazine"]);

  renderLibrarySummary(libraryResult.status === "fulfilled" ? libraryResult.value : {}, slots, drives, media);
  renderDrives(drives);
  renderSlots(slots, media);
  renderMagazines(magazines);

  if (errors.length > 0) {
    const hasAuthError = [libraryResult, inventoryResult, drivesResult, mediaResult, magazinesResult].some(
      (result) => result.status === "rejected" && result.reason instanceof ApiError && [401, 403].includes(result.reason.status),
    );
    if (hasAuthError) {
      setStatus(`Auth failed: ${errors.join(" | ")}`, "error");
    } else {
      setStatus(`Partial data loaded: ${errors.join(" | ")}`, "error");
    }
  } else {
    setStatus("All AML endpoints loaded.", "ok");
  }
  elements.refreshButton.disabled = false;
}

function clearToken() {
  state.token = "";
  elements.token.value = "";
  setStatus("Token cleared.", "neutral");
}

function init() {
  elements.baseUrl.value = defaults.baseUrl;
  elements.prefix.value = defaults.prefix;
  updateDocsLinks();

  elements.loginButton.addEventListener("click", login);
  elements.refreshButton.addEventListener("click", refresh);
  elements.clearTokenButton.addEventListener("click", clearToken);
  elements.playgroundSend.addEventListener("click", runPlaygroundRequest);
  elements.token.addEventListener("change", () => {
    state.token = elements.token.value.trim();
  });
  elements.baseUrl.addEventListener("change", updateDocsLinks);
  elements.prefix.addEventListener("change", updateDocsLinks);
  if (elements.docsMode) {
    elements.docsMode.addEventListener("change", updateDocsLinks);
  }

  refresh().catch((error) => {
    setStatus(`Initial refresh failed: ${error.message}`, "error");
  });
}

init();
