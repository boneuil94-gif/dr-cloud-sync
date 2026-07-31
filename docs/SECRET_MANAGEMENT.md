# Gestion des secrets

Les tables métier ne stockent que des références opaques. `secret_references` conserve provider, usage, état et dates, jamais la valeur. Les valeurs PrestaShop, Qonto, ShopCaisse et futurs providers sociaux sont injectées au runtime par variables protégées/SecretProvider et ne doivent être ni renvoyées, ni journalisées, ni incluses aux artifacts.

## Rotation

1. Créer la nouvelle valeur dans le gestionnaire runtime et une nouvelle référence opaque.
2. Vérifier la connexion sans afficher la valeur.
3. Basculer atomiquement le connecteur, marquer la référence active et dater `last_rotated_at`.
4. Révoquer l'ancienne valeur côté provider puis marquer l'ancienne référence `REVOKED`.
5. Auditer références, acteur et résultat uniquement. En cas d'échec, revenir à la référence précédente et ouvrir un incident.

La sanitisation centrale masque password, token, API key, Authorization, cookie, secret et credential dans toute métadonnée. Les fichiers env sont `0600`, hors image et hors HTTP. Aucun chiffrement artisanal n'est autorisé.
