const state = {
  disks: [],
  selectedDisk: null,
  selectedDiskNames: new Set(),
  currentJobId: null,
  pollTimer: null,
  activeJobsTimer: null,
  activeJobs: [],
  controllers: null,
  selectedDiskJobs: [],
  smartChart: null,
  selectedMode: "quick",
  selectedComplianceProfile: "resale_basic",
  complianceProfiles: {},
  secureErase: null,
  nvmeErase: null,
  selftest: null,
  themeMode: "system",
  enterprise: null,
  settings: {
    showInternalDisks: true,
    enableDestructive: false,
    allowInternalErase: false,
  },
};

const MODE_HINTS = {
  quick: "Short sample read test for initial sorting.",
  deep_sample: "Distributed read test across the drive. More useful for HDDs than SSD/NVMe.",
  smart_short: "Short drive self-test. Good for SSD/NVMe and quick pre-checks.",
  smart_extended: "Real SMART Extended self-test executed by the drive. Credible for resale.",
  full: "Full read test. Takes longer and provides the strongest read-test claim for resale.",
  erase_zero: "Single-pass zero write. Destructive.",
  secure_erase_ata: "ATA Secure Erase. Destructive.",
  secure_erase_ata_enhanced: "ATA Enhanced Secure Erase. Destructive.",
  nvme_format: "NVMe Format NVM user-data erase. Destructive.",
  nvme_sanitize_crypto: "NVMe Sanitize Crypto Erase. Destructive.",
  nvme_sanitize_block: "NVMe Sanitize Block Erase. Destructive.",
  smart_extended_external: "SMART self-test started outside the app.",
};

const MODE_LABELS = {
  quick: "Quick",
  deep_sample: "Deep Sample",
  smart_short: "SMART Short",
  smart_extended: "SMART Extended",
  full: "Full Read",
  erase_zero: "Zero Erase",
  secure_erase_ata: "ATA Secure Erase",
  secure_erase_ata_enhanced: "ATA Enhanced Secure Erase",
  nvme_format: "NVMe Format Erase",
  nvme_sanitize_crypto: "NVMe Sanitize Crypto",
  nvme_sanitize_block: "NVMe Sanitize Block",
  smart_extended_external: "External SMART Test",
};

const OVERVIEW_LABELS = {
  summary: "Status",
  kind: "Type",
  interface: "Interface",
  capacity: "Capacity",
  serial: "Serial",
  powerOnHours: "Power-On Hours",
  temperature: "Temperature",
  reallocated: "Reallocated",
  pending: "Pending",
  offlineUncorrectable: "Offline Uncorrectable",
  crcErrors: "CRC Errors",
  mediaErrors: "Media Errors",
};

function formatBytes(bytes) {
  if (!bytes && bytes !== 0) return "n/a";
  const units = ["B", "KB", "MB", "GB", "TB"];
  let value = bytes;
  let unit = units[0];
  for (const current of units) {
    unit = current;
    if (value < 1024 || current === units.at(-1)) break;
    value /= 1024;
  }
  return `${value.toFixed(1)} ${unit}`;
}

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function formatTemperature(value) {
  const numeric = Number(value);
  if (!Number.isFinite(numeric)) return "n/a";
  const fahrenheit = Math.round((numeric * 9) / 5 + 32);
  return `${numeric} °C / ${fahrenheit} °F`;
}

function byId(id) {
  return document.getElementById(id);
}

function pageName() {
  return document.body.dataset.page || "dashboard";
}

function resolvedTheme(mode) {
  if (mode === "dark" || mode === "light") return mode;
  return window.matchMedia("(prefers-color-scheme: light)").matches ? "light" : "dark";
}

function updateThemeButtons() {
  const mapping = {
    system: byId("themeSystem"),
    dark: byId("themeDark"),
    light: byId("themeLight"),
  };
  Object.entries(mapping).forEach(([mode, button]) => {
    button.classList.toggle("active", state.themeMode === mode);
  });
}

function applyTheme(mode) {
  state.themeMode = mode;
  localStorage.setItem("themeMode", mode);
  document.body.dataset.theme = resolvedTheme(mode);
  updateThemeButtons();
}

function initTheme() {
  const stored = localStorage.getItem("themeMode") || "system";
  applyTheme(stored);
  window.matchMedia("(prefers-color-scheme: light)").addEventListener("change", () => {
    if (state.themeMode === "system") {
      document.body.dataset.theme = resolvedTheme("system");
    }
  });
}

function loadSettings() {
  try {
    const raw = localStorage.getItem("safetySettings");
    if (!raw) {
      persistSettings();
      return;
    }
    const parsed = JSON.parse(raw);
    state.settings = { ...state.settings, ...parsed };
  } catch (_) {
    // ignore broken local storage
  }
}

function persistSettings() {
  localStorage.setItem("safetySettings", JSON.stringify(state.settings));
}

function loadComplianceSelection() {
  state.selectedComplianceProfile = localStorage.getItem("complianceProfile") || "resale_basic";
}

async function loadComplianceProfiles() {
  const payload = await fetchJson("/api/compliance-profiles");
  state.complianceProfiles = payload.profiles || {};
  renderComplianceProfiles();
}

async function loadSystemTools() {
  const payload = await fetchJson("/api/system-tools");
  const container = byId("systemToolsList");
  if (!container) return;
  const tools = payload.tools || {};
  container.innerHTML = "";
  for (const [name, tool] of Object.entries(tools)) {
    const label = name === "browser" ? "Browser / PDF engine" : name;
    const featureCount = Object.values(tool.features || {}).filter((feature) => feature.available).length;
    const totalFeatures = Object.keys(tool.features || {}).length;
    const row = document.createElement("div");
    row.className = `system-tool-row ${tool.installed ? "ok" : "missing"}`;
    row.innerHTML = `
      <div>
        <strong>${label}</strong>
        <span>${tool.version || "unknown"} · min ${tool.minimum_version}${tool.path ? ` · ${tool.path}` : ""}</span>
      </div>
      <span class="badge ${tool.installed ? "ok" : "danger"}">${tool.installed ? `${featureCount}/${totalFeatures}` : "missing"}</span>
    `;
    container.appendChild(row);
  }
}

async function loadNetworkConfig() {
  const status = byId("networkConfigStatus");
  if (!status) return;
  const payload = await fetchJson("/api/network-config");
  const config = payload.config || {};
  byId("networkIp").value = config.ip || "";
  byId("networkGw").value = config.gw || "";
  byId("networkDns").value = config.dns || "";
  status.textContent = payload.exists
    ? `saved to ${payload.path}`
    : payload.available
      ? "DHCP default. No static config saved."
      : `network config unavailable · ${payload.error}`;
}

async function saveNetworkConfig(config) {
  const payload = await fetchJson("/api/network-config", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(config),
  });
  byId("networkConfigStatus").textContent = payload.deleted
    ? "Static config removed. DHCP will be used on next boot."
    : `saved to ${payload.path}`;
  await loadNetworkConfig();
}

