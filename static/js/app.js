const form = document.querySelector("#hazard-form");
const imageInput = document.querySelector("#images");
const previews = document.querySelector("#image-previews");
const uploadZone = document.querySelector("#upload-zone");
const MAX_FILE_BYTES = 8 * 1024 * 1024;
const MAX_FILES = 5;
let selectedFiles = [];

const categoryIcons = {
  pothole: "⌁", road: "⌁", manhole: "◉", garbage: "▤", drain: "≋",
  water: "◌", tree: "♧", electrical: "ϟ", infrastructure: "◇",
};

function safeText(value) {
  return typeof value === "string" ? value : "";
}

function addText(parent, tag, text, className) {
  const node = document.createElement(tag);
  node.textContent = safeText(text);
  if (className) node.className = className;
  parent.append(node);
  return node;
}

function categoryIcon(category) {
  const normalized = safeText(category).toLowerCase();
  const match = Object.keys(categoryIcons).find((key) => normalized.includes(key));
  return match ? categoryIcons[match] : "◇";
}

function errorMessage(message) {
  const node = document.querySelector("#form-error");
  node.textContent = message;
  node.hidden = false;
  node.focus?.();
}

function clearError() {
  const node = document.querySelector("#form-error");
  node.textContent = "";
  node.hidden = true;
}

function renderPreviews() {
  previews.replaceChildren();
  selectedFiles.forEach((file, index) => {
    const item = document.createElement("div");
    item.className = "preview-item";
    item.title = file.name;
    const image = document.createElement("img");
    image.alt = `Preview of ${file.name}`;
    image.src = URL.createObjectURL(file);
    const remove = document.createElement("button");
    remove.type = "button";
    remove.className = "remove-image";
    remove.setAttribute("aria-label", `Remove ${file.name}`);
    remove.textContent = "×";
    remove.addEventListener("click", () => {
      URL.revokeObjectURL(image.src);
      selectedFiles = selectedFiles.filter((_, fileIndex) => fileIndex !== index);
      syncInputFiles();
      renderPreviews();
    });
    item.append(image, remove);
    previews.append(item);
  });
}

function syncInputFiles() {
  const transfer = new DataTransfer();
  selectedFiles.forEach((file) => transfer.items.add(file));
  imageInput.files = transfer.files;
}

function acceptFiles(incoming) {
  clearError();
  const supported = /\.(png|jpe?g|webp)$/i;
  for (const file of incoming) {
    if (!supported.test(file.name)) {
      errorMessage(`${file.name} is not supported. Choose PNG, JPG, JPEG, or WEBP.`);
      continue;
    }
    if (file.size > MAX_FILE_BYTES) {
      errorMessage(`${file.name} is larger than 8 MB.`);
      continue;
    }
    if (selectedFiles.length >= MAX_FILES) {
      errorMessage(`Choose no more than ${MAX_FILES} images.`);
      break;
    }
    if (!selectedFiles.some((item) => item.name === file.name && item.size === file.size)) {
      selectedFiles.push(file);
    }
  }
  syncInputFiles();
  renderPreviews();
}

if (imageInput) {
  imageInput.addEventListener("change", () => {
    acceptFiles(Array.from(imageInput.files || []));
  });
  ["dragenter", "dragover"].forEach((eventName) => uploadZone.addEventListener(eventName, (event) => {
    event.preventDefault();
    uploadZone.classList.add("dragging");
  }));
  ["dragleave", "drop"].forEach((eventName) => uploadZone.addEventListener(eventName, (event) => {
    event.preventDefault();
    uploadZone.classList.remove("dragging");
  }));
  uploadZone.addEventListener("drop", (event) => acceptFiles(Array.from(event.dataTransfer.files)));
}

const description = document.querySelector("#description");
description?.addEventListener("input", () => {
  document.querySelector("#description-count").textContent = `${description.value.length} / 2000`;
});

function setBusy(isBusy) {
  document.querySelector("#submit-button").disabled = isBusy;
  document.querySelector("#progress").hidden = !isBusy;
  document.querySelector("#progress-title").textContent = isBusy ? "Preparing your report" : "";
  document.querySelector("#progress-detail").textContent = isBusy
    ? "Validating your submission securely…"
    : "";
}

function addList(parent, items) {
  const list = document.createElement("ul");
  const safeItems = Array.isArray(items) ? items : [];
  if (!safeItems.length) safeItems.push("No details supplied.");
  safeItems.forEach((item) => addText(list, "li", item));
  parent.append(list);
}

function makeBlock(title, items, isParagraph = false) {
  const block = document.createElement("section");
  block.className = "result-block";
  addText(block, "h3", title);
  if (isParagraph) {
    addText(block, "p", items || "No details supplied.");
  } else {
    addList(block, items);
  }
  return block;
}

function formatDate(value) {
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime())
    ? value
    : parsed.toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" });
}

