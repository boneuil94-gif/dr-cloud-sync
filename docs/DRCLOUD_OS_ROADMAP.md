# Roadmap officielle DrCloud OS

**Version :** 1.0.0 — **mise à jour :** 2026-07-30 — **progression pondérée calculée : 37,89 %** — **reste : 62,11 %**.

La mesure est fondée sur des jalons constatables dans le dépôt : `DONE = 100 %`, `IN_PROGRESS = 50 %`, `TODO/BLOCKED = 0 %` du jalon, puis moyenne des jalons et pondération du bloc. La source exécutable est [`drcloud-os-roadmap.json`](drcloud-os-roadmap.json) et `RoadmapService` recalcule les valeurs ; ce document explicite l'état, il ne pilote pas l'interface.

| Bloc | Statut | Progression | Poids | Progression pondérée |
|---|---:|---:|---:|---:|
| 01 Core + architecture | IN_PROGRESS | 60 % | 10 | 6.0 |
| 02 Catalogue + mapping | IN_PROGRESS | 80 % | 10 | 8.0 |
| 03 Inventaire + EAN | IN_PROGRESS | 80 % | 10 | 8.0 |
| 04 Stock + synchronisation | IN_PROGRESS | 90 % | 10 | 9.0 |
| 05 Achats + fournisseurs | IN_PROGRESS | 10 % | 10 | 1.0 |
| 06 Ventes | IN_PROGRESS | 5 % | 8 | 0.4 |
| 07 Finance + pilotage | TODO | 0 % | 8 | 0.0 |
| 08 Clients + fidélité | TODO | 0 % | 6 | 0.0 |
| 09 Marketing + réseaux sociaux | TODO | 0 % | 8 | 0.0 |
| 10 Automatisations + IA | IN_PROGRESS | 5 % | 8 | 0.4 |
| 11 Dashboard | IN_PROGRESS | 10 % | 5 | 0.5 |
| 12 Sécurité + utilisateurs | IN_PROGRESS | 10 % | 4 | 0.4 |
| 13 Production + PWA | IN_PROGRESS | 70 % | 3 | 2.1 |
| **Total** | **IN_PROGRESS** | **37,89 %** | **100** | **37.89** |

## État opérationnel par bloc

### 01 — Core + architecture
- **Terminé :** séparation domaine/services/repositories, types métier, ActivityLog, ports repository.
- **En cours :** catalogue d'événements et conventions communes.
- **Prochaine étape :** idempotence persistée, jobs reprenables, notifications et autorisation complète.
- **Bloqué :** rien.

### 02 — Catalogue + mapping
- **Terminé :** mapping certain 478/478, clé DrCloud stable, catalogue central, recherche, EAN, BarcodeAssignment dry-run et tests.
- **En cours :** aucun jalon partiel comptabilisé.
- **Prochaine étape :** cycle de vie complet de DrCloudProduct, avec statut et timestamps persistés.
- **Bloqué :** activation live volontairement exclue.

### 03 — Inventaire + EAN
- **Terminé :** V1, interface OS V2, sessions SQLite, scan/comptage, progression/exports, préparation EAN, rapprochement et workflow validé Inventory → Stock local.
- **En cours :** aucun jalon partiel comptabilisé.
- **Prochaine étape :** tests terrain EAN.
- **Bloqué :** écriture EAN live non autorisée à ce stade.

### 04 — Stock + synchronisation
- **Terminé :** StockMovement, types, idempotence, validation, ledger permanent, projection Stock read-only, test bout-en-bout Inventaire → Stock et observation PrestaShop contrôlée sans écriture externe.
- **En cours :** aucun jalon partiel comptabilisé.
- **Prochaine étape :** alertes métier après définition de seuils explicites ; ShopCaisse reste non observable faute de snapshot quantité persistant, daté et relié à un job.
- **Bloqué :** aucun ; les alertes de stock faible attendent encore une règle métier explicite.

### 05 — Achats + fournisseurs
- **Terminé :** frontière et workflow cible documentés.
- **En cours :** aucun.
- **Prochaine étape :** modèles Supplier/Purchase/GoodsReceipt et repositories locaux.
- **Bloqué :** aucun ; OCR complet différé.

### 06 — Ventes
- **Terminé :** aucun.
- **En cours :** frontière et modèles conceptuels unifiés documentés.
- **Prochaine étape :** modèles locaux et import idempotent en lecture.
- **Bloqué :** aucun.

### 07 — Finance + pilotage
- **Terminé / En cours :** aucun jalon d'implémentation.
- **Prochaine étape :** définir les projections analytiques à partir de Sales, Purchasing et Stock.
- **Bloqué :** disponibilité future des faits métier.

### 08 — Clients + fidélité
- **Terminé / En cours :** aucun jalon d'implémentation.
- **Prochaine étape :** Customer, CustomerIdentity et CustomerConsent sans fusion automatique.
- **Bloqué :** aucun.

### 09 — Marketing + réseaux sociaux
- **Terminé / En cours :** aucun jalon d'implémentation.
- **Prochaine étape :** modèles Content/Campaign/SocialPost indépendants des plateformes.
- **Bloqué :** aucun.

### 10 — Automatisations + IA
- **Terminé :** aucun.
- **En cours :** règles, séparation READ/PROPOSE/EXECUTE et validation humaine documentées.
- **Prochaine étape :** dispatcher événementiel local et propositions persistées.
- **Bloqué :** aucun.

### 11 — Dashboard
- **Terminé :** frontière de projection documentée.
- **En cours :** vue Roadmap dynamique lisant le service.
- **Prochaine étape :** projections ventes, marge et stock.
- **Bloqué :** faits des modules futurs.

### 12 — Sécurité + utilisateurs
- **Terminé :** principes de gestion des secrets documentés.
- **En cours :** permissions cibles.
- **Prochaine étape :** User, Role, Permission et contrôle serveur.
- **Bloqué :** aucun.

### 13 — Production + PWA
- **Terminé :** image Docker, authentification, PWA, sauvegarde/restauration, health check et kit OVH reproductible préparé (Compose local-only, Caddy exemple, bootstrap, firewall et procédures).
- **En cours :** attente du VPS; aucun déploiement, DNS ou certificat n'a encore été réalisé.
- **Prochaine étape :** valider le VPS et les accès, exécuter la checklist en SAFE_MODE/dry-run, puis configurer DNS et HTTPS lors d'une intervention contrôlée.
- **Bloqué :** aucun.

## Ordre de livraison

Consolider d'abord Core/Catalog/Inventory/Stock sans écriture distante, puis Purchasing et Sales qui alimentent Stock. Finance, Customers et Marketing consomment ensuite ces faits. Automation/AI, Dashboard, sécurité et production progressent transversalement, sans contourner les validations.