async function loadVendorTools() {
  const container = byId("vendorToolsList");
  if (!container) return;
  const payload = await fetchJson("/api/vendor-tools");
  container.innerHTML = "";
  for (const item of payload.catalog || []) {
    const row = document.createElement("div");
    row.className = `system-tool-row ${item.installed ? "ok" : "missing"}`;
    row.innerHTML = `
      <div>
        <strong>${item.label}</strong>
        <span>${item.installed ? "installed" : `${item.license_note} Requires a writable Linux tools partition.`}</span>
      </div>
      <button class="mini-action" type="button">${item.installed ? "Info" : "Get"}</button>
    `;
    row.querySelector("button").onclick = () => prepareVendorToolDownload(item.id);
    container.appendChild(row);
  }
  if (payload.tools?.length) {
    const installed = document.createElement("div");
    installed.className = "empty-inline muted";
    installed.textContent = `Detected: ${payload.tools.map((tool) => `${tool.name} at ${tool.path}`).join("; ")}`;
    container.appendChild(installed);
  }
}

async function prepareVendorToolDownload(toolId) {
  const accepted = window.confirm("Open vendor download information? Continue only if you are allowed to download and use this vendor tool under its license terms.");
  if (!accepted) return;
  const payload = await fetchJson(`/api/vendor-tools/${toolId}/download-info`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ accepted_terms: true }),
  });
  window.open(payload.download_url, "_blank", "noopener");
  byId("vendorToolsList").insertAdjacentHTML(
    "afterbegin",
    `<div class="empty-inline muted">Place extracted binary as ${payload.expected_names.join(" or ")} in ${payload.target_directory}</div>`
  );
}

function renderComplianceProfiles() {
  const select = byId("complianceProfile");
  if (!select) return;
  const entries = Object.entries(state.complianceProfiles);
  select.innerHTML = "";
  for (const [id, profile] of entries) {
    const option = document.createElement("option");
    option.value = id;
    option.textContent = profile.label || id;
    select.appendChild(option);
  }
  if (!state.complianceProfiles[state.selectedComplianceProfile]) {
    state.selectedComplianceProfile = entries[0]?.[0] || "resale_basic";
  }
  select.value = state.selectedComplianceProfile;
  updateComplianceHint();
}

function updateComplianceHint() {
  const profile = state.complianceProfiles[state.selectedComplianceProfile];
  byId("complianceProfileHint").textContent = profile
    ? `${profile.standard} · ${profile.description}`
    : "Resale diagnostic report.";
}

function syncSettingsInputs() {
  byId("showInternalDisks").checked = state.settings.showInternalDisks;
  byId("enableDestructive").checked = state.settings.enableDestructive;
  byId("allowInternalErase").checked = state.settings.allowInternalErase;
}

function loadBatchSelection() {
  try {
    const names = JSON.parse(localStorage.getItem("selectedBatchDisks") || "[]");
    state.selectedDiskNames = new Set(Array.isArray(names) ? names : []);
  } catch (_) {
    state.selectedDiskNames = new Set();
  }
}

function persistBatchSelection() {
  localStorage.setItem("selectedBatchDisks", JSON.stringify([...state.selectedDiskNames]));
}

function selectedBatchDisks() {
  const visibleNames = new Set(visibleDisks().map((disk) => disk.name));
  return state.disks.filter((disk) => visibleNames.has(disk.name) && state.selectedDiskNames.has(disk.name));
}

function selectVisibleDisks(predicate) {
  state.selectedDiskNames = new Set(
    visibleDisks()
      .filter(predicate)
      .map((disk) => disk.name)
  );
  persistBatchSelection();
  renderDiskList();
  renderActiveJobs();
  setControlsBusy();
}

function testTargetDisks() {
  const batch = selectedBatchDisks();
  if (batch.length) return batch;
  return state.selectedDisk ? [state.selectedDisk] : [];
}

function hasAppJob(device) {
  return state.activeJobs.some((job) => job.device === device && !String(job.id).startsWith("external-"));
}

function updateRunButtonLabel() {
  const targets = testTargetDisks();
  const count = targets.length;
  byId("runTestButton").textContent = count > 1 ? `Run test on ${count} drives` : "Run test";
  byId("eraseButton").textContent = count > 1 ? `Zero erase ${count} drives` : "Single-pass zero erase";
}

function updateSafetyUi() {
  byId("dangerZone").classList.toggle("hidden", !state.settings.enableDestructive);
  byId("allowInternalErase").disabled = !state.settings.enableDestructive;
}

function setJobProgress(percent, label = "Status") {
  const safePercent = Math.max(0, Math.min(100, Math.round(percent || 0)));
  byId("jobStatusLabel").textContent = label;
  byId("jobPercent").textContent = `${safePercent}%`;
  byId("jobProgressBar").style.width = `${safePercent}%`;
}

function getSelectedAppJob() {
  if (!state.selectedDisk) return null;
  return state.activeJobs.find((job) => job.device === state.selectedDisk.name && !String(job.id).startsWith("external-")) || null;
}

function getSelectedExternalJob() {
  if (!state.selectedDisk) return null;
  return state.activeJobs.find((job) => job.device === state.selectedDisk.name && String(job.id).startsWith("external-")) || null;
}

function getLatestCompletedSelectedJob() {
  if (!state.selectedDiskJobs?.length) return null;
  return state.selectedDiskJobs.find((job) => job.status === "done" && job.result?.report_id) || null;
}

function reportExportStatus(exportInfo) {
  if (!exportInfo) return "not exported yet";
  if (exportInfo.status === "saved") {
    return `saved to ${exportInfo.target?.label || "export partition"} · ${exportInfo.pdf_name || "PDF"}`;
  }
  if (exportInfo.status === "error") return `export error · ${exportInfo.message}`;
  return exportInfo.message || exportInfo.status || "export status unknown";
}

async function loadSelectedDiskJobs() {
  if (!state.selectedDisk) {
    state.selectedDiskJobs = [];
    return;
  }
  const payload = await fetchJson(`/api/tests?device=${encodeURIComponent(state.selectedDisk.name)}`);
  state.selectedDiskJobs = payload.jobs || [];
}

function syncSelectedJobState() {
  if (!state.selectedDisk) return;
  const selectedAppJob = getSelectedAppJob();
  if (selectedAppJob) {
    if (state.currentJobId !== selectedAppJob.id) {
      state.currentJobId = selectedAppJob.id;
    }
    renderSelectedJobStatus(selectedAppJob);
    return;
  }
  if (state.currentJobId) {
    state.currentJobId = null;
  }
  renderSelectedJobStatus(null);
}

