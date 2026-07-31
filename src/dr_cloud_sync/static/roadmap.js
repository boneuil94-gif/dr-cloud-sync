"use strict";
const escapeRoadmap = value => String(value ?? "").replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
fetch("/api/roadmap").then(response => {
  if (!response.ok) throw new Error("Roadmap indisponible");
  return response.json();
}).then(roadmap => {
  document.querySelector("#globalProgress").textContent = `${roadmap.global_progress_percent} %`;
  document.querySelector("#roadmapRemaining").textContent = `${roadmap.remaining_percent} %`;
  const target = document.querySelector("#roadmapModules");
  const template = document.querySelector("#moduleTemplate");
  roadmap.modules.forEach(module => {
    const node = template.content.cloneNode(true);
    node.querySelector("h2").textContent = module.name;
    const badge=node.querySelector(".status-badge"); badge.textContent=module.status; badge.classList.add(`status-${module.status.toLowerCase().replace('_','-')}`);
    node.querySelector(".module-percent").textContent = `${module.progress_percent} %`;
    node.querySelector(".module-weight").textContent = `Poids global : ${module.weight} %`;
    node.querySelector("progress").value = module.progress_percent;
    node.querySelector(".next").textContent = module.next || "—";
    node.querySelector(".milestone-list").innerHTML=module.milestones.map(m=>`<li><span class="status-badge status-${m.status.toLowerCase().replace('_','-')}">${escapeRoadmap(m.status)}</span><span>${escapeRoadmap(m.name)}${m.steps?` <small>(${m.steps.filter(s=>s.done).length}/${m.steps.length} sous-étapes)</small>`:''}${m.blocking_reason?`<small class="milestone-reason">${escapeRoadmap(m.blocking_reason)}</small>`:''}</span></li>`).join('');
    target.appendChild(node);
  });
}).catch(error => { const alert=document.querySelector("#roadmapError"); alert.hidden=false; alert.textContent=error.message; });
