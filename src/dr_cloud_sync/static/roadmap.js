"use strict";
const dimensionLabels={code_maturity:"Code maturity",production_maturity:"Production maturity",business_completeness:"Business completeness",security:"Security",observability:"Observability",test_quality:"Test quality",ux:"UX"};
fetch("/api/roadmap").then(response=>{if(!response.ok)throw new Error("Roadmap indisponible");return response.json();}).then(roadmap=>{
  document.querySelector("#globalProgress").textContent=`${roadmap.global_score} %`;
  document.querySelector("#evidenceDate").textContent=new Intl.DateTimeFormat("fr-FR",{dateStyle:"long",timeZone:"UTC"}).format(new Date(`${roadmap.evidence_date}T00:00:00Z`));
  const summary=document.querySelector("#roadmapSummary");
  Object.entries(roadmap.dimensions).forEach(([key,value])=>{const card=document.createElement("article");card.className="dc-card";const label=document.createElement("span");label.textContent=dimensionLabels[key]||key;const score=document.createElement("strong");score.textContent=String(value);card.append(label,score);summary.append(card);});
  const target=document.querySelector("#roadmapModules"),template=document.querySelector("#moduleTemplate");
  roadmap.modules.forEach(module=>{const node=template.content.cloneNode(true);node.querySelector("h2").textContent=module.name;const badge=node.querySelector(".status-badge");badge.textContent=module.status;badge.classList.add(`status-${module.status.toLowerCase().replaceAll("_","-")}`);node.querySelector(".module-percent").textContent=`${module.score} %`;node.querySelector(".module-evidence").textContent=`Niveau de preuve : ${module.evidence_level}`;node.querySelector("progress").value=module.score;node.querySelector(".justification").textContent=module.justification;node.querySelector(".blocker").textContent=module.blocker;node.querySelector(".next").textContent=module.next_step;target.appendChild(node);});
}).catch(error=>{const alert=document.querySelector("#roadmapError");alert.hidden=false;alert.textContent=error.message;});