function setControlsBusy() {
  const selectedAppJob = getSelectedAppJob();
  const externalJob = getSelectedExternalJob();
  const appBusy = Boolean(selectedAppJob || state.currentJobId);
  const deviceBusy = appBusy || Boolean(externalJob);
  const runnableTargets = testTargetDisks().filter((disk) => !hasAppJob(disk.name));
  const internalEraseBlocked = state.selectedDisk?.internal && !state.settings.allowInternalErase;
  byId("runTestButton").disabled = !runnableTargets.length;
  byId("safeRemoveButton").disabled = deviceBusy;
  byId("eraseButton").disabled = appBusy || !state.settings.enableDestructive || internalEraseBlocked;
  byId("secureEraseButton").disabled = appBusy || !state.settings.enableDestructive || internalEraseBlocked || !state.secureErase?.basic_supported;
  byId("enhancedSecureEraseButton").disabled = appBusy || !state.settings.enableDestructive || internalEraseBlocked || !state.secureErase?.enhanced_supported;
  byId("nvmeEraseButton").disabled = appBusy || !state.settings.enableDestructive || internalEraseBlocked || !state.nvmeErase?.format_supported;
  byId("nvmeSanitizeCryptoButton").disabled = appBusy || !state.settings.enableDestructive || internalEraseBlocked || !state.nvmeErase?.sanitize_crypto_supported;
  byId("nvmeSanitizeBlockButton").disabled = appBusy || !state.settings.enableDestructive || internalEraseBlocked || !state.nvmeErase?.sanitize_block_supported;
  byId("abortSelftestButton").disabled = false;
  updateRunButtonLabel();
}

async function fetchJson(url, options = {}) {
  const response = await fetch(url, options);
  const payload = await response.json();
  if (!response.ok) {
    throw new Error(payload.error || `HTTP ${response.status}`);
  }
  return payload;
}

async function refreshEnterpriseStatus(force = false) {
  const suffix = force ? "?refresh=1" : "";
  state.enterprise = await fetchJson(`/api/enterprise/status${suffix}`);
  renderEnterpriseStatus();
}

function renderEnterpriseStatus() {
  const status = state.enterprise;
  const card = byId("enterpriseCard");
  if (!card || !status) return;
  const title = card.querySelector("h3");
  const badge = byId("enterpriseBadge");
  const reason = byId("enterpriseReason");
  const networkMode = byId("networkMode");
  const networkAddresses = byId("networkAddresses");
  const networkConfigButton = byId("networkConfigButton");
  const topbarNetworkStatus = byId("topbarNetworkStatus");
  const topbarNetworkMode = byId("topbarNetworkMode");
  const topbarNetworkAddress = byId("topbarNetworkAddress");
  const stateLabel = {
    disabled: "Local mode",
    available: "Server available",
    connected: "Connected",
  }[status.state] || "Local mode";
  const badgeLabel = {
    disabled: "Disabled",
    available: "Licensed",
    connected: "Connected",
  }[status.state] || "Disabled";

  card.dataset.state = status.state || "disabled";
  title.textContent = stateLabel;
  badge.textContent = badgeLabel;
  badge.classList.toggle("muted-badge", status.state === "disabled");
  reason.textContent = status.reason || "Enterprise features are unavailable.";

  const network = status.network || {};
  const modeLabel = `${String(network.mode || "dhcp").toUpperCase()} default`;
  networkMode.textContent = modeLabel;
  const addresses = network.addresses || [];
  const primaryAddress = network.primary_address || addresses[0] || null;
  networkAddresses.textContent = addresses.length
    ? addresses.map((addr) => `${addr.interface}: ${addr.address}/${addr.prefixlen}`).join(" · ")
    : "No IPv4 address detected yet.";
  if (topbarNetworkStatus && topbarNetworkMode && topbarNetworkAddress) {
    topbarNetworkMode.textContent = String(network.mode || "dhcp").toUpperCase();
    topbarNetworkAddress.textContent = primaryAddress
      ? `${primaryAddress.address}/${primaryAddress.prefixlen}`
      : "No IPv4";
    topbarNetworkStatus.classList.toggle("online", Boolean(primaryAddress));
    topbarNetworkStatus.title = primaryAddress
      ? `Network: ${primaryAddress.interface}: ${primaryAddress.address}/${primaryAddress.prefixlen}`
      : "No IPv4 address detected. Open network settings.";
    topbarNetworkStatus.onclick = () => {
      window.location.href = "/settings";
    };
  }

  networkConfigButton.classList.remove("hidden");
  networkConfigButton.onclick = () => {
    window.location.href = "/settings";
  };
}

function visibleDisks() {
  return state.disks.filter((disk) => state.settings.showInternalDisks || !disk.internal);
}

function renderDiskList() {
  const container = byId("diskList");
  container.innerHTML = "";

  const disks = visibleDisks();
  if (!disks.length) {
    const total = state.disks.length;
    const internalCount = state.disks.filter((disk) => disk.internal).length;
    const message = total
      ? `${total} drive(s) detected, ${internalCount} internal. Enable "Show internal drives" to display server disks.`
      : "No drives detected by the system. Check controller mode, RAID/HBA passthrough, or open diagnostics from the boot menu.";
    container.innerHTML = `<div class="empty-inline muted">${message}</div>`;
    return;
  }

  for (const disk of disks) {
    const button = document.createElement("div");
    const running = state.activeJobs.some((job) => job.device === disk.name);
    const checked = state.selectedDiskNames.has(disk.name);
    button.className = `disk-card ${state.selectedDisk?.name === disk.name ? "active" : ""} ${checked ? "batch-selected" : ""}`;
    button.tabIndex = 0;
    button.role = "button";
    button.innerHTML = `
      <div class="disk-card-head">
        <div class="disk-card-title">${disk.vendor || ""} ${disk.model || disk.name}</div>
        ${running ? '<span class="pill pill-status">active</span>' : ""}
      </div>
      <div class="muted">${disk.path} · ${formatBytes(disk.size_bytes)}</div>
      <div class="pill-row">
        <span class="pill">${disk.transport}</span>
        <span class="pill">${disk.kind}</span>
        ${disk.internal ? '<span class="pill">internal</span>' : '<span class="pill">external</span>'}
      </div>
      <label class="disk-select">
        <input type="checkbox" ${checked ? "checked" : ""}>
        <span>Include in batch test</span>
      </label>
    `;
    button.onclick = () => selectDisk(disk.name);
    button.onkeydown = (event) => {
      if (event.key === "Enter" || event.key === " ") {
        event.preventDefault();
        selectDisk(disk.name);
      }
    };
    const checkbox = button.querySelector("input");
    checkbox.onclick = (event) => event.stopPropagation();
    checkbox.onchange = (event) => {
      event.stopPropagation();
      if (event.target.checked) {
        state.selectedDiskNames.add(disk.name);
      } else {
        state.selectedDiskNames.delete(disk.name);
      }
      persistBatchSelection();
      renderDiskList();
      renderActiveJobs();
      setControlsBusy();
    };
    container.appendChild(button);
  }
  updateRunButtonLabel();
}

