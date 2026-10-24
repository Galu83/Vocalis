"""Lecture et écriture de config.toml, avec valeurs par défaut."""
from __future__ import annotations

import copy
import json
import logging
import os
import tomllib
from pathlib import Path

log = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = ROOT / "config.toml"
MODELS_DIR = ROOT / "modeles"
LOG_PATH = ROOT / "vocalis.log"
ICON_PATH = ROOT / "vocalis.ico"

DEFAULTS: dict = {
    "raccourcis": {
        "dictee_bascule": "ctrl+alt+shift+k",
        "lecture": "ctrl+alt+shift+j",
        "arreter_lecture": "esc",
        "demander": "ctrl+alt+shift+l",
    },
    "dictee": {
        "langues": ["fr", "pt", "en", "de"],
        "langue_forcee": "",
        "langue_preferee": "fr",
        "moteur": "auto",
        "modele_whisper": "large-v3-turbo",
        "appareil": "cuda",
        "type_calcul": "float16",
        "modele_secours_cpu": "small",
        "ecrire_en_direct": True,
        "remplacer_par_version_nettoyee": True,
        "intervalle_secondes": 0.4,
        "micro": "",
        "vocabulaire": "",
    },
    "nettoyage": {
        "actif": True,
        "modele": "qwen2.5:7b",
        "url": "http://127.0.0.1:11434",
        "delai_max_secondes": 25,
    },
    "lecture": {
        "vitesse": 1.0,
        "pause_phrases": 0.2,
        "langue_ecoute": "auto",
        "clic_arrete": True,
        "pause_medias": True,
        "voix_fr": "piper:fr_FR-siwis-medium",
        "voix_pt": "piper:pt_PT-tugao-medium",
        "voix_en": "edge:en-US-AvaMultilingualNeural",
        "voix_de": "edge:de-DE-SeraphinaMultilingualNeural",
        "couleur_surlignage": "#FFE600",
        "opacite_surlignage": 0.45,
        "taille_police_fenetre": 17,
    },
    "assistant": {
        "modele": "",
    },
    "general": {
        "mises_a_jour_auto": True,
        "captures_visibles": False,
    },
    "interface": {
        "pastille_x": -1,       # position mémorisée de la pastille (-1 = en bas, centrée)
        "pastille_y": -1,
        "menu_classique": False,
    },
}

COMMENTS = {
    ("raccourcis", "dictee_bascule"): "un appui pour commencer, un appui pour finir",
    ("interface", "pastille_x"): "plus utilisé : la pastille s'ouvre près de l'endroit où l'on écrit",
    ("general", "mises_a_jour_auto"): "proposer les nouvelles versions au démarrage",
    ("general", "captures_visibles"): "true = les fenêtres de Vocalis apparaissent sur les captures d'écran",
    ("interface", "menu_classique"): "true = ancien menu de l'icône au lieu du panneau",
    ("raccourcis", "lecture"): "lire la sélection, ou tracer une zone de l'écran",
    ("raccourcis", "arreter_lecture"): "arrêter la lecture",
    ("raccourcis", "demander"): "question à voix haute sur le texte sélectionné",
    ("assistant", "modele"): "vide = automatique (qwen2.5:7b avec carte NVIDIA, qwen2.5:3b sinon)",
    ("dictee", "langues"): "langues autorisées (détection automatique parmi elles)",
    ("dictee", "langue_forcee"): "réglage avancé : impose une langue (vide = non)",
    ("dictee", "moteur"): "auto, whisper (carte graphique), parakeet (processeur) ou windows (en ligne)",
    ("dictee", "langue_preferee"): "langue de la pastille (cliquer dessus pour changer)",
    ("dictee", "appareil"): "cuda = carte graphique, cpu = processeur",
    ("dictee", "micro"): "vide = micro par défaut",
    ("dictee", "intervalle_secondes"): "mise à jour du texte en direct",
    ("dictee", "vocabulaire"): "noms propres ou mots techniques, séparés par des virgules",
    ("nettoyage", "modele"): "modèle Ollama",
    ("lecture", "vitesse"): "0.5 à 2.5",
    ("lecture", "pause_medias"): "mettre en pause musique et vidéos pendant que Vocalis parle",
    ("lecture", "clic_arrete"): "essai : clic = arrêter, double-clic = reprendre au mot",
    ("lecture", "langue_ecoute"): "auto = langue du texte ; fr, pt, en ou de = traduit si besoin",
    ("lecture", "pause_phrases"): "pause après un point, en secondes (à vitesse normale)",
    ("lecture", "voix_fr"): "à choisir dans Paramètres > Lecture",
    ("lecture", "voix_pt"): "à choisir dans Paramètres > Lecture",
    ("lecture", "opacite_surlignage"): "0 = invisible, 1 = opaque",
}


