# Vocalis — guide pour Claude

Vocalis est une boîte à outils vocale pour Windows 11, écrite en Python (PySide6) :
dictée partout dans Windows, lecture à voix haute avec surlignage, traduction,
« Demander » (question à voix haute sur un texte, réponse lue) et Actions.
Dépôt public : `Galu83/Vocalis`, branche `main`.

## La personne

- Flavio (Flavio Alves), Key Account Manager chez Bechtle direct SA. **Pas développeur.**
- Parle français (aussi portugais du Portugal). **Toujours répondre en français**, simplement,
  sans jargon. Il dicte souvent ses messages : des mots peuvent être mal reconnus.
- Veut un logiciel « normal » et grand public : installateur, mises à jour par fenêtre,
  aucune manipulation technique. Tout réglage doit être faisable dans l'interface.
- **Il ne veut jamais intervenir dans les sessions de travail.** Une session lancée par
  une autre ne pose aucune question à Flavio (ni bouton, ni question en fin de rapport) :
  elle envoie ses décisions à trancher à la session qui l'a lancée, qui tranche ou lui
  transmet. Flavio ne décide que du produit (ce qu'il veut, ce qu'il entend, ce qu'il voit).
- Il teste vite et envoie des captures d'écran : lire son journal (`vocalis.log`) avant
  de conclure.
- Son PC : Windows 11, RTX 5070 (12 Go), clavier AZERTY, **Smart App Control activé**.
  **Ne jamais conseiller de désactiver Smart App Control** (impossible à réactiver
  sans réinstaller Windows).
- Voix préférées : Ariane (fr-CH, Edge) et Raquel (pt-PT, Edge). Le narrateur choisi ne
  doit jamais être remis à zéro.
- Mode test **personnel** : section `[test]` de son `config.toml`
  (`sans_carte_graphique`, `hors_ligne`), avec deux interrupteurs dans le panneau de
  l'icône. Rien de cela n'existe chez les autres utilisateurs.

## Organisation

| Fichier | Rôle |
|---|---|
| `vocalis/main.py` | Application : icône, raccourcis, dictée, Demander, mises à jour |
| `vocalis/dictation.py`, `streaming.py` | Session de dictée (aperçu en direct, texte final collé) |
| `vocalis/asr.py` / `parakeet.py` / `windows_stt.py` | Moteurs : Whisper (GPU), Parakeet (CPU), Windows (en ligne) |
| `vocalis/cleaner.py` | Correction du texte dicté (Ollama) |
| `vocalis/translator.py` | Traduction : Qwen local, puis Microsoft Edge, puis Google (en ligne) |
| `vocalis/reading.py`, `reading_controller.py`, `ui_reading.py` | Lecture à voix haute, surlignage, traduction par passage |
| `vocalis/tts.py`, `voices.py` | Voix Edge (en ligne), Piper, Windows ; secours hors ligne |
| `vocalis/uia_text.py`, `ocr.py`, `screenmap.py` | Texte des applications (UI Automation), OCR, coordonnées |
| `vocalis/assistant.py`, `ui_answer.py` | « Demander » : conversation Ollama en direct, carte de réponse lue |
| `vocalis/actions.py`, `ui_actions.py` | Actions (💬 répondre / ✏️ réécrire), mode avancé (prompt complet) |
| `vocalis/dictionary.py`, `learning.py`, `ui_learn.py` | Dictionnaire de dictée et de prononciation, apprentissage des corrections |
| `vocalis/langdetect.py`, `languages.py` | Détection de langue, catalogue des 8 langues |
| `vocalis/media.py` | Pause de la musique / des vidéos pendant que Vocalis parle |
| `vocalis/settings_ui.py` | Fenêtre Paramètres |
| `vocalis/updates.py`, `updater.py`, `ui_update.py` | Mises à jour depuis GitHub ; bibliothèques à jour au lancement |
| `vocalis/scripts/*.ps1` | Installation, OCR Windows, désinstallation |
| `installer/` | Installateur NSIS → `dist/Vocalis-Setup.exe` |
| `tests/` | Tests automatiques (pytest), non livrés aux utilisateurs |

Fichiers de l'utilisateur (jamais dans le dépôt, jamais écrasés par une mise à jour) :
`config.toml`, `dictionnaire.json`, `actions.json`, `modeles/`, `voix/`, `.venv/`.
Installation : `%LOCALAPPDATA%\Programs\Vocalis`.

## Publier une version (à chaque changement destiné à Flavio)

Accord permanent de Flavio (oct. 2026) : la session « Atelier » intègre et publie seule
quand les tests passent et qu'elle a vérifié le travail. Elle ne montre à Flavio que ce
qu'il doit voir ou entendre (captures, échantillons). Les sessions de travail, elles,
commitent sur leur propre branche et ne publient jamais : c'est Atelier qui intègre, et
qui attribue les numéros de version au moment de l'intégration.

1. Augmenter `__version__` dans `vocalis/__init__.py`.
2. Mettre à jour `version.json` : même numéro, `notes` courtes en français (« • … »).
   C'est ce texte qui s'affiche dans la fenêtre de mise à jour.
3. `sh installer/build.sh` (nécessite `makensis`, paquet `nsis`) pour régénérer
   `dist/Vocalis-Setup.exe`.
4. `python -m flake8 --select=F vocalis tests` (pyflakes) doit être propre et
   `python -m pytest` doit passer (voir « Tests automatiques »).
5. Commit sur `main` puis push. **Pas de tags** (le proxy les refuse ; inutiles).
   GitHub relance ces vérifications sur Windows à chaque push
   (`.github/workflows/tests.yml`).

La mise à jour lit le dernier commit via l'API GitHub (`commits/main`), puis
`version.json` et l'archive **de ce commit précis** : jamais de décalage de cache.
Une mise à jour ne remplace que `vocalis/`, `requirements*.txt` et `README.md` ;
une nouvelle bibliothèque dans `requirements.txt` s'installe au redémarrage suivant.

## Règles techniques

- Scripts PowerShell : **UTF-8 avec BOM et fins de ligne CRLF** (Windows PowerShell 5.1).
- Noms WinRT (`winrt-*`) : vérifier dans les fichiers `.pyi` des paquets, jamais de mémoire.
- Ollama : `hardware.ollama_options()` sur toutes les requêtes (mode test sans GPU).
  Qwen3/3.5 : `"think": False`.
- Qwen glisse parfois en chinois : `assistant._guarded` relance une fois avec une
  consigne stricte. Une action qui réécrit garde la langue **du texte**.
- Une traduction qui échoue ne doit jamais arrêter la lecture (passage lu tel quel).
- Les fenêtres de Vocalis sont exclues des captures (`winutil.exclude_from_capture`)
  sauf si `general.captures_visibles = true`.
- Textes de l'interface : français, tutoiement, phrases courtes ; jamais le mot « prompt »
  hors du mode avancé des Actions.
- Session locale (Claude Code sur le PC de Flavio, dossier OneDrive › Alves Flavio › Projet Développement › Vocalis) :
  on peut lancer Vocalis et lire `vocalis.log` directement. Session cloud (pas de Windows) :
  tester la logique en Python, et l'interface avec `QT_QPA_PLATFORM=offscreen`
  (capture `widget.grab().save(...)`) avant de livrer.

## Tests automatiques

- **Avant tout rapport à Flavio ou toute publication** :
  `python -m flake8 --select=F vocalis tests` puis `python -m pytest` (quelques secondes).
  Tout doit passer. (`flake8 --select=F` = les vérifications de pyflakes, en respectant
  les `# noqa` ; `pyflakes` seul signale deux faux positifs `app` dans `main.py` et
  `setup_models.py`.)
- **Tout nouveau comportement ajoute ou adapte un test** dans `tests/` ; un bug corrigé
  reçoit un test qui l'aurait détecté.
- Tests dans `tests/` à la racine, **jamais dans `vocalis/`** (remplacé par les mises à
  jour). pytest, pyflakes et flake8 sont dans `requirements-dev.txt`, **pas** dans
  `requirements.txt` (installé chez les utilisateurs).
- `tests/conftest.py` isole chaque test : config, dictionnaire, actions et voix dans un
  dossier temporaire (jamais la vraie configuration de Flavio), réseau coupé, pas de
  carte graphique. Ollama et GitHub sont simulés (`monkeypatch`) quand un test en a besoin.
- Sur le PC de Flavio, sans rien modifier dans son installation : installer les outils
  à part (`pip install --target <dossier> -r requirements-dev.txt`), puis lancer
  `%LOCALAPPDATA%\Programs\Vocalis\.venv\Scripts\python.exe -m pytest` avec
  `PYTHONPATH=<dossier>`.
- Non couvert : micro, haut-parleur, raccourcis clavier, UI Automation, OCR, vraies voix,
  vrai Ollama. Pour ça, Flavio teste à la main.

## Décisions déjà prises (ne pas revenir dessus sans demander)

- **Ordre de priorité de Flavio (oct. 2026), pour chaque fonction** : 1) sur le PC, sans
  Internet, sans carte graphique ; 2) en ligne, sans carte graphique ; 3) en dernier
  recours, sur le PC avec carte graphique. Sans renoncer à la qualité : on ne descend au
  niveau suivant que si la solution du niveau au-dessus n'est pas assez bonne. Le modèle
  sans carte graphique est donc le modèle **principal**, pas un secours.

