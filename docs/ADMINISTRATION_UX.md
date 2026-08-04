# Administration UX

Data Hub est l'autorité de l'état runtime des connecteurs. Chaque carte met en avant l'état, la fraîcheur, la dernière réussite, le volume importé et l'action **Tester maintenant**. Une source `CONNECTED · FRESH` affiche son état courant : une ancienne erreur SQLite n'est pas une erreur active.

## Erreurs et diagnostic avancé

Les erreurs actives sont visibles immédiatement. Les erreurs résolues sont repliées dans l'historique et datées « Résolu le… ». Les diagnostics techniques sont masqués dans un panneau avancé afin de ne pas exposer de payload brut dans la lecture métier.

Pour le schéma SumUp, la synthèse attendue est : version **3 / 3**, migrations **à jour**, Web / Worker **même base**, état **OK**. PRAGMA, liste complète des colonnes et empreintes restent exclusivement dans **Diagnostic avancé**. Aucun secret n'est rendu par l'API ou la vue.

Les actions d'administration conservent contrôle de permission, CSRF, désactivation pendant la requête et journal d'audit. Le cockpit réutilise cette fraîcheur pour distinguer READY, PARTIAL, STALE et ERROR.

## Frontière avec Settlement Explorer

Settlement Explorer ne présente que les informations métier et les états de fraîcheur utiles à l’exploitation. Les diagnostics SQLite, schémas, erreurs techniques détaillées et payloads connecteur restent dans **Administration → Avancé**.

## Exploitation quotidienne Settlements

Depuis le cockpit, **Synchroniser**, **Recalculer** et **Importer l'historique** désactivent l'action pendant son exécution et annoncent le résultat via une zone `aria-live`. Une absence Qonto renvoie vers la configuration et reste un état global gris, non une série d'erreurs rouges. L'exploitant commence par les cinq anomalies P0/P1 les plus anciennes, puis ouvre l'Explorer pour la preuve et la décision.

## Diagnostic Qonto

La carte Qonto distingue désormais `NOT_CONFIGURED` (« Configuration Qonto absente du runtime ») d'un `ERROR` actif. Une authentification refusée affiche l'étape d'authentification et le code 401/403; une panne de transport affiche l'étape organization, la catégorie `NETWORK`/`TIMEOUT` et un message assaini. **Tester maintenant** reste disponible lorsqu'un provider configuré est en erreur et actualise l'état sans lancer d'import bancaire complet. Les erreurs résolues restent consultables dans l'historique replié.

## Marketing Intelligence

Les cockpits Social Analytics et Learning Loop présentent explicitement fraîcheur,
couverture et limites. Synchroniser n'active aucun provider. Les permissions
`marketing.analytics.read`, `marketing.learning.read`, `marketing.propose`,
`marketing.review`, `social.read` et `social.sync` séparent lecture et action; STAFF
ne reçoit aucune permission de validation ou publication sensible.

## Marketing Operations

Les permissions fines `marketing.calendar.read/write`, `marketing.campaigns.read`, `marketing.analytics.read`, `marketing.learning.read` et `marketing.export` suivent la matrice RBAC. STAFF ne peut ni approuver ni modifier une campagne sensible. Data Hub conserve les snapshots et jobs internes, tandis que les providers sociaux réels restent `NOT_CONFIGURED`.