function renderDashboard() {
  const groupContainer = byId("dashboardDiskGroups");
  if (groupContainer) {
    groupContainer.innerHTML = "";
    const groups = ["HDD", "SSD", "NVMe", "Other"];
    for (const group of groups) {
      const disks = visibleDisks().filter((disk) => (["HDD", "SSD", "NVMe"].includes(disk.kind) ? disk.kind : "Other") === group);
      const rows = disks.map((disk) => {
        const title = `${disk.vendor || ""} ${disk.model || disk.name}`.trim();
        const meta = `${disk.path} · ${formatBytes(disk.size_bytes)} · ${disk.internal ? "internal" : "external"}`;
        return `
            <div class="dashboard-list-row">
              <span title="${escapeHtml(title)}">${escapeHtml(title)}</span>
              <span title="${escapeHtml(meta)}">${escapeHtml(meta)}</span>
            </div>
          `;
      }).join("");
      const card = document.createElement("div");
      card.className = "dashboard-card";
      card.innerHTML = `
        <div class="dashboard-card-head">
          <strong>${group}</strong>
          <span class="badge">${disks.length}</span>
        </div>
        <div class="dashboard-list">
          ${disks.length ? rows : '<div class="muted">No drives detected.</div>'}
        </div>
      `;
      groupContainer.appendChild(card);
    }
  }

  const controllerContainer = byId("dashboardControllers");
  if (controllerContainer) {
    const transports = [...new Set(state.disks.map((disk) => disk.transport || "unknown"))].sort();
    const hosts = state.controllers?.scsi_hosts || [];
    const installedTools = state.controllers?.installed_vendor_tools || [];
    const catalog = state.controllers?.vendor_catalog || [];
    controllerContainer.innerHTML = `
      <div class="dashboard-card">
        <div class="dashboard-card-head"><strong>Block interfaces</strong><span class="badge">${transports.length}</span></div>
        <div class="dashboard-list">
          ${transports.map((transport) => `<div class="dashboard-list-row"><span>${transport}</span><span>${state.disks.filter((disk) => (disk.transport || "unknown") === transport).length} drive(s)</span></div>`).join("") || '<div class="muted">No interfaces detected.</div>'}
        </div>
      </div>
      <div class="dashboard-card">
        <div class="dashboard-card-head"><strong>SCSI hosts / HBA</strong><span class="badge">${hosts.length}</span></div>
        <div class="dashboard-list">
          ${hosts.map((host) => `
            <div class="dashboard-list-row">
              <span>${host.host} · ${host.driver || "unknown"}</span>
              <span>${[host.model, host.firmware ? `fw ${host.firmware}` : "", host.driver_version ? `drv ${host.driver_version}` : ""].filter(Boolean).join(" · ") || "system driver"}</span>
            </div>
          `).join("") || '<div class="muted">No SCSI/HBA hosts detected.</div>'}
        </div>
      </div>
      <div class="dashboard-card">
        <div class="dashboard-card-head"><strong>RAID vendor tools</strong><span class="badge">${installedTools.length}/${catalog.length}</span></div>
        <div class="dashboard-list">
          ${catalog.map((tool) => `
            <div class="dashboard-list-row">
              <span>${tool.label}</span>
              <span>${tool.installed ? "installed" : "not installed"}</span>
            </div>
          `).join("") || '<div class="muted">No vendor tool catalog loaded.</div>'}
          <div class="dashboard-list-row"><span>Controller setup</span><span><a href="/settings">Settings</a></span></div>
        </div>
      </div>
    `;
  }
}

function smartRows(smartPayload) {
  return smartPayload?.ata_smart_attributes?.table || [];
}

function fallbackSmartRows(payload) {
  const nvme = payload?.nvme_smart_health_information_log;
  if (!nvme) return [];
  return [
    { id: "-", name: "Power_On_Hours", label: "Power-On Hours", value: "-", worst: "-", thresh: "-", raw: { value: payload?.power_on_time?.hours ?? 0 }, human: `${payload?.power_on_time?.hours ?? 0} h` },
    { id: "-", name: "Temperature_Celsius", label: "Temperature", value: "-", worst: "-", thresh: "-", raw: { value: payload?.temperature?.current ?? 0 }, human: formatTemperature(payload?.temperature?.current ?? 0) },
    { id: "-", name: "Media_Errors", label: "Media Errors", value: "-", worst: "-", thresh: "-", raw: { value: nvme.media_errors ?? 0 }, human: `${nvme.media_errors ?? 0}` },
    { id: "-", name: "Unsafe_Shutdowns", label: "Unsafe Shutdowns", value: "-", worst: "-", thresh: "-", raw: { value: nvme.unsafe_shutdowns ?? 0 }, human: `${nvme.unsafe_shutdowns ?? 0}` },
    { id: "-", name: "Percentage_Used", label: "Wear Used", value: "-", worst: "-", thresh: "-", raw: { value: nvme.percentage_used ?? 0 }, human: `${nvme.percentage_used ?? 0} %` },
  ];
}

function smartLabel(row) {
  return row.label || row.name;
}

function smartHuman(row) {
  return row.human || `${row.raw?.value ?? row.raw ?? "n/a"}`;
}

function smartSeverity(row) {
  if (row.severity) return row.severity;
  const raw = Number(row.raw?.value ?? row.raw);
  if (Number.isNaN(raw)) return "neutral";
  if (["Reallocated_Sector_Ct", "Current_Pending_Sector", "Offline_Uncorrectable", "Media_Errors"].includes(row.name)) {
    return raw === 0 ? "ok" : "danger";
  }
  if (["UDMA_CRC_Error_Count", "Unsafe_Shutdowns"].includes(row.name)) {
    if (raw === 0) return "ok";
    return raw < 20 ? "warn" : "danger";
  }
  if (row.name === "Temperature_Celsius") {
    if (raw >= 50) return "danger";
    if (raw >= 45) return "warn";
    return "ok";
  }
  if (row.name === "Percentage_Used") {
    if (raw >= 80) return "danger";
    if (raw >= 50) return "warn";
    return "ok";
  }
  return "neutral";
}

function renderHealth(health, disk) {
  byId("healthScore").textContent = health.score ?? "--";
  byId("healthGrade").textContent = health.grade ?? "Unknown";
  byId("diskTitle").textContent = `${disk.vendor || ""} ${disk.model || disk.name}`;
  byId("diskMeta").textContent = `${disk.path} · ${formatBytes(disk.size_bytes)} · ${disk.serial || "no serial"}`;

  const notes = byId("healthNotes");
  notes.innerHTML = "";
  for (const note of health.notes || []) {
    const li = document.createElement("li");
    li.textContent = note;
    notes.appendChild(li);
  }
}

