# Rapprochement

Les autorités restent distinctes. Un rapprochement relie leurs identifiants sans créer de revenu ni de mouvement. Les états sont `MATCHED`, `POSSIBLE`, `UNMATCHED`, `CONFLICT`; seul un couple référence + montant + période déterministe devient automatiquement `MATCHED`. Un montant/date seul reste `POSSIBLE` et exige une validation humaine. Les confirmations sont protégées par authentification, permission, CSRF et AuditLog.
