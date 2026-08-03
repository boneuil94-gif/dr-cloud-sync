# Administration UX

Data Hub est l'autorité de l'état runtime des connecteurs. Chaque carte met en avant l'état, la fraîcheur, la dernière réussite, le volume importé et l'action **Tester maintenant**. Une source `CONNECTED · FRESH` affiche son état courant : une ancienne erreur SQLite n'est pas une erreur active.

## Erreurs et diagnostic avancé

Les erreurs actives sont visibles immédiatement. Les erreurs résolues sont repliées dans l'historique et datées « Résolu le… ». Les diagnostics techniques sont masqués dans un panneau avancé afin de ne pas exposer de payload brut dans la lecture métier.

Pour le schéma SumUp, la synthèse attendue est : version **3 / 3**, migrations **à jour**, Web / Worker **même base**, état **OK**. PRAGMA, liste complète des colonnes et empreintes restent exclusivement dans **Diagnostic avancé**. Aucun secret n'est rendu par l'API ou la vue.

Les actions d'administration conservent contrôle de permission, CSRF, désactivation pendant la requête et journal d'audit. Le cockpit réutilise cette fraîcheur pour distinguer READY, PARTIAL, STALE et ERROR.

## Frontière avec Settlement Explorer

Settlement Explorer ne présente que les informations métier et les états de fraîcheur utiles à l’exploitation. Les diagnostics SQLite, schémas, erreurs techniques détaillées et payloads connecteur restent dans **Administration → Avancé**.
