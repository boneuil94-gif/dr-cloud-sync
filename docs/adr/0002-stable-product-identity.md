# ADR 0002 — Identité produit DrCloud stable

- Statut : accepté
- Date : 2026-07-29

## Contexte
EAN, nom, prix, stock et identifiants de présentation peuvent changer. Une identité fondée sur ces attributs casserait historique, mouvements et rapprochements.

## Décision
`drcloud_product_key` est l'identité permanente de `DrCloudProduct`. Les clés PrestaShop, combinaison et ShopCaisse sont des correspondances externes. Le mapping certain 478/478 initialise le catalogue sans rendre la clé dépendante des champs mutables.

## Conséquences
Les changements de données ne recréent pas le produit. Les imports doivent résoudre les correspondances et signaler les ambiguïtés. Toute fusion/séparation exceptionnelle exige une opération auditée.
