# Sales Ledger — Analytics v1

## Autorités et sources

Le Sales Ledger est le journal commercial append-only de DrCloud OS. Il décrit
uniquement des ventes effectivement fournies par une source. `SHOPCAISSE`,
`PRESTASHOP`, `MANUAL` et `IMPORT` sont des identités de source, pas la promesse
qu'un connecteur existe. En v1, les connecteurs ShopCaisse et PrestaShop sont
explicitement `NOT_CONFIGURED`; seul l'import CSV contrôlé est disponible.

Le catalogue canonique est l'autorité d'identité. Le rapprochement est strict et
unique, par `drcloud_product_key`, EAN, référence ou identifiant ShopCaisse. Il
n'existe aucun fuzzy matching. Une ambiguïté ou une absence produit conserve la
ligne avec le statut `UNMATCHED` afin qu'elle reste diagnostiquable.

## Modèle, corrections et idempotence

Une ligne porte sa source, l'ordre et la ligne externes, la date avec offset, le
fuseau déclaré, le type (`SALE`, `REFUND`, `RETURN`, `CANCELLATION`,
`ADJUSTMENT`), la quantité positive, les montants HT/TTC optionnels, devise,
canal et lieu. Retours et remboursements sont de nouveaux événements à effet
négatif; aucune vente passée n'est effacée.

La clé SHA-256 déterministe de `source + external_sale_id + external_line_id +
event_kind` et une contrainte SQLite rendent les réimports idempotents. Un import
CSV exige un PREVIEW persistant, sans écriture commerciale, puis l'APPLY exact du
même contenu dans une transaction. Toute ligne invalide bloque l'apply.

Colonnes CSV reconnues :

`source, external_sale_id, external_line_id, sold_at, timezone, event_kind,
product_key, ean, reference, shopcaisse_item_id, quantity, unit_price_ttc,
unit_price_ht, line_total_ttc, line_total_ht, cost_basis, currency, channel,
location, raw_reference, source_updated_at`.

## Métriques et périodes

Les fenêtres 7 et 30 jours sont des intervalles UTC semi-ouverts `[début, fin)`;
la période précédente a exactement la même durée. Ceci évite les doubles comptes
aux frontières. Les tests injectent un `as_of` déterministe. Les unités tiennent
compte des événements négatifs. Le CA TTC et le CA HT restent séparés. Si une
ligne de la fenêtre n'a pas le montant demandé, le KPI est indisponible plutôt
qu'artificiellement égal à zéro. Croissance et prix moyen restent indisponibles
quand leur dénominateur fiable manque.

Une fraîcheur par défaut de 48 heures sépare `FRESH`, `STALE` et `UNAVAILABLE`.
Les signaux actuels (`BEST_SELLER`, `SALES_SPIKE`, `SALES_DROP`,
`TRENDING_PRODUCT`) sont déterministes et ne consomment que des données fraîches.
Les seuils v1 sont visibles dans `MarketingAutopilot`: top 3 et 10 unités pour un
best-seller; 10 unités et +50 % pour un spike; historique de 10 unités et -40 %
pour une baisse; 8 unités et +25 % pour une tendance. Les faits, classement,
croissance, score et composantes absentes accompagnent la proposition.

## Séparation Sales / Stock et limites

L'import ne touche jamais le catalogue, ProductMedia, Stock Ledger, PrestaShop
ou ShopCaisse. Une vente n'est **pas** un mouvement physique et ne décrémente
jamais le stock. Le coût historique n'étant pas prouvé, la marge est
indisponible. La faible rotation reste indisponible sans stock marketing qualifié.
Une proximité temporelle entre publication et vente n'est pas une attribution.
Les conversions sociales restent nulles/inconnues sauf valeur réellement livrée
par un provider.
