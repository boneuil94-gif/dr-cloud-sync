"use strict";
const list = value => value && value.length ? value.join(" · ") : "—";
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
    node.querySelector(".module-percent").textContent = `${module.progress_percent} %`;
    node.querySelector("progress").value = module.progress_percent;
    node.querySelector(".completed").textContent = list(module.completed);
    node.querySelector(".in-progress").textContent = list(module.in_progress);
    node.querySelector(".next").textContent = module.next || "—";
    node.querySelector(".blocked").textContent = list(module.blocked);
    target.appendChild(node);
  });
}).catch(error => { document.querySelector("#roadmapError").textContent = error.message; });
