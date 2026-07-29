# ADR 0001 — DrCloud OS comme cœur central

- Statut : accepté
- Date : 2026-07-29

## Contexte
Le produit dépasse la synchronisation de deux fournisseurs. Des fonctions catalogue, inventaire, stock, achats et pilotage doivent partager identité, règles et audit sans devenir des applications isolées.

## Décision
Construire DrCloud OS comme monolithe modulaire. CORE fournit les capacités transverses et chaque domaine expose des services/ports explicites. Les appels intermodules respectent une direction sans cycle.

## Conséquences
Le déploiement et les transactions restent simples. Les frontières rendent les responsabilités testables et extractibles si un besoin réel apparaît. Une discipline d'accès est nécessaire : ni tables voisines ni HTTP fournisseur dans les interfaces métier.
