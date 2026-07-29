"use strict";

const STATUS_LABELS = {DONE: "Terminé", IN_PROGRESS: "En cours", TODO: "À faire", BLOCKED: "Bloqué"};
const clampPercent = value => Number.isFinite(Number(value)) ? Math.min(100, Math.max(0, Number(value))) : 0;
const text = (value, fallback = "—") => value === null || value === undefined || value === "" ? fallback : String(value);

function dashboardModel(roadmap = {}, dashboard = {}) {
  const modules = Array.isArray(roadmap.modules) ? roadmap.modules : [];
  const progress = clampPercent(roadmap.global_progress_percent);
  const remaining = Number.isFinite(Number(roadmap.remaining_percent)) ? clampPercent(roadmap.remaining_percent) : 100 - progress;
  const blocked = modules.some(module => module && module.status === "BLOCKED");
  const status = progress >= 100 ? "DONE" : blocked ? "BLOCKED" : progress > 0 ? "IN_PROGRESS" : "TODO";
  const priorityModule = modules.find(module => module && module.status === "IN_PROGRESS" && module.next) || modules.find(module => module && module.next);
  return {modules, progress, remaining, status, updatedAt: roadmap.updated_at, next: priorityModule && priorityModule.next || dashboard.next,
    nextModule: priorityModule && priorityModule.name, catalogue: dashboard.catalogue,
    inventory: dashboard.inventory || {}, synchronizations: dashboard.synchronizations};
}

function setProgress(element, value) {
  const percent = clampPercent(value);
  element.setAttribute("aria-valuenow", String(percent));
  element.querySelector("span").style.width = `${percent}%`;
}

function statusClass(status) { return `status-${String(status || "TODO").toLowerCase().replace("_", "-")}`; }

function render(model) {
  document.querySelector("#progressHero").classList.remove("loading-card");
  document.querySelector("#progressHero").setAttribute("aria-busy", "false");
  document.querySelector("#globalProgress").textContent = text(model.progress);
  document.querySelector("#remainingProgress").textContent = `${text(model.remaining)} % restant`;
  setProgress(document.querySelector("#progressHero .progress-track"), model.progress);
  const globalStatus = document.querySelector("#globalStatus");
  globalStatus.textContent = STATUS_LABELS[model.status] || STATUS_LABELS.TODO;
  globalStatus.className = `status-badge ${statusClass(model.status)}`;
  document.querySelector("#moduleCount").textContent = `${model.modules.length} module${model.modules.length > 1 ? "s" : ""}`;
  document.querySelector("#updatedAt").textContent = model.updatedAt ? `Roadmap mise à jour le ${new Intl.DateTimeFormat("fr-FR", {dateStyle: "long"}).format(new Date(`${model.updatedAt}T00:00:00`))}` : "Date de mise à jour indisponible";

  const nextAction = document.querySelector("#nextAction");
  nextAction.classList.remove("loading-card"); nextAction.setAttribute("aria-busy", "false");
  document.querySelector("#nextText").textContent = text(model.next, "Aucune prochaine étape renseignée.");
  document.querySelector("#nextModule").textContent = model.nextModule ? `Module · ${model.nextModule}` : "Roadmap DrCloud OS";

  document.querySelector("#catalogueMetric").textContent = model.catalogue === undefined ? "Indisponible" : `${model.catalogue} produit${Number(model.catalogue) > 1 ? "s" : ""}`;
  const inventoryProgress = clampPercent(model.inventory.progress && model.inventory.progress.percent);
  document.querySelector("#inventoryMetric").textContent = model.inventory.progress && model.inventory.progress.percent !== undefined ? `${inventoryProgress} %` : "Indisponible";
  document.querySelector("#inventoryStatus").textContent = text(model.inventory.session && model.inventory.session.status, "Aucune session");
  document.querySelector("#syncMetric").textContent = text(model.synchronizations, "Indisponible");

  const grid = document.querySelector("#moduleGrid"); grid.replaceChildren(); grid.setAttribute("aria-busy", "false");
  const template = document.querySelector("#dashboardModuleTemplate");
  model.modules.forEach((module, index) => {
    const item = module || {}; const status = STATUS_LABELS[item.status] ? item.status : "TODO"; const percent = clampPercent(item.progress_percent);
    const node = template.content.cloneNode(true); const card = node.querySelector("article"); card.classList.add(statusClass(status));
    node.querySelector(".module-index").textContent = String(index + 1).padStart(2, "0");
    const badge = node.querySelector(".module-status"); badge.textContent = STATUS_LABELS[status]; badge.classList.add(statusClass(status));
    node.querySelector("h3").textContent = text(item.name, "Module sans nom"); node.querySelector(".module-percent").textContent = `${percent} %`;
    node.querySelector(".module-weight").textContent = item.weight === undefined ? "Poids non renseigné" : `Poids ${item.weight} %`;
    setProgress(node.querySelector(".progress-track"), percent); node.querySelector(".module-next strong").textContent = text(item.next, status === "DONE" ? "Module terminé" : "Non renseignée");
    grid.appendChild(node);
  });
  if (!model.modules.length) { const empty = document.createElement("p"); empty.className = "empty-state"; empty.textContent = "Aucun module n’est disponible dans la roadmap."; grid.appendChild(empty); }
}

async function loadDashboard() {
  const [roadmapResult, dashboardResult] = await Promise.allSettled([fetch("/api/roadmap"), fetch("/api/dashboard")]);
  try {
    if (roadmapResult.status !== "fulfilled" || !roadmapResult.value.ok) throw new Error("Impossible de charger la roadmap.");
    const roadmap = await roadmapResult.value.json();
    let dashboard = {};
    if (dashboardResult.status === "fulfilled" && dashboardResult.value.ok) dashboard = await dashboardResult.value.json();
    else showError("Les indicateurs opérationnels sont temporairement indisponibles.");
    render(dashboardModel(roadmap, dashboard));
  } catch (error) {
    showError(error instanceof Error ? error.message : "Le tableau de bord est indisponible.");
    render(dashboardModel());
  }
}
function showError(message) { const alert = document.querySelector("#dashboardError"); alert.textContent = message; alert.hidden = false; }

if (typeof document !== "undefined") loadDashboard();
if (typeof module !== "undefined") module.exports = {dashboardModel, clampPercent};
