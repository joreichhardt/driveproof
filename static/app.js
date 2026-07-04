const state = {
  disks: [],
  selectedDisk: null,
  currentJobId: null,
  pollTimer: null,
  activeJobsTimer: null,
  activeJobs: [],
  selectedDiskJobs: [],
  smartChart: null,
  selectedMode: "quick",
  secureErase: null,
  selftest: null,
  themeMode: "system",
  settings: {
    showInternalDisks: false,
    enableDestructive: false,
    allowInternalErase: false,
  },
};

const MODE_HINTS = {
  quick: "Kurzer Stichproben-Lesetest fuer Vorsortierung.",
  deep_sample: "Verteilter Lesetest ueber die Platte. Fuer HDDs sinnvoller als fuer SSD/NVMe.",
  smart_short: "Kurzer Laufwerks-Selbsttest. Gut fuer SSD/NVMe und schnelle Vorpruefung.",
  smart_extended: "Echter SMART Extended Self-Test des Laufwerks. Fuer Verkauf glaubwuerdig.",
  full: "Kompletter Lesetest. Dauert lange und ist fuer den Verkauf die staerkste Lesetest-Aussage.",
  erase_zero: "1x Nullschreiben. Destruktiv.",
  secure_erase_ata: "ATA Secure Erase. Destruktiv.",
  smart_extended_external: "Von ausserhalb der App gestarteter SMART Self-Test.",
};

const MODE_LABELS = {
  quick: "Schnelltest",
  deep_sample: "Tiefer Lesetest",
  smart_short: "SMART Kurztest",
  smart_extended: "SMART Extended",
  full: "Vollscan",
  erase_zero: "Nullschreiben",
  secure_erase_ata: "ATA Secure Erase",
  smart_extended_external: "Externer SMART Test",
};

const OVERVIEW_LABELS = {
  summary: "Status",
  kind: "Typ",
  interface: "Interface",
  capacity: "Kapazitaet",
  serial: "Seriennummer",
  powerOnHours: "Betriebsstunden",
  temperature: "Temperatur",
  reallocated: "Neu zugewiesen",
  pending: "Ausstehend",
  offlineUncorrectable: "Nicht korrigierbar",
  crcErrors: "CRC-Fehler",
  mediaErrors: "Medienfehler",
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
    if (!raw) return;
    state.settings = { ...state.settings, ...JSON.parse(raw) };
  } catch (_) {
    // ignore broken local storage
  }
}

function persistSettings() {
  localStorage.setItem("safetySettings", JSON.stringify(state.settings));
}

function syncSettingsInputs() {
  byId("showInternalDisks").checked = state.settings.showInternalDisks;
  byId("enableDestructive").checked = state.settings.enableDestructive;
  byId("allowInternalErase").checked = state.settings.allowInternalErase;
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
  byId("runTestButton").disabled = appBusy;
  byId("safeRemoveButton").disabled = deviceBusy;
  byId("eraseButton").disabled = appBusy || !state.settings.enableDestructive;
  byId("secureEraseButton").disabled = appBusy || !state.settings.enableDestructive || !state.secureErase?.supported;
  byId("abortSelftestButton").disabled = false;
}

async function fetchJson(url, options = {}) {
  const response = await fetch(url, options);
  const payload = await response.json();
  if (!response.ok) {
    throw new Error(payload.error || `HTTP ${response.status}`);
  }
  return payload;
}

function visibleDisks() {
  return state.disks.filter((disk) => state.settings.showInternalDisks || !disk.internal);
}

function renderDiskList() {
  const container = byId("diskList");
  container.innerHTML = "";

  const disks = visibleDisks();
  if (!disks.length) {
    container.innerHTML = `<div class="empty-inline muted">Keine passenden Laufwerke sichtbar. Fuer Serverplatten "Interne Laufwerke anzeigen" aktivieren.</div>`;
    return;
  }

  for (const disk of disks) {
    const button = document.createElement("button");
    const running = state.activeJobs.some((job) => job.device === disk.name);
    button.className = `disk-card ${state.selectedDisk?.name === disk.name ? "active" : ""}`;
    button.innerHTML = `
      <div class="disk-card-head">
        <div class="disk-card-title">${disk.vendor || ""} ${disk.model || disk.name}</div>
        ${running ? '<span class="pill pill-status">aktiv</span>' : ""}
      </div>
      <div class="muted">${disk.path} · ${formatBytes(disk.size_bytes)}</div>
      <div class="pill-row">
        <span class="pill">${disk.transport}</span>
        <span class="pill">${disk.kind}</span>
        ${disk.internal ? '<span class="pill">intern</span>' : '<span class="pill">extern</span>'}
      </div>
    `;
    button.onclick = () => selectDisk(disk.name);
    container.appendChild(button);
  }
}

