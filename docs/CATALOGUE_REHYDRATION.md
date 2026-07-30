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

## Workflow CLI

1. Déployer le code sans lancer la commande.
2. Exécuter `dr-cloud-sync catalogue-rehydrate` et conserver le rapport JSON.
3. Contrôler les totaux, tous les cas ambigus et l'échantillon Hyper Max.
4. Exécuter explicitement `dr-cloud-sync catalogue-rehydrate --apply-safe`.
   La commande crée d'abord une sauvegarde SQLite + médias et refuse toute écriture
   si cette sauvegarde échoue.
5. Contrôler le rapport après application, les identités, le stock, l'inventaire et
   les achats. Une seconde exécution doit indiquer `changed: 0`.

## Workflow Administration

La section **Administration > Maintenance du catalogue** rend le même service
accessible sans shell. **Analyser le catalogue** crée un JobRun `PREVIEW` asynchrone,
sans sauvegarde et sans mutation Product, puis conserve durablement le rapport en
base. La synthèse, les métriques avant/après et les lignes paginées peuvent être
filtrées par `SAFE`, `AMBIGUOUS` ou `NO_DATA` et recherchées par nom, variante ou
identité PrestaShop/DrCloud.

L'action **Appliquer les enrichissements sûrs** n'est proposée qu'après un preview
réussi. Sa confirmation rappelle le nombre de produits/champs, les cas ambigus
ignorés et la sauvegarde obligatoire. Le serveur recalcule l'empreinte du catalogue
et des observations : toute modification depuis l'analyse rend le rapport obsolète
et impose un nouveau preview. Un identifiant d'idempotence lié au rapport, ainsi
qu'un verrou de job, empêchent double clic, retry et applications concurrentes.
`DRCLOUD_SAFE_MODE` ne bloque pas cette mutation strictement locale et aucune écriture
externe n'est effectuée.

Après succès, l'Administration affiche les changements, l'identifiant public de la
sauvegarde et les invariants réellement comparés (produits, identités, mouvements de
stock, inventaires, achats et réceptions). `AMBIGUOUS` reste à vérifier manuellement;
`NO_DATA` doit être complété dans la fiche Catalogue et ne reçoit jamais de valeur
inventée.

## Incident et vérifications

Une erreur de sauvegarde arrête le job avant la première mutation. Une erreur
d'intégrité est critique : ne pas tenter de restauration depuis l'interface, relever
l'identifiant du job et de la sauvegarde, arrêter l'application et suivre la
procédure opérateur ci-dessous. Les erreurs API ne révèlent ni chemin de fichier,
ni configuration, ni contenu de sauvegarde.

## Retour arrière

Arrêter l'application, identifier la sauvegarde du résumé du JobRun, puis utiliser
la procédure de restauration hors ligne documentée dans `deploy/ovh/RESTORE.md`.
Restaurer le bundle complet DB + médias, redémarrer et exécuter les contrôles de santé.
Il n'existe volontairement aucun bouton de rollback en ligne.

## Source historique, résolution et packaging

La cause de l'incident du 30 juillet 2026 était une divergence entre tests et image de
production : l'Administration cherchait
`$DRCLOUD_DATA_DIR/catalogue-prestashop-reconstruit.json`, alors que le référentiel
validé était un artefact `dist/` ignoré par le contexte Docker. Le Dockerfile ne
copiait que `src/`; aucun mécanisme ne persistait cet artefact dans `/data`. Les tests
créaient leur propre fichier temporaire ou lisaient `dist/` depuis la racine Git, ce
qui masquait à la fois l'absence dans la wheel et la dépendance au working directory.
`final_mapping.py` décrit et produit le mapping final, mais ne contient aucune des 478
lignes historiques.

La source canonique unique est désormais
`dr_cloud_sync/data/catalogue-prestashop-reconstruit.json`. C'est le snapshot
PrestaShop reconstruit validé (72 parents, 453 combinaisons, soit 478 identités de
vente en comptant les 25 produits sans combinaison). Il est versionné et inclus dans
la wheel par `tool.setuptools.package-data`; l'image Docker installe cette wheel.
`importlib.resources` le résout depuis le package installé, sans dépendre du répertoire
courant. `DRCLOUD_REHYDRATION_SNAPSHOT` reste une surcharge explicite destinée aux
contrôles opératoires et aux tests; elle subit exactement les mêmes validations.

Avant chaque PREVIEW et chaque APPLY_SAFE, le document doit être lisible, contenir
une liste `catalogue` non vide et ne présenter aucune identité dupliquée. L'identité
est exclusivement `(product_id, combination_id)`; aucun nom, ordre, prix, image ou
EAN ne participe à la jointure. Toute absence, corruption ou duplication fait échouer
le job et interdit donc APPLY_SAFE. Le diagnostic Administration ne publie aucun
chemin serveur : il nomme la source, son statut et recommande de vérifier/redéployer
le package.

## Contrôle de disponibilité et procédure PREVIEW

Le statut de réhydratation vérifie réellement la lecture du catalogue SQLite et le
parsing/intégrité du mapping packagé. Il affiche le nombre de produits locaux et les
478 identités du snapshot. PrestaShop et ShopCaisse sont indiqués `NOT_USED`, et non
faussement disponibles : PREVIEW n'effectue aucun appel réseau et n'en a pas besoin.

Après déploiement : ouvrir **Administration > Réhydratation Catalogue**, vérifier
`Catalogue local : Disponible` et `Mapping historique : Disponible`, puis cliquer
uniquement sur **Analyser le catalogue**. Contrôler produits analysés, SAFE,
AMBIGUOUS, NO_DATA, produits enrichissables et champs candidats. Vérifier notamment
les combinaisons Hyper Max 710–716. Le snapshot confirme les variantes PEACH ICE,
STRAWBERRY PUNCH, LOVE, GUM MINT, MINT, MAGIC LOVE et MANGO PINEAPPLE; leurs EAN et
références absents restent `NO_DATA`. Ne pas utiliser APPLY_SAFE dans le cadre de ce
correctif. Si une source est indisponible, conserver le job FAILED, vérifier que la
wheel contient la ressource, puis redéployer le même artefact validé; ne jamais copier
une fixture ou fabriquer un snapshot dans `/data`.
