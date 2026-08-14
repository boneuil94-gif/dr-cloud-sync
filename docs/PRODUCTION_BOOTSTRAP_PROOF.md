# Preuve production secrets / bootstrap

Le workflow manuel **DrCloud OS production bootstrap proof** inspecte le VPS avec
l'environment GitHub `production`. Il n'installe ni ne change aucun secret ou
compte. Il téléverse seulement `production_bootstrap_evidence.json`, après un
second contrôle strict de son schéma sensible.

Le contrôle échoue fermé si le fichier runtime n'est pas un fichier régulier en
`0600`, si une configuration obligatoire manque, si une valeur critique est
suivie par Git, si les deux services ou `/health` ne sont pas sains, si le commit
diffère, ou si l'administrateur bootstrap durable n'est pas unique, actif,
autorisé et protégé par le hash PBKDF2 attendu. La vérification du mot de passe
est locale et en lecture seule : aucun login HTTP et aucune session ne sont créés.

## Premier Game Day

1. Faire approuver et fusionner le mécanisme sur `main` sans modifier la roadmap.
2. Déclencher manuellement le workflow dans l'environment protégé `production`.
3. Vérifier le succès de **Enforce proof**, télécharger l'artefact et contrôler
   qu'il ne contient que des statuts et le commit déployé.
4. Conserver le run daté comme preuve d'audit. La clôture du P0 et tout rescoring
   doivent faire l'objet d'un changement ultérieur, après cette inspection.