function renderResult(report) {
  const section = document.querySelector("#results-section");
  section.replaceChildren();
  section.hidden = false;

  const top = document.createElement("div");
  top.className = "result-topline";
  const identity = document.createElement("div");
  addText(identity, "div", "03 · ANALYSIS SUMMARY", "result-kicker");
  addText(identity, "div", `Reference ${report.reference} · ${formatDate(report.created_at)}`, "result-reference");
  const actions = document.createElement("div");
  actions.className = "result-actions";
  const copy = document.createElement("button");
  copy.type = "button";
  copy.className = "button button-secondary";
  copy.textContent = "Copy summary";
  copy.addEventListener("click", async () => {
    const summary = [
      `NagrikShield report ${report.reference}`,
      `Mode: ${report.mode_label}`,
      `Category: ${report.hazard_category}`,
      `Severity: ${report.severity}`,
      `Preliminary score: ${report.risk_score}/100`,
      `Explanation: ${report.explanation}`,
      "Human review required. This is not an official determination or authority submission.",
    ].join("\n");
    try {
      await navigator.clipboard.writeText(summary);
      copy.textContent = "Copied!";
      setTimeout(() => { copy.textContent = "Copy summary"; }, 1800);
    } catch {
      errorMessage("Clipboard access was blocked. Select and copy the report details manually.");
    }
  });
  const pdf = document.createElement("a");
  pdf.className = "button button-primary";
  pdf.href = report.pdf_url;
  pdf.textContent = "Download PDF ↓";
  actions.append(copy, pdf);
  top.append(identity, actions);
  section.append(top);

  const banner = document.createElement("div");
  banner.className = "mode-banner";
  banner.textContent = report.mode_label;
  section.append(banner);

  const grid = document.createElement("div");
  grid.className = "result-grid";
  const riskCard = document.createElement("div");
  riskCard.className = "risk-card";
  const score = Math.min(100, Math.max(0, Number(report.risk_score) || 0));
  const ring = document.createElement("div");
  ring.className = "risk-ring";
  ring.style.setProperty("--score-angle", `${score * 3.6}deg`);
  const ringInner = document.createElement("div");
  ringInner.className = "risk-ring-inner";
  addText(ringInner, "strong", String(score));
  addText(ringInner, "small", "PRELIMINARY / 100");
  ring.append(ringInner);
  riskCard.append(ring);
  addText(riskCard, "div", report.severity, "risk-severity");
  addText(riskCard, "div", report.hazard_category, "risk-category");
  addText(riskCard, "p", report.risk_score_basis, "risk-basis");

  const content = document.createElement("div");
  content.className = "result-content";
  content.append(makeBlock("Visible / submitted observations", report.visible_observations));
  const columns = document.createElement("div");
  columns.className = "result-columns";
  columns.append(
    makeBlock("Possible risks · inference", report.possible_risks),
    makeBlock("Missing evidence", report.missing_evidence),
  );
  content.append(columns);
  content.append(makeBlock("Recommended safe next steps", report.recommended_actions));
  content.append(makeBlock("Explanation", report.explanation, true));
  content.append(makeBlock("Uncertainty", report.uncertainty, true));
  grid.append(riskCard, content);
  section.append(grid);

  const review = document.createElement("div");
  review.className = "human-review";
  const reviewIcon = addText(review, "span", "!");
  reviewIcon.setAttribute("aria-hidden", "true");
  const reviewCopy = document.createElement("span");
  const reviewTitle = addText(reviewCopy, "strong", "Human review required.");
  reviewTitle.append(document.createTextNode(
    " This is a preliminary prioritization aid, not a calibrated probability or official risk determination. For immediate threats, move to safety and contact appropriate local emergency services or civic authorities.",
  ));
  review.append(reviewCopy);
  section.append(review);
  const meta = document.createElement("div");
  meta.className = "result-meta";
  meta.textContent = `Submitted description: ${report.description || "Not provided"} · User-entered location: ${report.location || "Not provided"} (unverified) · ${report.image_count} image(s). No authority was contacted.`;
  section.append(meta);
  section.scrollIntoView({ behavior: "smooth", block: "start" });
}

form?.addEventListener("submit", async (event) => {
  event.preventDefault();
  clearError();
  const descriptionValue = document.querySelector("#description").value.trim();
  const observationsValue = document.querySelector("#observations").value.trim();
  if (!descriptionValue && !observationsValue && !selectedFiles.length) {
    errorMessage("Add a description, observations, or at least one image.");
    return;
  }
  setBusy(true);
  document.querySelector("#progress-title").textContent =
    document.querySelector('input[name="mode"]:checked').value === "demo"
      ? "Preparing clearly labelled demo results"
      : "Analyzing with Gemma 4";
  document.querySelector("#progress-detail").textContent =
    "Images are validated on the server; live mode sends them to Google for analysis.";
  try {
    const response = await fetch("/api/analyze", { method: "POST", body: new FormData(form) });
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || `Request failed (${response.status}).`);
    renderResult(result.report);
    await loadDashboardData();
  } catch (error) {
    errorMessage(error.message || "Could not reach the local server. Check that Flask is running.");
  } finally {
    setBusy(false);
  }
});

