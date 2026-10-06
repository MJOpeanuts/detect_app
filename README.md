# detect_app

`detect_app` est une brique autonome de détection du système global Nuts Vision, et non l’application de bureau globale. Son package Python est `detect_app`, sa distribution est `detect-app` et son titre de fenêtre est `detect_app`. L’interface reste en français.

## Installation et lancement

Prérequis : Windows 11 et Python 3.11 ou ultérieur.

- Depuis Windows, lancer `start_local.bat`. Le script crée le venv si nécessaire, vérifie l’installation même si le dossier du venv existe déjà et relance l’installation si elle est incomplète. Les erreurs et le code de sortie sont conservés. Une installation complète permet les démarrages suivants sans accès réseau.
- Depuis le dépôt, installation manuelle : `py -3.11 -m venv .venv`, puis `.\.venv\Scripts\python.exe -m pip install -e .`.
- Après activation du venv avec `.\.venv\Scripts\Activate.ps1`, lancer le module avec `python -m detect_app` ou la commande installée `detect-app`.

Le premier lancement d’une installation propre nécessite l’accès aux paquets déclarés dans `pyproject.toml`. L’application exécute l’inférence avec ONNX Runtime CPU et n’utilise pas le réseau pendant l’analyse.

## Stockage

Sous Windows, les données techniques sont stockées dans `%LOCALAPPDATA%\DataPeanuts\detect_app\` :

- `database\detect_app.sqlite3` : base SQLite ;
- `models\` : modèles ajoutés ou embarqués ;
- `configuration\models\` : mapping des classes ;
- `configuration\removed_bundled_models.json` : empreintes des modèles embarqués retirés par l’utilisateur (ils ne sont pas réajoutés au démarrage) ;
- `logs\` : journaux.

Par défaut, les images, annotations et découpes sont enregistrées dans le dossier Images Windows (résolu par son identifiant de dossier connu, y compris si son nom affiché est traduit), sous `detect_app\analyses\<job_id>\`. `DETECT_APP_IMAGES` permet de choisir un autre dossier. Les chemins d’images conservés dans la base sont relatifs à ce dossier.

Cette identité de stockage démarre sur une base neuve. Aucune migration ni import de l’ancien stockage NutsVision n’est effectué : son historique n’apparaîtra pas dans la nouvelle base. Les fichiers de l’ancien emplacement ne sont ni lus ni supprimés.

Les fichiers originaux sont copiés sans réencodage. L’orientation EXIF est appliquée pour l’inférence, les previews, les boîtes, les annotations et les découpes ; leurs coordonnées sont celles de l’image orientée. Une erreur de lecture ou une limite de décodage est rapportée comme telle, sans modifier l’original.

## Images haute résolution

La preview est décodée hors du thread graphique, orientée selon EXIF, réduite à 2048 pixels maximum sur le plus grand côté et convertie en `QImage`. Seul le thread UI crée le `QPixmap`. La scène utilise les dimensions orientées de référence ; les overlays enregistrés sont dessinés dans ce même référentiel. Sélectionner une boîte ne recrée pas le pixmap. Le bouton **100 %** vise un pixel source par pixel écran, mais une preview réduite ne restitue pas le détail absent ; le visualiseur l’indique.

Les formats effectivement gérés par Pillow sont BMP, JPEG, PNG, TIFF et WebP. Les limites explicites sont de 32 768 pixels par côté, 120 millions de pixels au total et 2 Gio de mémoire estimée par image (jusqu’à environ 15 octets par pixel plus 128 Mio réservés aux conversions, copies temporaires et allocations intermédiaires). Les avertissements de décompression Pillow sont traités localement après vérification des dimensions ; aucune protection globale n’est désactivée. La preview est bornée, mais le décodage d’un original peut nécessiter de la mémoire proportionnelle à ses dimensions. Les images dépassant ces limites sont refusées avec un diagnostic explicite.

Le modèle fourni `ic_detect_best.onnx` est inspecté avant utilisation : entrée float `[1, 3, 640, 640]`, sortie brute `[1, 7, 8400]`, format Ultralytics YOLO de détection avec NMS externe. Le prétraitement RGB utilise un letterbox gris et une normalisation par 255 ; le post-traitement applique un seuil de confiance de 0,25 et une NMS par classe au seuil IoU de 0,45, puis garde les 300 détections de confiance les plus élevées, toutes classes confondues.

Les noms sont lus dans les métadonnées du modèle, sans supposer un ordre de classes : `0=four_side`, `1=two_side`, `2=without_side`. Si un modèle n’intègre pas les noms, un mapping explicite est demandé. Seuls les exports Ultralytics statiques de détection brute à une entrée RGB sont acceptés ; les sorties end-to-end/NMS, signatures dynamiques, modèles multi-entrées et fichiers `.pt` sont refusés.

## Interface

La navigation principale comporte exactement trois onglets : **Analyse**, **Historique**, **Modèles**. Le pied de page « powered by » est affiché directement sur le fond de l’application (#181A1D), sans surface intermédiaire, dans les trois onglets et en plein écran (F11, Échap pour quitter).

### Analyse

- Rangée source : **Importer une image**, nom de l’image, indication de glisser-déposer ou message de refus local.
- Rangée paramètres : **Modèle**, **Confiance minimale** (25 % par défaut, de 0 à 100 %, valeur transmise à l’inférence) et **Analyser** à droite. Sans modèle compatible, Analyser est désactivé et un lien ouvre l’onglet Modèles.
- Rangée **Classement facultatif** : Client et PCBA, avec **Nouveau client…** et **Nouveau PCBA…**. Avec « Aucun client », la liste propose tous les PCBA (avec leur client ou « sans client ») ; choisir un client filtre les PCBA. Choisir un PCBA affiche son client sans jamais le réaffecter. Un client sans PCBA n’est pas enregistré : le job reste non classé.
- Import par dialogue ou par dépôt d’un fichier depuis l’Explorateur sur le visualiseur : les deux passent par la même validation (une seule image, fichier local, ni dossier ni URL distante, fichier lisible), puis par un décodage hors du thread graphique. Une image valide efface les résultats précédents ; un import refusé laisse l’image et le résultat en place. Aucun dépôt ne lance d’analyse. Pendant une analyse ou une fermeture différée, l’import et le dépôt sont refusés.
- Après réussite, le visualiseur affiche le fichier annoté enregistré (état « Image annotée »), la liste des objets et leurs découpes.

Stratégie d’affichage des annotations : le fichier `annotated.png` enregistré contient déjà les boîtes, classes et confiances ; le visualiseur n’y ajoute que la surbrillance de l’objet sélectionné, pour ne jamais dessiner deux fois les mêmes boîtes. Si le fichier annoté manque juste après une analyse, les boîtes sont reconstruites depuis les coordonnées enregistrées sur l’original, sans relancer l’inférence. L’original n’est jamais modifié.

### Historique

- Filtres **Client** (Tous / Sans client / client) et **PCBA** (Tous / Sans PCBA / PCBA cohérents avec le client), tri chronologique décroissant. Chaque entrée indique date, modèle, chemin de classement, état et nombre d’objets. « Sans PCBA » désigne un job non rattaché ; « Sans client » un job rattaché à un PCBA sans client.
- Sélecteur **Original / Annotée** au-dessus du visualiseur : Annotée par défaut si disponible, Original sans boîtes ni surbrillance. Les deux images restent en cache et le cadrage est conservé entre les modes. Un fichier manquant désactive le mode correspondant et affiche un avertissement local ; aucune inférence n’est relancée.
- **Ouvrir le dossier** et **Modifier le classement** : rattacher le job à un PCBA existant ou nouveau, retirer le lien, ou changer le client du PCBA (après confirmation, car cela reclasse toutes ses analyses). Le dossier du job reste identifié par `job_id`. Le classement d’un job en cours n’est pas modifiable. La suppression d’un PCBA ou d’un client encore lié est refusée avec une explication.

### Modèles

Liste des modèles à gauche ; à droite nom, identifiant, empreinte SHA-256 abrégée, nombre de classes, format d’entrée, mapping et chemin du fichier géré. **Ajouter un modèle ONNX** réutilise la validation de compatibilité, le mapping intégré ou demandé, l’empreinte et la copie dans le stockage géré, hors du thread graphique. Le nouveau modèle est aussitôt sélectionnable dans Analyse. **Retirer…** demande confirmation, supprime la copie gérée et son mapping, conserve le fichier source fourni ainsi que toutes les analyses ; un modèle utilisé par l’analyse en cours ne peut pas être retiré.

### Diagnostic

Chaque onglet possède un panneau **Diagnostic** repliable (chevron, fermé par défaut, accessible au clavier). Il contient les détails techniques sélectionnables, classés par catégorie (erreur, objet sélectionné, fichiers…). Le résumé d’une erreur reste visible hors du panneau.

## Schéma SQLite

Version de schéma : 2 (`PRAGMA user_version`). Les clés étrangères sont activées sur chaque connexion.

- `clients` : `id` (TEXT, UUID, clé primaire), `name` (TEXT, obligatoire, non vide).
- `pcbas` : `id` (TEXT, UUID, clé primaire), `name` (TEXT, obligatoire, non vide), `client_id` (TEXT, nullable, clé étrangère vers `clients.id`, indexée). Deux PCBA peuvent porter le même nom.
- `analysis_logs` : un job par analyse (image, modèle, état, chemins `image_path` et `annotated_image_path`), plus `pcba_id` (TEXT, nullable, clé étrangère vers `pcbas.id`, indexée). Le client d’un job est obtenu par son PCBA.
- `detected_objects` : objets détectés d’un job.

Les suppressions sont bloquées (`ON DELETE RESTRICT`) : supprimer un PCBA ou un client lié ne supprime jamais de job.

Mise à jour : au démarrage, une base existante en version 1 est sauvegardée (`detect_app.sqlite3.backup-v1-<horodatage>`, API de sauvegarde SQLite), puis mise à jour dans une transaction (création de `clients` et `pcbas`, ajout de `analysis_logs.pcba_id` et des index). Les analyses existantes sont conservées sans PCBA. Une base d’un schéma plus récent est refusée avec un message.

## Architecture

L’application garde une seule analyse active, le verrou d’instance et la récupération des jobs interrompus. Fermer pendant une analyse propose d’attendre sa terminaison sans bloquer l’interface.

- `ui/` : fenêtre PySide6, thème, visualiseur, panneau d’objets, panneau repliable, écran Modèles, dialogues de classement ;
- `services/` : orchestration des fichiers, modèles, inférence et persistance ; les originaux restent séparés des previews ;
- `vision/` : validation ONNX, décodage orienté, prétraitement, inférence et post-traitement, sans Qt ni SQLite ;
- `persistence/` : schéma SQLite, mise à jour contrôlée et dépôt.

`services/image_source.py` définit la frontière entre les sources d’images (import manuel ou matériel). Les ressources Data Peanuts partagées (`nuts-app.png`, `nuts-app.svg`, `nuts-app.ico`, `powered by_white.png`, `squirrel.svg`) gardent leurs noms et leur dessin.

Les métadonnées de licence du modèle fourni indiquent AGPL-3.0 ; les notices de ressources sont dans `THIRD_PARTY_NOTICES.md`.

## Tests

Tests rapides : `python -m unittest discover -s tests`.

Le test lourd de preview 12 000 × 9 000 pixels : `DETECT_APP_RUN_HIGHRES=1 python -m unittest discover -s tests -p test_highres.py -v` (PowerShell : `$env:DETECT_APP_RUN_HIGHRES = "1"; python -m unittest discover -s tests -p test_highres.py -v`). Ce test mesure les dimensions et le temps de chargement ; il vérifie le chemin technique, pas la qualité de reconnaissance. Une vraie analyse peut aussi être lancée avec le modèle fourni si les dépendances ONNX Runtime sont installées.
