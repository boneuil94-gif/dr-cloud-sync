# Valorisation du stock

La méthode V1 est FIFO sur lots `CONFIRMED`, ordonnés par date de réception puis identifiant. Les montants sont arrondis au centime (`ROUND_HALF_UP`). La quantité initiale est la quantité réellement reçue, y compris en réception partielle.

La valeur HT couvre seulement `min(stock physique, quantité restante des lots)`. Quantité non couverte, taux de couverture et fraîcheur sont exposés séparément. Stock négatif, données historiques absentes et devise non convertie ne reçoivent aucune valeur inventée. Le coût rendu est nullable; lorsqu'il existe il est ajouté au coût unitaire. V1 ne réévalue pas les lots et ne prétend pas produire un inventaire comptable certifié.
