const state = {
  disks: [],
  selectedDisk: null,
  selectedDiskNames: new Set(),
  currentJobId: null,
  pollTimer: null,
  activeJobsTimer: null,
  activeJobs: [],
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

function formatTemperature(value) {
  const numeric = Number(value);
  if (!Number.isFinite(numeric)) return "n/a";
  const fahrenheit = Math.round((numeric * 9) / 5 + 32);
  return `${numeric} °C / ${fahrenheit} °F`;
}

function byId(id) {
  return document.getElementById(id);
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
  byId("runTestButton").disabled = !runnableTargets.length;
  byId("safeRemoveButton").disabled = deviceBusy;
  byId("eraseButton").disabled = appBusy || !state.settings.enableDestructive;
  byId("secureEraseButton").disabled = appBusy || !state.settings.enableDestructive || !state.secureErase?.basic_supported;
  byId("enhancedSecureEraseButton").disabled = appBusy || !state.settings.enableDestructive || !state.secureErase?.enhanced_supported;
  byId("nvmeEraseButton").disabled = true;
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
  networkMode.textContent = `${String(network.mode || "dhcp").toUpperCase()} default`;
  const addresses = network.addresses || [];
  networkAddresses.textContent = addresses.length
    ? addresses.map((addr) => `${addr.interface}: ${addr.address}/${addr.prefixlen}`).join(" · ")
    : "No IPv4 address detected yet.";

  const canConfigure = Boolean(status.features?.network_configuration);
  networkConfigButton.classList.toggle("hidden", !canConfigure);
  networkConfigButton.onclick = () => {
    alert("Network configuration is managed by a licensed DriveProof Enterprise Server. DHCP remains the default for standalone live boot.");
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
  byId("eraseConfirmInput").value = "";
}

function renderNvmeEraseOptions(nvmeErase) {
  state.nvmeErase = nvmeErase;
  const hint = byId("nvmeEraseHint");
  const button = byId("nvmeEraseButton");
  if (!hint || !button) return;
  if (nvmeErase?.supported) {
    hint.textContent = "NVMe sanitize/format support detected.";
    button.disabled = !state.settings.enableDestructive;
  } else {
    hint.textContent = nvmeErase?.reason || "NVMe sanitize/format is not available for this drive.";
    button.disabled = true;
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
  if (!state.selectedDisk) return;
  const confirmation = byId("eraseConfirmInput").value.trim();
  const payload = await fetchJson(`/api/disks/${state.selectedDisk.name}/erase`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ confirmation, allow_internal: state.settings.allowInternalErase, compliance_profile: state.selectedComplianceProfile }),
  });
  state.currentJobId = payload.job_id;
  await refreshActiveJobs();
  pollSelectedJob();
}

async function secureEraseSelectedDisk(method = "basic") {
  if (!state.selectedDisk) return;
  const confirmation = byId("eraseConfirmInput").value.trim();
  const payload = await fetchJson(`/api/disks/${state.selectedDisk.name}/secure-erase`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ confirmation, allow_internal: state.settings.allowInternalErase, method, compliance_profile: state.selectedComplianceProfile }),
  });
  state.currentJobId = payload.job_id;
  await refreshActiveJobs();
  pollSelectedJob();
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
    await refreshDisks();
    await refreshActiveJobs();
  };
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
  bindSettings();
  await loadComplianceProfiles();
  await refreshEnterpriseStatus();
  await refreshDisks();
  await refreshActiveJobs();
});