function smartRows(smartPayload) {
  return smartPayload?.ata_smart_attributes?.table || [];
}

function fallbackSmartRows(payload) {
  const nvme = payload?.nvme_smart_health_information_log;
  if (!nvme) return [];
  return [
    { id: "-", name: "Power_On_Hours", label: "Betriebsstunden", value: "-", worst: "-", thresh: "-", raw: { value: payload?.power_on_time?.hours ?? 0 }, human: `${payload?.power_on_time?.hours ?? 0} h` },
    { id: "-", name: "Temperature_Celsius", label: "Temperatur", value: "-", worst: "-", thresh: "-", raw: { value: payload?.temperature?.current ?? 0 }, human: `${payload?.temperature?.current ?? 0} C` },
    { id: "-", name: "Media_Errors", label: "Medienfehler", value: "-", worst: "-", thresh: "-", raw: { value: nvme.media_errors ?? 0 }, human: `${nvme.media_errors ?? 0}` },
    { id: "-", name: "Unsafe_Shutdowns", label: "Unsichere Abschaltungen", value: "-", worst: "-", thresh: "-", raw: { value: nvme.unsafe_shutdowns ?? 0 }, human: `${nvme.unsafe_shutdowns ?? 0}` },
    { id: "-", name: "Percentage_Used", label: "Abnutzung", value: "-", worst: "-", thresh: "-", raw: { value: nvme.percentage_used ?? 0 }, human: `${nvme.percentage_used ?? 0} %` },
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
  byId("healthGrade").textContent = health.grade ?? "Unbekannt";
  byId("diskTitle").textContent = `${disk.vendor || ""} ${disk.model || disk.name}`;
  byId("diskMeta").textContent = `${disk.path} · ${formatBytes(disk.size_bytes)} · ${disk.serial || "ohne Seriennummer"}`;

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
  const button = byId("secureEraseButton");
  const internalGuard = disk.internal && !state.settings.allowInternalErase
    ? "Interne Laufwerke erst explizit fuer Loeschvorgaenge freigeben."
    : null;

  if (internalGuard) {
    hint.textContent = internalGuard;
    button.disabled = true;
  } else if (erase?.supported) {
    hint.textContent = `ATA Secure Erase verfuegbar (${erase.method === "enhanced" ? "enhanced" : "basic"}).`;
    button.disabled = !state.settings.enableDestructive;
  } else {
    hint.textContent = erase?.reason || "ATA Secure Erase nicht verfuegbar.";
    button.disabled = true;
  }
  byId("eraseConfirmInput").value = "";
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
    const prefix = "Externer SMART Self-Test laeuft";
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
    [OVERVIEW_LABELS.summary, health.summary || "n/a"],
    [OVERVIEW_LABELS.kind, disk.kind || "unknown"],
    [OVERVIEW_LABELS.interface, overview?.interface || disk.transport || "unknown"],
    [OVERVIEW_LABELS.capacity, formatBytes(disk.size_bytes)],
    [OVERVIEW_LABELS.serial, overview?.serial || disk.serial || "n/a"],
    [OVERVIEW_LABELS.powerOnHours, overview?.power_on_hours || "n/a"],
    [OVERVIEW_LABELS.temperature, overview?.temperature_c || "n/a"],
    [OVERVIEW_LABELS.reallocated, overview?.reallocated || "0"],
    [OVERVIEW_LABELS.pending, overview?.pending || "0"],
    [OVERVIEW_LABELS.offlineUncorrectable, overview?.offline_uncorrectable || "0"],
    [OVERVIEW_LABELS.crcErrors, overview?.crc_errors || "0"],
    [OVERVIEW_LABELS.mediaErrors, smart.payload?.nvme_smart_health_information_log?.media_errors ?? "n/a"],
  ];

  byId("overviewGrid").innerHTML = items
    .map(([label, value]) => `<div class="info-cell"><span>${label}</span><strong>${value}</strong></div>`)
    .join("");
}