- Pas de mode en ligne à clé (Groq…) : jugé pas assez simple. Sans GPU : petit modèle local.
- Traduction en dictée : pastilles de langue dans la fenêtre de dictée ; avec une
  sélection, parler la **remplace** (pas d'ajout) ; ✕ annule tout.
- Lecture : « Écouter en » Automatique ou une langue fixe, mémorisée ; badge « FR » quand
  le passage est lu tel quel, « EN→FR » seulement pendant un passage traduit.
- Clic = arrêter la lecture, double-clic = reprendre au mot (option).
- La fenêtre de dictée et la carte de réponse s'ouvrent près de l'endroit où l'on écrit.
- Raccourcis : Ctrl+Alt+Maj+K dictée, J lecture, L Demander, Échap arrêter.
- Licences : uniquement MIT, Apache 2.0 ou CC-BY (avec crédit à l'auteur), car Vocalis doit
  être utilisable en entreprise. Exclus (non commerciaux) : Qwen 2.5 3B (*Qwen Research*),
  XTTS, F5-TTS, Fish Speech. Vérifier la licence de tout nouveau modèle avant de l'ajouter.
- Services Microsoft gratuits non garantis : le traducteur Edge a renvoyé une erreur 404
  (oct. 2026, Google prend le relais) ; les voix Edge pourraient disparaître aussi.

## Prochaines étapes prévues

1. **Remplacer Qwen 2.5 3B** (licence *Qwen Research* : non commerciale) pour les PC sans
   GPU : comparer `qwen3.5:4b`, `qwen3.5:2b`, `gemma4` E2B/E4B (Apache 2.0) avec le mode test.
2. Essayer `qwen3.5:9b` à la place de `qwen2.5:7b` avec carte graphique.
3. Ajouter le crédit NVIDIA pour Parakeet (CC-BY 4.0).
4. Plus tard : Voxtral Realtime (dictée en continu), voix locale de qualité
   en secours des voix Edge : Kokoro-82M (Apache 2.0, processeur, mais vérifier
   pt-PT et allemand) ou Chatterbox Multilingual v3 (MIT, 25 langues, plutôt avec GPU).
5. Selon l'ordre de priorité : Parakeet (processeur) comme moteur de dictée par défaut,
   Whisper en option ; correction de la dictée aussi sans carte graphique si le modèle
   retenu à l'étape 1 est assez rapide ; voix locale de secours qui tourne sur le processeur.
6. Remettre la configuration normale de Flavio quand il a fini ses tests
   (supprimer `[test]`, `moteur = "whisper"`).
