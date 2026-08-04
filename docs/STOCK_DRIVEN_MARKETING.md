# Stock-driven Marketing

Le moteur déterministe lit exclusivement la position du stock local et les ventes
persistées. Les seuils configurables sont : faible 10, surstock 80, rupture
imminente 3, dormance 30 jours, minimum 5 et cible 40. Il produit des opportunités
explicables (faible, surstock, dormant, rupture imminente) idempotentes par jour.
Les données absentes rendent les preuves `PARTIAL`; elles ne deviennent jamais zéro.
Toute proposition est `READY_FOR_REVIEW`, expire après sept jours et ne publie rien.
