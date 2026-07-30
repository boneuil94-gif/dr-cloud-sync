# Consolidation du catalogue durable — PR G

## Audit avant / après

Avant cette consolidation, `Product` n'avait aucun statut, le catalogue utilisé par l'OS était reconstruit en mémoire depuis le mapping, SQLite ne conservait que les surcharges EAN et l'initialisation remplaçait les données. Inventaire et déploiement exigeaient exactement 478 lignes. Les références externes et l'EAN n'avaient pas d'invariant SQLite global.

Après consolidation, chaque produit complet (identité, références PrestaShop et ShopCaisse, nom, référence, EAN, statut et timestamps) est chargé depuis `drcloud_products`. Le fichier certain 478/478 reste uniquement la graine historique : il n'est ni une limite, ni une source de mise à jour implicite.

## Identité, cycle de vie et transitions

`drcloud_product_key` est permanent. Nom, référence, EAN, prix, stock et correspondances externes sont mutables et ne doivent jamais provoquer sa régénération. Les trois états sont :

- `ACTIVE` : exploitable ;
- `INACTIVE` : temporairement non exploitable ;
- `ARCHIVED` : conservé en lecture pour l'historique.

Transitions : `ACTIVE → INACTIVE|ARCHIVED`, `INACTIVE → ACTIVE|ARCHIVED`, `ARCHIVED → INACTIVE`. Une réactivation directe d'une archive est interdite afin d'imposer une revue intermédiaire. L'archivage ne supprime aucune ligne et Stock continue donc à résoudre le produit et ses mouvements.

## Migration additive et bootstrap

Au démarrage, les colonnes structurées absentes sont ajoutées à l'ancienne table JSON. Les lignes JSON sont copiées dans ces colonnes sans suppression. Le bootstrap utilise `INSERT OR IGNORE` : il crée les identités absentes mais ne remplace jamais un produit existant. Il est idempotent et supporte tout nombre positif de produits.

Les EAN non vides sont uniques au niveau SQLite lorsque les données existantes sont cohérentes. Si des doublons EAN historiques sont détectés, aucune ligne n'est supprimée ou fusionnée et l'index n'est pas créé ; le diagnostic UI les expose et toute nouvelle attribution conflictuelle est refusée. Les identités PrestaShop `(prestashop_key, product_id, combination_id)` et ShopCaisse sont uniques entre produits non archivés ; une ambiguïté existante bloque la migration, sans traitement destructif. Une chaîne vide n'est jamais une identité canonique valide.

## Compatibilité et réaudit du palier

Inventaire accepte désormais un mapping validé, non vide et cohérent sans constante 478 ; création de session, recherche, scan, progression, comptage, exports et workflow Stock restent fondés sur les clés durables. Les sessions, comptages et propositions historiques restent dans leurs tables inchangées. Stock conserve 90 % : ledger, projection et observations externes n'ont pas reçu de crédit artificiel, et une archive reste résolue en lecture. Inventaire reste à 80 %, Core à 70 %, Catalogue passe honnêtement de 80 % à 90 %. Dashboard et Production ne changent pas. La progression pondérée globale passe de 37,89 % à **38,89 %**.

Le reliquat Catalogue est l'adapter PostgreSQL. Les tests terrain EAN restent le prochain jalon Inventaire ; alertes métier et observation ShopCaisse restent les écarts Stock. Achats, Ventes, Finance, Clients et Marketing ne reçoivent aucun crédit pour leur navigation ou leur documentation. Le palier suivant recommandé est de valider le catalogue et les EAN sur données réelles, puis de démarrer un unique flux métier Achats ou Ventes sans élargir prématurément le catalogue en ERP.