function renderSmart(smart) {
  const badge = byId("smartBadge");
  const table = byId("smartTable");

  if (!smart.available) {
    badge.textContent = "Nicht verfuegbar";
    table.innerHTML = `<p class="muted">${smart.error || "SMART nicht verfuegbar"}</p>`;
    if (state.smartChart) state.smartChart.destroy();
    return;
  }

  if (smart.error && !smart.payload) {
    badge.textContent = "Fehler";
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
        label: "Raw SMART Werte",
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
  for (const report of payload.reports) {
    const anchor = document.createElement("a");
    anchor.className = "report-link";
    anchor.href = `/report/${report.report_id}`;
    anchor.target = "_blank";
    anchor.textContent = `${report.device.path} · Score ${report.health.score} · ${new Date(report.generated_at).toLocaleString()}`;
    container.appendChild(anchor);
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
      setJobProgress(100, MODE_LABELS[lastDoneJob.mode] || "Letzter Test");
      byId("jobStatus").innerHTML = `abgeschlossen · <a href="/report/${lastDoneJob.result.report_id}" target="_blank">Letzten Bericht oeffnen</a>`;
    } else {
      setJobProgress(0, "Aktiver Test");
      byId("jobStatus").textContent = "Noch kein Test gestartet.";
    }
    if (state.selftest?.running) {
      const prefix = state.selftest.source === "app" ? "SMART Self-Test" : "Externer SMART Self-Test";
      byId("jobStatus").textContent = `Kein App-Test aktiv · ${prefix}: ${state.selftest.status_text}`;
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
    container.innerHTML = `<div class="job-box muted">Keine weiteren laufenden Jobs.</div>`;
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
    container.innerHTML = `<div class="job-box muted">Keine weiteren laufenden Jobs.</div>`;
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
  byId("envHint").textContent = payload.smartctl_available
    ? "SMART verfuegbar"
    : "SMART fehlt: smartmontools installieren";
  renderDiskList();
  await loadReports();

  const stored = localStorage.getItem("selectedDiskName");
  const stillVisible = visibleDisks().some((disk) => disk.name === stored);
  if (stored && stillVisible) {
    await selectDisk(stored);
  } else if (state.selectedDisk && visibleDisks().some((disk) => disk.name === state.selectedDisk.name)) {
    await selectDisk(state.selectedDisk.name);
  } else {
    state.selectedDisk = null;
    byId("detailView").classList.add("hidden");
    byId("emptyState").classList.remove("hidden");
  }
}

async function startTest() {
  if (!state.selectedDisk || getSelectedAppJob()) return;
  const payload = await fetchJson("/api/tests", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ device: state.selectedDisk.name, mode: state.selectedMode }),
  });
  state.currentJobId = payload.job_id;
  await refreshActiveJobs();
  pollSelectedJob();
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
    byId("jobStatus").innerHTML = `done · <a href="/report/${payload.result.report_id}" target="_blank">Bericht oeffnen</a>`;
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
    byId("jobStatus").textContent = "SMART Self-Test abgebrochen.";
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
    body: JSON.stringify({ confirmation, allow_internal: state.settings.allowInternalErase }),
  });
  state.currentJobId = payload.job_id;
  await refreshActiveJobs();
  pollSelectedJob();
}

async function secureEraseSelectedDisk() {
  if (!state.selectedDisk) return;
  const confirmation = byId("eraseConfirmInput").value.trim();
  const payload = await fetchJson(`/api/disks/${state.selectedDisk.name}/secure-erase`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ confirmation, allow_internal: state.settings.allowInternalErase }),
  });
  state.currentJobId = payload.job_id;
  await refreshActiveJobs();
  pollSelectedJob();
}

function bindSettings() {
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
    }
    setControlsBusy();
  };
}

document.addEventListener("DOMContentLoaded", async () => {
  initTheme();
  loadSettings();
  syncSettingsInputs();
  updateSafetyUi();
  byId("themeSystem").onclick = () => applyTheme("system");
  byId("themeDark").onclick = () => applyTheme("dark");
  byId("themeLight").onclick = () => applyTheme("light");
  byId("refreshButton").onclick = async () => {
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
  bindSettings();
  await refreshDisks();
  await refreshActiveJobs();
});
