# Gestion des secrets

Les tables métier ne stockent que des références opaques. `secret_references` conserve provider, usage, état et dates, jamais la valeur. Les valeurs PrestaShop, Qonto, ShopCaisse et futurs providers sociaux sont injectées au runtime par variables protégées/SecretProvider et ne doivent être ni renvoyées, ni journalisées, ni incluses aux artifacts.

## Rotation

1. Créer la nouvelle valeur dans le gestionnaire runtime et une nouvelle référence opaque.
2. Vérifier la connexion sans afficher la valeur.
3. Basculer atomiquement le connecteur, marquer la référence active et dater `last_rotated_at`.
4. Révoquer l'ancienne valeur côté provider puis marquer l'ancienne référence `REVOKED`.
5. Auditer références, acteur et résultat uniquement. En cas d'échec, revenir à la référence précédente et ouvrir un incident.

La sanitisation centrale masque password, token, API key, Authorization, cookie, secret et credential dans toute métadonnée. Les fichiers env sont `0600`, hors image et hors HTTP. Aucun chiffrement artisanal n'est autorisé.

## Contrat des connecteurs

Le domaine conserve des références stables (`qonto.production`, `prestashop.production`,
et `<provider>.production` pour le social). `EnvironmentSecretProvider.resolve()` est
l'unique frontière runtime : il traduit la référence vers le nom de variable injecté,
puis la valeur n'existe qu'en mémoire. Qonto et PrestaShop utilisent cette frontière.
Le flux ShopCaisse actif dans DrCloud OS est un inbox CSV local et n'a donc aucune
référence artificielle. Les anciens outils d'import supervisés résolvent leurs clés à
la frontière CLI et ne les persistent jamais.

Ajouter un connecteur exige une référence enregistrée sans valeur, un provider côté
serveur, trois états de santé (`CONNECTED`, `NOT_CONFIGURED`, `ERROR`), la rédaction
des erreurs et des tests prouvant l'absence de valeur dans HTTP, SQLite et AuditLog.
La rotation reste une opération du gestionnaire de secrets hors application : injecter
la nouvelle valeur, vérifier, basculer la référence, révoquer l'ancienne, puis auditer
uniquement référence, acteur et résultat.
