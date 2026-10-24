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

Fichiers de l'utilisateur (jamais dans le dépôt, jamais écrasés par une mise à jour) :
`config.toml`, `dictionnaire.json`, `actions.json`, `modeles/`, `voix/`, `.venv/`.
Installation : `%LOCALAPPDATA%\Programs\Vocalis`.

## Publier une version (à chaque changement destiné à Flavio)

1. Augmenter `__version__` dans `vocalis/__init__.py`.
2. Mettre à jour `version.json` : même numéro, `notes` courtes en français (« • … »).
   C'est ce texte qui s'affiche dans la fenêtre de mise à jour.
3. `sh installer/build.sh` (nécessite `makensis`, paquet `nsis`) pour régénérer
   `dist/Vocalis-Setup.exe`.
4. `python3 -m pyflakes vocalis/*.py` doit être propre.
5. Commit sur `main` puis push. **Pas de tags** (le proxy les refuse ; inutiles).

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
- Pas de Windows ici : tester la logique en Python, et l'interface avec
  `QT_QPA_PLATFORM=offscreen` (capture `widget.grab().save(...)`) avant de livrer.

## Décisions déjà prises (ne pas revenir dessus sans demander)

- Pas de mode en ligne à clé (Groq…) : jugé pas assez simple. Sans GPU : petit modèle local.
- Traduction en dictée : pastilles de langue dans la fenêtre de dictée ; avec une
  sélection, parler la **remplace** (pas d'ajout) ; ✕ annule tout.
- Lecture : « Écouter en » Automatique ou une langue fixe, mémorisée ; badge « FR » quand
  le passage est lu tel quel, « EN→FR » seulement pendant un passage traduit.
- Clic = arrêter la lecture, double-clic = reprendre au mot (option).
- La fenêtre de dictée et la carte de réponse s'ouvrent près de l'endroit où l'on écrit.
- Raccourcis : Ctrl+Alt+Maj+K dictée, J lecture, L Demander, Échap arrêter.

## Prochaines étapes prévues

1. **Remplacer Qwen 2.5 3B** (licence *Qwen Research* : non commerciale) pour les PC sans
   GPU : comparer `qwen3.5:4b`, `qwen3.5:2b`, `gemma4` E2B/E4B (Apache 2.0) avec le mode test.
2. Essayer `qwen3.5:9b` à la place de `qwen2.5:7b` avec carte graphique.
3. Ajouter le crédit NVIDIA pour Parakeet (CC-BY 4.0).
4. Plus tard : Voxtral Realtime (dictée en continu), voix locale de qualité
   (Kokoro, Chatterbox) en secours des voix Edge.
5. Remettre la configuration normale de Flavio quand il a fini ses tests
   (supprimer `[test]`, `moteur = "whisper"`).