def _merge(base: dict, extra: dict) -> dict:
    out = copy.deepcopy(base)
    for key, value in extra.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _merge(out[key], value)
        else:
            out[key] = value
    return out


def load_config(path: Path = CONFIG_PATH) -> dict:
    cfg = copy.deepcopy(DEFAULTS)
    if path.exists():
        try:
            text = path.read_bytes().decode("utf-8-sig")   # tolère un BOM
            cfg = _merge(cfg, tomllib.loads(text))
            # Anciennes versions : deux touches de lecture, remplacées par une seule.
            for old in ("lecture_zone", "lecture_selection", "dictee_maintenir"):
                cfg["raccourcis"].pop(old, None)
            cfg["dictee"].pop("pause_phrase_secondes", None)
            cfg.pop("moteur", None)
            cfg["raccourcis"].pop("dictee_traduite", None)
            for old in ("traduire_vers", "derniere_traduction", "langue_du_document"):
                cfg["dictee"].pop(old, None)
        except Exception:  # fichier mal formé : on garde les valeurs par défaut
            log.exception("config.toml illisible, valeurs par défaut utilisées")
    else:
        log.warning("config.toml introuvable, valeurs par défaut utilisées")
    return cfg


def _fmt(value) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return repr(round(value, 3) if isinstance(value, float) else value)
    if isinstance(value, (list, tuple)):
        return "[" + ", ".join(_fmt(v) for v in value) + "]"
    return json.dumps(str(value), ensure_ascii=False)


def save_config(cfg: dict, path: Path = CONFIG_PATH) -> None:
    lines = [
        "# ============================================================",
        "#  Vocalis - configuration",
        "#  Modifiable avec la fenêtre Paramètres (clic droit sur l'icône)",
        "#  ou à la main, puis clic droit > Redémarrer.",
        "# ============================================================",
        "",
    ]
    for section, values in cfg.items():
        if not isinstance(values, dict):
            continue
        lines.append(f"[{section}]")
        for key, value in values.items():
            line = f"{key} = {_fmt(value)}"
            comment = COMMENTS.get((section, key))
            if comment:
                line = f"{line:<40} # {comment}"
            lines.append(line)
        lines.append("")
    tmp = path.with_suffix(".tmp")
    tmp.write_text("\n".join(lines), encoding="utf-8")
    tomllib.loads(tmp.read_text(encoding="utf-8"))   # vérifie avant de remplacer
    os.replace(tmp, path)


def update_settings(section: str, values: dict, path: Path = CONFIG_PATH) -> None:
    """Modifie quelques réglages du fichier sans toucher aux autres."""
    try:
        cfg = load_config(path)
        cfg.setdefault(section, {}).update(values)
        save_config(cfg, path)
    except Exception:
        log.exception("Réglage non enregistré : %s", values)


def resolve(path_str: str) -> Path:
    """Chemin relatif au dossier de l'application."""
    p = Path(path_str)
    return p if p.is_absolute() else ROOT / p


_KEY_NAMES = {
    "ctrl": "Ctrl", "shift": "Maj", "alt": "Alt", "alt gr": "AltGr", "windows": "Win",
    "esc": "Échap", "space": "Espace", "enter": "Entrée", "tab": "Tab",
    "page up": "Page préc.", "page down": "Page suiv.", "insert": "Inser",
    "delete": "Suppr", "home": "Début", "end": "Fin", "pause": "Pause",
    "backspace": "Retour arr.", "scroll lock": "Arrêt défil", "caps lock": "Verr. maj",
    "print screen": "Impr. écran", "up": "↑", "down": "↓", "left": "←", "right": "→",
    "menu": "Menu",
}


def key_label(hotkey: str) -> str:
    """'ctrl+shift+f8' -> 'Ctrl + Maj + F8'."""
    if not hotkey:
        return "—"
    parts = [p.strip() for p in hotkey.lower().split("+") if p.strip()]
    return " + ".join(_KEY_NAMES.get(p, p.upper()) for p in parts)