function renderEraseOptions(disk, erase) {
  state.secureErase = erase;
  const hint = byId("secureEraseHint");
  const basicButton = byId("secureEraseButton");
  const enhancedButton = byId("enhancedSecureEraseButton");
  const internalGuard = disk.internal && !state.settings.allowInternalErase
    ? "Explicitly allow internal drives before using erase functions."
    : null;

  if (internalGuard) {
    hint.textContent = internalGuard;
    basicButton.disabled = true;
    enhancedButton.disabled = true;
  } else if (erase?.supported) {
    const methods = [];
    if (erase.basic_supported) methods.push("basic");
    if (erase.enhanced_supported) methods.push("enhanced");
    hint.textContent = `ATA Secure Erase available: ${methods.join(", ")}.`;
    basicButton.disabled = !state.settings.enableDestructive || !erase.basic_supported;
    enhancedButton.disabled = !state.settings.enableDestructive || !erase.enhanced_supported;
  } else {
    hint.textContent = erase?.reason || "ATA Secure Erase not available.";
    basicButton.disabled = true;
    enhancedButton.disabled = true;
  }
}

function renderNvmeEraseOptions(nvmeErase) {
  state.nvmeErase = nvmeErase;
  const hint = byId("nvmeEraseHint");
  const formatButton = byId("nvmeEraseButton");
  const cryptoButton = byId("nvmeSanitizeCryptoButton");
  const blockButton = byId("nvmeSanitizeBlockButton");
  if (!hint || !formatButton || !cryptoButton || !blockButton) return;
  if (nvmeErase?.supported) {
    const methods = [];
    if (nvmeErase.format_supported) methods.push("Format");
    if (nvmeErase.sanitize_crypto_supported) methods.push("Sanitize Crypto");
    if (nvmeErase.sanitize_block_supported) methods.push("Sanitize Block");
    hint.textContent = `${nvmeErase.reason || "NVMe erase support detected"} Available: ${methods.join(", ")}.`;
    formatButton.disabled = !state.settings.enableDestructive || !nvmeErase.format_supported;
    cryptoButton.disabled = !state.settings.enableDestructive || !nvmeErase.sanitize_crypto_supported;
    blockButton.disabled = !state.settings.enableDestructive || !nvmeErase.sanitize_block_supported;
  } else {
    hint.textContent = nvmeErase?.reason || "NVMe sanitize/format is not available for this drive.";
    formatButton.disabled = true;
    cryptoButton.disabled = true;
    blockButton.disabled = true;
  }
}

function renderExternalSelftest(selftest) {
  state.selftest = selftest;
  const box = byId("externalSelftestBox");
  const button = byId("abortSelftestButton");
  if (selftest?.running) {
    if (selftest.source === "app") {
      box.classList.add("hidden");
      box.textContent = "";
      button.classList.toggle("hidden", !selftest.abort_supported);
      return;
    }
    box.classList.remove("hidden");
    const prefix = "External SMART self-test running";
    box.textContent = `${prefix} · ${selftest.status_text}`;
    button.classList.toggle("hidden", !selftest.abort_supported);
  } else {
    box.classList.add("hidden");
    box.textContent = "";
    button.classList.add("hidden");
  }
}

function renderOverview(disk, overview, health) {
  const items = [
    { label: OVERVIEW_LABELS.summary, value: health.summary || "n/a", wide: true },
    { label: OVERVIEW_LABELS.kind, value: disk.kind || "unknown" },
    { label: OVERVIEW_LABELS.interface, value: overview?.interface || disk.transport || "unknown" },
    { label: OVERVIEW_LABELS.capacity, value: formatBytes(disk.size_bytes) },
    { label: OVERVIEW_LABELS.serial, value: overview?.serial || disk.serial || "n/a", wide: true, wrap: true },
    { label: OVERVIEW_LABELS.powerOnHours, value: overview?.power_on_hours || "n/a" },
    { label: OVERVIEW_LABELS.temperature, value: overview?.temperature_c || "n/a" },
    { label: OVERVIEW_LABELS.reallocated, value: overview?.reallocated || "0" },
    { label: OVERVIEW_LABELS.pending, value: overview?.pending || "0" },
    { label: OVERVIEW_LABELS.offlineUncorrectable, value: overview?.offline_uncorrectable || "0" },
    { label: OVERVIEW_LABELS.crcErrors, value: overview?.crc_errors || "0" },
    { label: OVERVIEW_LABELS.mediaErrors, value: overview?.media_errors || "n/a" },
  ];

  byId("overviewGrid").innerHTML = items
    .map(({ label, value, wide, wrap }) => `
      <div class="info-cell${wide ? " wide" : ""}">
        <span>${label}</span>
        <strong class="${wrap ? "wrap-value" : ""}">${value}</strong>
      </div>
    `)
    .join("");
}

function renderSmart(smart) {
  const badge = byId("smartBadge");
  const table = byId("smartTable");

  if (!smart.available) {
    badge.textContent = "Unavailable";
    table.innerHTML = `<p class="muted">${smart.error || "SMART unavailable"}</p>`;
    if (state.smartChart) state.smartChart.destroy();
    return;
  }

  if (smart.error && !smart.payload) {
    badge.textContent = "Error";
    table.innerHTML = `<p class="muted">${smart.error}</p>`;
    if (state.smartChart) state.smartChart.destroy();
    return;
  }

  const payload = smart.payload || {};
  const passed = payload.smart_status?.passed;
  badge.textContent = passed === false ? "SMART Warnung" : "SMART OK";
  badge.className = `badge ${passed === false ? "danger" : "ok"}`;

  const rows = smartRows(payload).length ? smartRows(payload) : fallbackSmartRows(payload);
  const warningHtml = smart.warning ? `<p class="muted">${smart.warning}</p>` : "";
  table.innerHTML = `${warningHtml}
    <div class="smart-header"><span>ID</span><span>Attribut</span><span>Current</span><span>Worst</span><span>Thresh</span><span>Wert</span></div>
    ${rows
      .map(
        (row) => `
          <div class="smart-row full">
            <span class="row-indicator ${smartSeverity(row)}"></span>
            <span>${row.id ?? "-"}</span>
            <span>${smartLabel(row)}</span>
            <span>${row.value ?? "-"}</span>
            <span>${row.worst ?? "-"}</span>
            <span>${row.thresh ?? "-"}</span>
            <span>${smartHuman(row)} <span class="smart-raw">(${row.raw?.value ?? row.raw ?? "n/a"})</span></span>
          </div>
        `
      )
      .join("")}`;

  const chartRows = rows.filter((row) =>
    ["Reallocated_Sector_Ct", "Current_Pending_Sector", "Offline_Uncorrectable", "Power_On_Hours", "Temperature_Celsius", "Media_Errors", "Unsafe_Shutdowns", "Percentage_Used"].includes(row.name)
    && Number.isFinite(Number(row.raw?.value ?? row.raw))
  );

  const palette = ["#34d399", "#fbbf24", "#fb7185", "#86efac", "#f59e0b", "#fda4af", "#a3e635", "#fcd34d"];
  const ctx = document.getElementById("smartChart");
  if (state.smartChart) state.smartChart.destroy();
  state.smartChart = new Chart(ctx, {
    type: "bar",
    data: {
      labels: chartRows.map((row) => row.name),
      datasets: [{
        label: "Raw SMART values",
        data: chartRows.map((row) => Number(row.raw?.value ?? row.raw)),
        backgroundColor: chartRows.map((_, index) => palette[index % palette.length]),
      }],
    },
    options: {
      responsive: true,
      plugins: {
        legend: { display: false },
      },
      scales: {
        y: { beginAtZero: true },
      },
    },
  });
}

