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
- `logs\` : journaux.

Par défaut, les images, annotations et découpes sont enregistrées dans le dossier Images Windows (résolu par son identifiant de dossier connu, y compris si son nom affiché est traduit), sous `detect_app\analyses\<job_id>\`. `DETECT_APP_IMAGES` permet de choisir un autre dossier. Les chemins d’images conservés dans la base sont relatifs à ce dossier.

Cette identité de stockage démarre sur une base neuve. Aucune migration ni import de l’ancien stockage NutsVision n’est effectué : son historique n’apparaîtra pas dans la nouvelle base. Les fichiers de l’ancien emplacement ne sont ni lus ni supprimés.

Les fichiers originaux sont copiés sans réencodage. L’orientation EXIF est appliquée pour l’inférence, les previews, les boîtes, les annotations et les découpes ; leurs coordonnées sont celles de l’image orientée. Une erreur de lecture ou une limite de décodage est rapportée comme telle, sans modifier l’original.

## Images haute résolution

La preview est décodée hors du thread graphique, orientée selon EXIF, réduite à 2048 pixels maximum sur le plus grand côté et convertie en `QImage`. Seul le thread UI crée le `QPixmap`. La scène utilise les dimensions orientées de référence ; les overlays enregistrés sont dessinés dans ce même référentiel. Sélectionner une boîte ne recrée pas le pixmap. Le bouton **100 %** vise un pixel source par pixel écran, mais une preview réduite ne restitue pas le détail absent ; le visualiseur l’indique.

Les formats effectivement gérés par Pillow sont BMP, JPEG, PNG, TIFF et WebP. Les limites explicites sont de 32 768 pixels par côté, 120 millions de pixels au total et 2 Gio de mémoire estimée par image (jusqu’à environ 15 octets par pixel plus 128 Mio réservés aux conversions, copies temporaires et allocations intermédiaires). Les avertissements de décompression Pillow sont traités localement après vérification des dimensions ; aucune protection globale n’est désactivée. La preview est bornée, mais le décodage d’un original peut nécessiter de la mémoire proportionnelle à ses dimensions. Les images dépassant ces limites sont refusées avec un diagnostic explicite.

Le modèle fourni `ic_detect_best.onnx` est inspecté avant utilisation : entrée float `[1, 3, 640, 640]`, sortie brute `[1, 7, 8400]`, format Ultralytics YOLO de détection avec NMS externe. Le prétraitement RGB utilise un letterbox gris et une normalisation par 255 ; le post-traitement applique un seuil de confiance de 0,25 et une NMS par classe au seuil IoU de 0,45, puis garde les 300 détections de confiance les plus élevées, toutes classes confondues.

Les noms sont lus dans les métadonnées du modèle, sans supposer un ordre de classes : `0=four_side`, `1=two_side`, `2=without_side`. Si un modèle n’intègre pas les noms, un mapping explicite est demandé. Seuls les exports Ultralytics statiques de détection brute à une entrée RGB sont acceptés ; les sorties end-to-end/NMS, signatures dynamiques, modèles multi-entrées et fichiers `.pt` sont refusés.

## Analyse, historique et architecture

L’application conserve les écrans **Analyse** et **Historique**, une seule analyse active, le verrou d’instance et la récupération des jobs interrompus. Fermer pendant une analyse propose d’attendre sa terminaison sans bloquer l’interface.

- `ui/` : fenêtre PySide6, thème, visualiseur de preview et panneau d’objets ;
- `services/` : orchestration des fichiers, modèles, inférence et persistance ; les originaux restent séparés des previews ;
- `vision/` : validation ONNX, décodage orienté, prétraitement, inférence et post-traitement, sans Qt ni SQLite ;
- `persistence/` : deux tables métier SQLite (`analysis_logs` et `detected_objects`).

`services/image_source.py` définit la frontière entre les sources d’images (import manuel ou matériel). Les ressources Data Peanuts partagées (`nuts-app.png`, `nuts-app.svg`, `nuts-app.ico`, `powered by_white.png`, `squirrel.svg`) gardent leurs noms et leur dessin.

Les métadonnées de licence du modèle fourni indiquent AGPL-3.0 ; les notices de ressources sont dans `THIRD_PARTY_NOTICES.md`.

## Tests

Tests rapides : `python -m unittest discover -s tests`.

Le test lourd de preview 12 000 × 9 000 pixels : `DETECT_APP_RUN_HIGHRES=1 python -m unittest discover -s tests -p test_highres.py -v` (PowerShell : `$env:DETECT_APP_RUN_HIGHRES = "1"; python -m unittest discover -s tests -p test_highres.py -v`). Ce test mesure les dimensions et le temps de chargement ; il vérifie le chemin technique, pas la qualité de reconnaissance. Une vraie analyse peut aussi être lancée avec le modèle fourni si les dépendances ONNX Runtime sont installées.
