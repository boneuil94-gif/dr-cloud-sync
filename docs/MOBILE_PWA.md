# DrCloud OS sur mobile

DrCloud OS reste une application web progressive : aucune application Android ou
iOS distincte n'est nécessaire. Une session installée utilise les mêmes écrans de
connexion, cookies de session, protections CSRF et action de déconnexion que le
navigateur. L'application installée démarre à la racine `/`, dans le périmètre `/`.

## Installation

### Android (Chrome ou Samsung Internet)

1. Ouvrir l'URL HTTPS de DrCloud OS et se connecter.
2. Ouvrir le menu du navigateur.
3. Choisir **Installer l'application** ou **Ajouter à l'écran d'accueil**.
4. Lancer **DrCloud OS** depuis l'icône ajoutée.

### iPhone (Safari)

1. Ouvrir l'URL HTTPS de DrCloud OS dans Safari et se connecter.
2. Toucher **Partager**.
3. Choisir **Sur l'écran d'accueil**, puis confirmer.

DrCloud OS n'est pas publié sur le Play Store ni sur l'App Store.

## Scan EAN

Le champ EAN reste compatible avec une douchette clavier et avec la saisie
manuelle. Le bouton **Scanner** utilise `BarcodeDetector` et demande l'accès à la
caméra uniquement après un appui. Le flux est arrêté après une lecture, à la
fermeture de la caméra et au départ de la page.

Le scan caméra dépend du support de `BarcodeDetector`, de `getUserMedia` et d'un
contexte HTTPS par le navigateur. Si l'une de ces conditions manque, ou si la
permission est refusée, l'interface conserve le champ de saisie manuelle. Aucun
produit n'est créé quand un EAN est inconnu.

## Cache et icône

Aucun service worker n'est enregistré : les API métier et les pages authentifiées
ne sont donc pas placées dans un cache hors ligne. Le manifest utilise le PNG
officiel existant comme icône. Une future déclinaison 192 × 192 et 512 × 512
`maskable`, validée par l'équipe de marque, améliorera le rendu sur certains
lanceurs ; elle ne doit pas être recréée approximativement depuis le SVG actuel.