async function loadReports() {
  const payload = await fetchJson("/api/reports");
  const container = byId("reportsList");
  if (!container) return;
  container.innerHTML = "";
  if (!payload.reports.length) {
    container.innerHTML = `<div class="job-box muted">No reports saved yet.</div>`;
    return;
  }
  for (const report of payload.reports) {
    const item = document.createElement("div");
    item.className = "report-item";
    item.innerHTML = `
      <a class="report-link" href="/report/${report.report_id}">${report.device.path} · Score ${report.health.score} · ${new Date(report.generated_at).toLocaleString()}</a>
      <div class="report-export-status ${report.export?.status === "saved" ? "ok" : report.export?.status === "error" ? "danger" : ""}">
        ${reportExportStatus(report.export)}
      </div>
      <div class="report-actions-inline">
        <a class="mini-action" href="/report/${report.report_id}">Open</a>
        <a class="mini-action" href="/certificate/${report.report_id}">Certificate</a>
        <a class="mini-action" href="/report/${report.report_id}/pdf">PDF</a>
        <button class="mini-action danger" type="button" data-action="delete">Delete</button>
      </div>
    `;

    item.querySelector('[data-action="delete"]').onclick = async () => {
      const confirmed = window.confirm(`Delete report ${report.report_id}?`);
      if (!confirmed) return;
      try {
        await fetchJson(`/api/reports/${report.report_id}`, { method: "DELETE" });
        await loadReports();
      } catch (error) {
        if (state.selectedDisk) {
          byId("jobStatus").textContent = `report delete error · ${error.message}`;
        }
      }
    };
    container.appendChild(item);
  }
}

async function loadControllers() {
  state.controllers = await fetchJson("/api/controllers");
  renderDashboard();
}

function renderModeSelector(modes) {
  const container = byId("testSelector");
  container.innerHTML = "";
  const availableModes = modes || [];
  if (!availableModes.some((mode) => mode.id === state.selectedMode)) {
    state.selectedMode = availableModes[0]?.id || "quick";
  }

  availableModes.forEach((mode) => {
    const button = document.createElement("button");
    button.className = `mode-button ${state.selectedMode === mode.id ? "active" : ""} ${mode.id === "full" ? "warn" : ""}`;
    button.dataset.mode = mode.id;
    button.textContent = mode.label || MODE_LABELS[mode.id] || mode.id;
    button.onclick = () => updateModeSelection(mode.id);
    container.appendChild(button);
  });
  updateModeSelection(state.selectedMode);
}

function updateModeSelection(mode) {
  state.selectedMode = mode;
  document.querySelectorAll("[data-mode]").forEach((button) => {
    button.classList.toggle("active", button.dataset.mode === mode);
  });
  byId("testModeHint").textContent = MODE_HINTS[mode] || "";
}

function renderSelectedJobStatus(job) {
  if (!job) {
    const lastDoneJob = getLatestCompletedSelectedJob();
    if (lastDoneJob?.result?.report_id) {
      setJobProgress(100, MODE_LABELS[lastDoneJob.mode] || "Latest Test");
      byId("jobStatus").innerHTML = `done · ${reportExportStatus(lastDoneJob.result.export)} · <a href="/report/${lastDoneJob.result.report_id}">Open latest report</a>`;
    } else {
      setJobProgress(0, "Active Test");
      byId("jobStatus").textContent = "No test started yet.";
    }
    if (state.selftest?.running) {
      const prefix = state.selftest.source === "app" ? "SMART self-test" : "External SMART self-test";
      byId("jobStatus").textContent = `No app test active · ${prefix}: ${state.selftest.status_text}`;
      setJobProgress(100 - (state.selftest.remaining_percent ?? 100), prefix);
    }
    return;
  }

  const percent = Math.round((job.progress || 0) * 100);
  setJobProgress(percent, MODE_LABELS[job.mode] || "Test");
  byId("jobStatus").textContent = `${job.status} · ${job.current_step} · ${percent}%`;
}

function renderActiveJobs() {
  const container = byId("activeJobsList");
  container.innerHTML = "";
  if (!state.activeJobs.length) {
    container.innerHTML = `<div class="job-box muted">No other running jobs.</div>`;
    return;
  }

  state.activeJobs
    .filter((job) => !state.selectedDisk || job.device !== state.selectedDisk.name || String(job.id).startsWith("external-"))
    .forEach((job) => {
    const percent = Math.round((job.progress || 0) * 100);
    const element = document.createElement("div");
    element.className = "active-job-card";
    element.innerHTML = `
      <div class="active-job-head">
        <strong>${job.device} · ${MODE_LABELS[job.mode] || job.mode}</strong>
        <span class="job-percent">${percent}%</span>
      </div>
      <div class="muted">${job.current_step || job.status}</div>
      <div class="progress-track compact">
        <div class="progress-bar" style="width:${percent}%"></div>
      </div>
    `;
    if (state.selectedDisk?.name !== job.device) {
      element.onclick = () => selectDisk(job.device);
    }
    container.appendChild(element);
  });

  if (!container.children.length) {
    container.innerHTML = `<div class="job-box muted">No other running jobs.</div>`;
  }
}

async function refreshActiveJobs() {
  try {
    const payload = await fetchJson("/api/tests?active=1");
    state.activeJobs = payload.jobs || [];
    renderActiveJobs();
    renderDiskList();
    if (state.selectedDisk) {
      const selectedAppJob = getSelectedAppJob();
      if (!selectedAppJob) {
        await loadSelectedDiskJobs();
      }
      syncSelectedJobState();
      if (selectedAppJob) {
        if (!state.pollTimer) {
          pollSelectedJob();
        }
      } else if (state.pollTimer) {
        clearTimeout(state.pollTimer);
        state.pollTimer = null;
      }
      setControlsBusy();
    }
  } finally {
    if (state.activeJobsTimer) clearTimeout(state.activeJobsTimer);
    state.activeJobsTimer = setTimeout(refreshActiveJobs, 3000);
  }
}

