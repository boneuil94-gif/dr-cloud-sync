# ADR 0004 — Systèmes externes derrière des connecteurs

- Statut : accepté
- Date : 2026-07-29

## Contexte
PrestaShop et ShopCaisse ont leurs propres API, erreurs et cycles de vie. Les détails HTTP dispersés dans l'UI coupleraient tout le métier aux fournisseurs.

## Décision
Les modules métier dépendent de ports. INTEGRATIONS fournit les adaptateurs PrestaShop, ShopCaisse et futurs services et encapsule authentification, timeout, retry, limites, erreurs, idempotence et health status.

## Conséquences
Les services se testent avec des doubles et un fournisseur se remplace sans réécrire le métier. Il faut maintenir les contrats et traductions. Les secrets restent côté serveur, hors modèles métier et navigateur.
