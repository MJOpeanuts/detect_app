# Nuts Vision Desktop

Application Windows locale en français pour importer une image, lancer une détection ONNX sur CPU, consulter les résultats et conserver l'historique SQLite. Aucune connexion réseau n'est utilisée pendant une analyse.

## Démarrage

Prérequis : Windows 11 et Python 3.11 ou ultérieur. Depuis le dépôt, exécuter `start_local.bat`. Le premier lancement crée un environnement virtuel et installe les dépendances Python ; cette installation initiale nécessite l'accès aux paquets PyPI. Les lancements et analyses suivants fonctionnent localement, hors ligne.

Pour un démarrage manuel :

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e .
.\.venv\Scripts\python.exe -m detect_app
```

## Modèles compatibles

Le fichier fourni `ic_detect_best.onnx` est inspecté avant utilisation : entrée float `[1, 3, 640, 640]`, sortie brute `[1, 7, 8400]`, format Ultralytics YOLO de détection avec NMS externe. Le prétraitement RGB utilise un letterbox gris et une normalisation par 255 ; le post-traitement applique un seuil de confiance de 0,25 et une NMS par classe au seuil IoU de 0,45.

Les noms sont lus à partir des métadonnées du fichier, sans supposer un ordre de classes : `0=four_side`, `1=two_side`, `2=without_side`. Lorsqu'un modèle n'a pas de noms de classes intégrés, l'application demande un mapping explicite dans l'ordre des identifiants.

L'import accepte uniquement les modèles ONNX dont la signature statique est celle d'un détecteur Ultralytics brut à une entrée float RGB, un tenseur de sortie de rang 3, des coordonnées XYWH suivies des scores de classes et sans NMS intégrée. Les sorties end-to-end/NMS, signatures dynamiques, modèles multi-entrées et autres formats sont refusés avec une explication ; les `.pt` ne sont pas chargés.

## Données locales

- Base unique : `%LOCALAPPDATA%\DataPeanuts\NutsVision\database\nuts_vision.sqlite3`
- Journaux et modèles : `%LOCALAPPDATA%\DataPeanuts\NutsVision\logs\` et `models\`
- Configuration de mapping : `%LOCALAPPDATA%\DataPeanuts\NutsVision\configuration\models\`
- Images, annotations et découpes : `Images\NutsVision\analyses\<job_id>\`

Le dossier des images peut être modifié avec la variable `NUTS_VISION_IMAGES`. Le modèle et sa configuration sont copiés dans les dossiers gérés par l'application ; son empreinte SHA-256 est enregistrée avec chaque analyse.

Les métadonnées du modèle fourni indiquent la licence AGPL-3.0.

La base contient exactement deux tables métier, `analysis_logs` et `detected_objects`. `completed_at` correspond à la fin du traitement aussi bien en cas de succès que d'erreur, sauf pour une analyse interrompue par un arrêt de l'application : son heure réelle de fin est inconnue et reste `NULL`. Une ligne d'analyse est créée pour chaque exécution, y compris pour la même image et le même modèle. Le lien objet-analyse utilise une clé étrangère `ON DELETE RESTRICT`.

Au démarrage, un verrou d'instance local est acquis avant tout accès à la base. Une seconde instance affiche un message puis quitte sans modifier celle-ci. Les analyses restées en état `processing` sont alors passées à `error` dans une transaction dédiée, avec un message d'interruption ; leurs images, annotations et découpes sont conservées pour le diagnostic.

## Analyse et historique

L'onglet **Analyse** affiche l'image d'entrée et son chemin, ainsi que les commandes d'analyse. L'onglet **Historique** permet uniquement de consulter les résultats : sélectionner un résultat ne change jamais l'image d'entrée et ne permet pas de lancer une analyse.

Si une analyse est en cours à la fermeture, l'application propose de rester ou de fermer après sa terminaison. L'inférence ONNX n'est pas annulée : l'interface reste réactive, les nouveaux lancements sont interdits et la fenêtre attend la fin du thread avant de se fermer.

## Architecture

- `ui/` : interface PySide6, exécution de l'analyse dans un `QThread`.
- `services/` : orchestration, catalogue de modèles et stockage par job.
- `vision/` : validation ONNX, prétraitement, inférence et post-traitement, sans dépendance à Qt ni à SQLite.
- `persistence/` : deux tables SQLAlchemy sur une base SQLite unique ; chaque opération ouvre sa propre session.
- `services/image_source.py` définit l'interface minimale d'une source d'image. Une future source Arducam pourra fournir une image sans contrôler l'interface, la base ou le moteur de vision.

## Tests

Depuis le dépôt : `python -m unittest discover -s tests`.