async function selectDisk(name) {
  const payload = await fetchJson(`/api/disks/${name}`);
  state.selectedDisk = payload.disk;
  localStorage.setItem("selectedDiskName", payload.disk.name);
  renderDiskList();
  byId("emptyState").classList.add("hidden");
  byId("detailView").classList.remove("hidden");
  renderHealth(payload.health, payload.disk);
  renderModeSelector(payload.modes || payload.disk.modes || []);
  renderEraseOptions(payload.disk, payload.erase);
  renderNvmeEraseOptions(payload.nvme_erase);
  renderExternalSelftest(payload.selftest);
  renderOverview(payload.disk, payload.overview, payload.health);
  renderSmart(payload.smart);
  await loadSelectedDiskJobs();
  syncSelectedJobState();
  setControlsBusy();
  if (state.currentJobId && !state.pollTimer) {
    pollSelectedJob();
  }
}

async function refreshDisks() {
  const payload = await fetchJson("/api/disks");
  state.disks = payload.disks;
  const knownNames = new Set(state.disks.map((disk) => disk.name));
  state.selectedDiskNames = new Set([...state.selectedDiskNames].filter((name) => knownNames.has(name)));
  persistBatchSelection();
  byId("envHint").textContent = payload.smartctl_available
    ? "SMART available"
    : "SMART missing: install smartmontools";
  renderDiskList();
  await loadReports();
  await loadControllers();
  renderDashboard();

  if (pageName() === "dashboard") {
    state.selectedDisk = null;
    byId("detailView").classList.add("hidden");
    byId("emptyState").classList.add("hidden");
    return;
  }

  const visible = visibleDisks();
  const stored = localStorage.getItem("selectedDiskName");
  const stillVisible = visible.some((disk) => disk.name === stored);
  if (stored && stillVisible) {
    await selectDisk(stored);
  } else if (state.selectedDisk && visible.some((disk) => disk.name === state.selectedDisk.name)) {
    await selectDisk(state.selectedDisk.name);
  } else if (visible.length) {
    await selectDisk(visible[0].name);
  } else {
    state.selectedDisk = null;
    byId("detailView").classList.add("hidden");
    byId("emptyState").classList.remove("hidden");
  }
}

async function startTest() {
  const targets = testTargetDisks().filter((disk) => !hasAppJob(disk.name));
  if (!targets.length) return;

  const started = [];
  const failed = [];
  for (const disk of targets) {
    try {
      const payload = await fetchJson("/api/tests", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ device: disk.name, mode: state.selectedMode, compliance_profile: state.selectedComplianceProfile }),
      });
      started.push({ disk, jobId: payload.job_id });
    } catch (error) {
      failed.push(`${disk.name}: ${error.message}`);
    }
  }

  const selectedStarted = started.find((item) => item.disk.name === state.selectedDisk?.name) || started[0];
  if (selectedStarted) {
    state.currentJobId = selectedStarted.jobId;
  }
  await refreshActiveJobs();
  if (state.currentJobId) pollSelectedJob();
  if (started.length || failed.length) {
    const parts = [];
    if (started.length) parts.push(`started ${started.length} test${started.length === 1 ? "" : "s"}`);
    if (failed.length) parts.push(`failed: ${failed.join("; ")}`);
    byId("jobStatus").textContent = parts.join(" · ");
  }
}

async function pollSelectedJob() {
  if (!state.currentJobId) return;
  if (state.pollTimer) {
    clearTimeout(state.pollTimer);
    state.pollTimer = null;
  }

  const payload = await fetchJson(`/api/tests/${state.currentJobId}`);
  const percent = Math.round((payload.progress || 0) * 100);
  setJobProgress(percent, MODE_LABELS[payload.mode] || "Test");

  if (payload.status === "done") {
    byId("jobStatus").innerHTML = `done · ${reportExportStatus(payload.result.export)} · <a href="/report/${payload.result.report_id}">Open report</a>`;
    setJobProgress(100, MODE_LABELS[payload.mode] || "Test");
    state.currentJobId = null;
    state.pollTimer = null;
    await refreshActiveJobs();
    await loadReports();
    if (state.selectedDisk) await selectDisk(state.selectedDisk.name);
    return;
  }

  if (payload.status === "error") {
    byId("jobStatus").textContent = `error · ${payload.error}`;
    state.currentJobId = null;
    state.pollTimer = null;
    await refreshActiveJobs();
    setControlsBusy();
    return;
  }

  byId("jobStatus").textContent = `${payload.status} · ${payload.current_step} · ${percent}%`;
  setControlsBusy();
  state.pollTimer = setTimeout(pollSelectedJob, 1500);
}

async function safeRemoveSelectedDisk() {
  if (!state.selectedDisk) return;
  byId("safeRemoveButton").disabled = true;
  try {
    const payload = await fetchJson(`/api/disks/${state.selectedDisk.name}/safe-remove`, {
      method: "POST",
    });
    byId("jobStatus").textContent = payload.actions.join(" · ");
    state.selectedDisk = null;
    byId("detailView").classList.add("hidden");
    byId("emptyState").classList.remove("hidden");
    await refreshDisks();
    await refreshActiveJobs();
  } catch (error) {
    byId("jobStatus").textContent = `safe remove error · ${error.message}`;
    setControlsBusy();
  }
}

async function abortExternalSelftest() {
  if (!state.selectedDisk) return;
  byId("abortSelftestButton").disabled = true;
  try {
    const payload = await fetchJson(`/api/disks/${state.selectedDisk.name}/abort-selftest`, {
      method: "POST",
    });
    renderExternalSelftest(payload.status);
    byId("jobStatus").textContent = "SMART self-test aborted.";
    setJobProgress(0, "SMART");
    await refreshActiveJobs();
    setControlsBusy();
  } catch (error) {
    byId("jobStatus").textContent = `abort error · ${error.message}`;
    byId("abortSelftestButton").disabled = false;
  }
}

async function eraseSelectedDisk() {
  const targets = testTargetDisks().filter((disk) => !hasAppJob(disk.name));
  if (!targets.length) return;
  const started = [];
  const failed = [];
  for (const disk of targets) {
    try {
      const payload = await fetchJson(`/api/disks/${disk.name}/erase`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ allow_internal: state.settings.allowInternalErase, compliance_profile: state.selectedComplianceProfile }),
      });
      started.push({ disk, jobId: payload.job_id });
    } catch (error) {
      failed.push(`${disk.name}: ${error.message}`);
    }
  }
  const selectedStarted = started.find((item) => item.disk.name === state.selectedDisk?.name) || started[0];
  if (selectedStarted) state.currentJobId = selectedStarted.jobId;
  await refreshActiveJobs();
  if (state.currentJobId) pollSelectedJob();
  byId("jobStatus").textContent = [`started ${started.length} erase job${started.length === 1 ? "" : "s"}`, failed.length ? `failed: ${failed.join("; ")}` : ""].filter(Boolean).join(" · ");
}

