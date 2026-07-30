# Réhydratation du catalogue commercial

La commande est volontairement inactive au déploiement. Elle joint exclusivement les
`Product` existants au snapshot par `product_id + combination_id`; elle ne fait aucun
rapprochement par nom et n'appelle aucune écriture PrestaShop ou ShopCaisse.

## Priorité et sécurité

Les valeurs `MANUAL`/`DRCLOUD` validées restent prioritaires, puis la valeur canonique
existante, une observation PrestaShop GET déterministe, le snapshot historique, puis
`NO_DATA`. Une contradiction, un EAN invalide/dupliqué ou plusieurs images candidates
est `AMBIGUOUS` et n'est pas appliqué. Une image candidate reste une association
technique; aucun binaire ni `ProductMedia PRIMARY` n'est créé par cette opération.

## Procédure de production

1. Déployer le code sans lancer la commande.
2. Exécuter `dr-cloud-sync catalogue-rehydrate` et conserver le rapport JSON.
3. Contrôler les totaux, tous les cas ambigus et l'échantillon Hyper Max.
4. Exécuter explicitement `dr-cloud-sync catalogue-rehydrate --apply-safe`.
   La commande crée d'abord une sauvegarde SQLite + médias et refuse toute écriture
   si cette sauvegarde échoue.
5. Contrôler le rapport après application, les identités, le stock, l'inventaire et
   les achats. Une seconde exécution doit indiquer `changed: 0`.

## Retour arrière

Arrêter l'application, identifier le chemin `backup` du résumé du JobRun, puis utiliser
la procédure de restauration hors ligne documentée dans `deploy/ovh/RESTORE.md`.
Restaurer le bundle complet DB + médias, redémarrer et exécuter les contrôles de santé.
Il n'existe volontairement aucun bouton de rollback en ligne.