function buildCategoryChart(reports) {
  const container = document.querySelector("#category-chart");
  container.replaceChildren();
  const counts = new Map();
  reports.forEach((report) => {
    const category = report.hazard_category || "Unknown";
    counts.set(category, (counts.get(category) || 0) + 1);
  });
  if (!counts.size) {
    addText(container, "div", "No saved reports yet. Your analyses will appear here.", "chart-empty");
    return;
  }
  const highest = Math.max(...counts.values());
  [...counts.entries()].slice(0, 6).forEach(([category, count]) => {
    const row = document.createElement("div");
    row.className = "chart-row";
    addText(row, "span", category);
    const track = document.createElement("div");
    track.className = "chart-track";
    const fill = document.createElement("div");
    fill.className = "chart-fill";
    fill.style.width = `${(count / highest) * 100}%`;
    track.append(fill);
    row.append(track);
    addText(row, "span", String(count), "chart-count");
    container.append(row);
  });
}

function buildSeverityChart(reports) {
  const container = document.querySelector("#severity-chart");
  container.replaceChildren();
  const labels = ["Low", "Moderate", "High", "Critical", "Unknown"];
  const counts = Object.fromEntries(labels.map((label) => [label, 0]));
  reports.forEach((report) => {
    if (Object.hasOwn(counts, report.severity)) counts[report.severity] += 1;
  });
  labels.forEach((label) => {
    const row = document.createElement("div");
    row.className = "severity-row";
    addText(row, "span", label);
    const track = document.createElement("div");
    track.className = "severity-track";
    const fill = document.createElement("div");
    fill.className = `severity-fill ${label.toLowerCase()}`;
    fill.style.width = `${reports.length ? (counts[label] / reports.length) * 100 : 0}%`;
    track.append(fill);
    row.append(track);
    addText(row, "span", String(counts[label]));
    container.append(row);
  });
}

function reportItem(report) {
  const row = document.createElement("div");
  row.className = "recent-item";
  const icon = document.createElement("span");
  icon.className = "hazard-avatar";
  icon.textContent = categoryIcon(report.hazard_category);
  const main = document.createElement("span");
  main.className = "recent-main";
  addText(main, "strong", report.hazard_category);
  addText(main, "small", `${formatDate(report.created_at)} · ${report.mode_label}`);
  const severity = document.createElement("span");
  severity.className = `severity-pill ${safeText(report.severity).toLowerCase()}`;
  severity.textContent = report.severity;
  row.append(icon, main, severity);
  return row;
}

function renderReports(reports) {
  const recent = document.querySelector("#recent-list");
  if (recent) {
    recent.replaceChildren();
    if (!reports.length) addText(recent, "div", "Your recent analyses will appear here after your first report.", "empty-recent");
    reports.slice(0, 4).forEach((report) => recent.append(reportItem(report)));
  }
  const history = document.querySelector("#history-list");
  if (history) {
    history.replaceChildren();
    if (!reports.length) {
      addText(history, "div", "No locally saved reports yet. Create your first submission from the dashboard.", "history-empty");
    } else {
      reports.forEach((report) => {
        const row = document.createElement("div");
        row.className = "history-row";
        const identity = document.createElement("div");
        identity.className = "history-report";
        addText(identity, "strong", report.hazard_category);
        addText(identity, "small", `${report.reference} · ${report.mode_label}`);
        addText(row, "span", formatDate(report.created_at), "history-date");
        addText(row, "span", report.severity, `severity-pill ${safeText(report.severity).toLowerCase()}`);
        addText(row, "span", `${Math.min(100, Math.max(0, Number(report.risk_score) || 0))}/100`, "history-score");
        const download = document.createElement("a");
        download.className = "button button-secondary";
        download.href = `/reports/${encodeURIComponent(report.reference)}.pdf`;
        download.textContent = "Download PDF";
        row.prepend(identity);
        row.append(download);
        history.append(row);
      });
    }
  }
}

async function loadDashboardData() {
  try {
    const response = await fetch("/api/history");
    if (!response.ok) throw new Error("History is temporarily unavailable.");
    const data = await response.json();
    const reports = data.reports || [];
    const count = document.querySelector("#total-count");
    if (count) {
      count.textContent = String(data.total || 0);
      document.querySelector("#urgent-count").textContent = String(reports.filter((item) => ["High", "Critical"].includes(item.severity)).length);
      document.querySelector("#category-count").textContent = String(new Set(reports.map((item) => item.hazard_category)).size);
      document.querySelector("#review-count").textContent = String(data.total || 0);
      buildCategoryChart(reports);
      buildSeverityChart(reports);
    }
    renderReports(reports);
  } catch (error) {
    const target = document.querySelector("#recent-list") || document.querySelector("#history-list");
    if (target) addText(target, "p", error.message, "error-message");
  }
}

const isHistoryPage = document.body.dataset.page === "history";
document.querySelector("#dashboard-view").hidden = isHistoryPage;
document.querySelector("#history-view").hidden = !isHistoryPage;
const today = document.querySelector("#today-label");
if (today) today.textContent = new Date().toLocaleDateString(undefined, { weekday: "short", month: "short", day: "numeric", year: "numeric" });
loadDashboardData();