async function secureEraseSelectedDisk(method = "basic") {
  const targets = testTargetDisks().filter((disk) => !hasAppJob(disk.name));
  if (!targets.length) return;
  const started = [];
  const failed = [];
  for (const disk of targets) {
    try {
      const payload = await fetchJson(`/api/disks/${disk.name}/secure-erase`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ allow_internal: state.settings.allowInternalErase, method, compliance_profile: state.selectedComplianceProfile }),
      });
      started.push({ disk, jobId: payload.job_id });
    } catch (error) {
      failed.push(`${disk.name}: ${error.message}`);
    }
  }
  const selectedStarted = started.find((item) => item.disk.name === state.selectedDisk?.name) || started[0];
  if (selectedStarted) state.currentJobId = selectedStarted.jobId;
  await refreshActiveJobs();
  if (state.currentJobId) pollSelectedJob();
  byId("jobStatus").textContent = [`started ${started.length} secure erase job${started.length === 1 ? "" : "s"}`, failed.length ? `failed: ${failed.join("; ")}` : ""].filter(Boolean).join(" · ");
}

async function nvmeEraseSelectedDisk(method = "format") {
  const targets = testTargetDisks().filter((disk) => !hasAppJob(disk.name));
  if (!targets.length) return;
  const started = [];
  const failed = [];
  for (const disk of targets) {
    try {
      const payload = await fetchJson(`/api/disks/${disk.name}/nvme-erase`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ allow_internal: state.settings.allowInternalErase, method, compliance_profile: state.selectedComplianceProfile }),
      });
      started.push({ disk, jobId: payload.job_id });
    } catch (error) {
      failed.push(`${disk.name}: ${error.message}`);
    }
  }
  const selectedStarted = started.find((item) => item.disk.name === state.selectedDisk?.name) || started[0];
  if (selectedStarted) state.currentJobId = selectedStarted.jobId;
  await refreshActiveJobs();
  if (state.currentJobId) pollSelectedJob();
  byId("jobStatus").textContent = [`started ${started.length} NVMe erase job${started.length === 1 ? "" : "s"}`, failed.length ? `failed: ${failed.join("; ")}` : ""].filter(Boolean).join(" · ");
}

function bindSettings() {
  byId("complianceProfile").onchange = (event) => {
    state.selectedComplianceProfile = event.target.value;
    localStorage.setItem("complianceProfile", state.selectedComplianceProfile);
    updateComplianceHint();
  };
  byId("showInternalDisks").onchange = async (event) => {
    state.settings.showInternalDisks = event.target.checked;
    persistSettings();
    renderDiskList();
    await refreshDisks();
  };
  byId("enableDestructive").onchange = async (event) => {
    state.settings.enableDestructive = event.target.checked;
    if (!state.settings.enableDestructive) {
      state.settings.allowInternalErase = false;
    }
    persistSettings();
    syncSettingsInputs();
    updateSafetyUi();
    if (state.selectedDisk) {
      const payload = await fetchJson(`/api/disks/${state.selectedDisk.name}`);
      renderEraseOptions(payload.disk, payload.erase);
      renderNvmeEraseOptions(payload.nvme_erase);
    }
    setControlsBusy();
  };
  byId("allowInternalErase").onchange = async (event) => {
    state.settings.allowInternalErase = event.target.checked;
    persistSettings();
    updateSafetyUi();
    if (state.selectedDisk) {
      const payload = await fetchJson(`/api/disks/${state.selectedDisk.name}`);
      renderEraseOptions(payload.disk, payload.erase);
      renderNvmeEraseOptions(payload.nvme_erase);
    }
    setControlsBusy();
  };
}

document.addEventListener("DOMContentLoaded", async () => {
  initTheme();
  loadSettings();
  loadComplianceSelection();
  loadBatchSelection();
  syncSettingsInputs();
  updateSafetyUi();
  byId("themeSystem").onclick = () => applyTheme("system");
  byId("themeDark").onclick = () => applyTheme("dark");
  byId("themeLight").onclick = () => applyTheme("light");
  byId("refreshButton").onclick = async () => {
    await refreshEnterpriseStatus(true);
    await loadNetworkConfig();
    await loadSystemTools();
    await loadVendorTools();
    await refreshDisks();
    await refreshActiveJobs();
  };
  byId("selectHddButton").onclick = () => selectVisibleDisks((disk) => disk.kind === "HDD");
  byId("selectSsdButton").onclick = () => selectVisibleDisks((disk) => disk.kind === "SSD");
  byId("selectNvmeButton").onclick = () => selectVisibleDisks((disk) => disk.kind === "NVMe");
  byId("selectAllButton").onclick = () => selectVisibleDisks(() => true);
  byId("clearSelectionButton").onclick = () => selectVisibleDisks(() => false);
  byId("networkConfigForm").onsubmit = (event) => {
    event.preventDefault();
    saveNetworkConfig({
      ip: byId("networkIp").value,
      gw: byId("networkGw").value,
      dns: byId("networkDns").value,
    }).catch((error) => {
      byId("networkConfigStatus").textContent = `save error · ${error.message}`;
    });
  };
  byId("clearNetworkConfigButton").onclick = () => saveNetworkConfig({ ip: "", gw: "", dns: "" }).catch((error) => {
    byId("networkConfigStatus").textContent = `clear error · ${error.message}`;
  });
  byId("runTestButton").onclick = () => startTest().catch((error) => {
    byId("jobStatus").textContent = `start error · ${error.message}`;
    setControlsBusy();
  });
  byId("safeRemoveButton").onclick = () => safeRemoveSelectedDisk();
  byId("abortSelftestButton").onclick = () => abortExternalSelftest();
  byId("eraseButton").onclick = () => eraseSelectedDisk().catch((error) => {
    byId("jobStatus").textContent = `erase error · ${error.message}`;
    setControlsBusy();
  });
  byId("secureEraseButton").onclick = () => secureEraseSelectedDisk().catch((error) => {
    byId("jobStatus").textContent = `secure erase error · ${error.message}`;
    setControlsBusy();
  });
  byId("enhancedSecureEraseButton").onclick = () => secureEraseSelectedDisk("enhanced").catch((error) => {
    byId("jobStatus").textContent = `enhanced secure erase error · ${error.message}`;
    setControlsBusy();
  });
  byId("nvmeEraseButton").onclick = () => nvmeEraseSelectedDisk().catch((error) => {
    byId("jobStatus").textContent = `NVMe erase error · ${error.message}`;
    setControlsBusy();
  });
  byId("nvmeSanitizeCryptoButton").onclick = () => nvmeEraseSelectedDisk("sanitize_crypto").catch((error) => {
    byId("jobStatus").textContent = `NVMe sanitize crypto error · ${error.message}`;
    setControlsBusy();
  });
  byId("nvmeSanitizeBlockButton").onclick = () => nvmeEraseSelectedDisk("sanitize_block").catch((error) => {
    byId("jobStatus").textContent = `NVMe sanitize block error · ${error.message}`;
    setControlsBusy();
  });
  bindSettings();
  await loadComplianceProfiles();
  await loadNetworkConfig();
  await loadSystemTools();
  await loadVendorTools();
  await refreshEnterpriseStatus();
  await refreshDisks();
  await refreshActiveJobs();
});
