"""Small, declarative registry for the DrCloud OS application shell."""
from __future__ import annotations

from dataclasses import dataclass
from html import escape


@dataclass(frozen=True)
class Module:
    id: str
    label: str
    group: str
    order: int
    icon: str
    roadmap_id: str | None = None
    route: str | None = None
    page_template: str | None = None
    script: str | None = None

    @property
    def available(self) -> bool:
        """A module is available only when its complete web entry point is declared."""
        return all((self.route, self.page_template, self.script))


GROUPS = ("Principal", "Opérations", "Pilotage", "Automatisation", "Système")

MODULES = (
    Module("dashboard", "Tableau de bord", "Principal", 10, "⌂", "11-dashboard", "/", "dashboard.html", "dashboard.js"),
    Module("roadmap", "Roadmap", "Principal", 20, "◇", route="/roadmap", page_template="roadmap.html", script="roadmap.js"),
    Module("catalogue", "Catalogue", "Opérations", 10, "▦", "02-catalog", "/catalogue", "catalogue.html", "inventory.js"),
    Module("inventory", "Inventaire", "Opérations", 20, "✓", "03-inventory", "/inventaire", "inventory.html", "inventory.js"),
    Module("stock", "Stock", "Opérations", 30, "≋", "04-stock", "/stock", "stock.html", "stock.js"),
    Module("purchasing", "Achats", "Opérations", 40, "↓", "05-purchasing", "/achats", "purchasing.html", "purchasing.js"),
    Module("sales", "Ventes", "Opérations", 50, "↑", "06-sales", "/sales", "sales.html", "sales.js"),
    Module("finance", "Finance", "Pilotage", 10, "◫", "07-finance", "/finance", "finance.html", "finance.js"),
    Module("settlements", "Settlements", "Pilotage", 15, "⇄", "07-finance", "/settlements", "settlements.html", "settlements.js"),
    Module("customers", "Clients", "Pilotage", 20, "♙", "08-customers"),
    Module("marketing", "Marketing", "Pilotage", 30, "◎", "09-marketing", "/marketing", "marketing.html", "marketing.js"),
    Module("synchronizations", "Synchronisations", "Automatisation", 10, "↻", "04-stock"),
    Module("automation", "Automatisations + IA", "Automatisation", 20, "✦", "10-automation"),
    Module("administration", "Administration", "Système", 10, "⚙", route="/administration", page_template="administration.html", script="administration.js"),
    Module("security", "Sécurité", "Système", 20, "◆", "12-security", "/securite", "security.html", "security.js"),
    Module("production", "Production", "Système", 30, "●", "13-production"),
)


def available_pages() -> dict[str, Module]:
    """Index the real pages; future modules deliberately have no route."""
    return {module.page_template: module for module in MODULES if module.available}  # type: ignore[misc]


def render_navigation(active_module_id: str) -> str:
    """Render the shared navigation without turning future modules into links."""
    sections = []
    for group in GROUPS:
        items = []
        for module in sorted((item for item in MODULES if item.group == group), key=lambda item: item.order):
            icon = f'<span class="dc-nav-icon" aria-hidden="true">{escape(module.icon)}</span>'
            label = escape(module.label)
            if module.available:
                current = ' aria-current="page"' if module.id == active_module_id else ""
                items.append(f'<a href="{escape(module.route or "")}"{current}>{icon}<span>{label}</span></a>')
            else:
                items.append(
                    f'<span class="dc-nav-future" aria-disabled="true">{icon}'
                    f'<span>{label}</span><small>À venir</small></span>'
                )
        sections.append(f'<div class="dc-nav-group"><p class="dc-nav-label">{group}</p>{"".join(items)}</div>')
    return "".join(sections)
