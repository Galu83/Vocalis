"""Télécharge les modèles et vérifie l'installation (lancé par l'installateur :
python -m vocalis.setup_models [étapes])."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from vocalis.config import MODELS_DIR, load_config  # noqa: E402

CFG = load_config()
OK, KO, INFO = "[OK] ", "[!!] ", "     "


problems: list[str] = []
warnings: list[str] = []


def title(text: str) -> None:
    print(f"\n=== {text} ===")


def download(url: str, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".part")
    with urllib.request.urlopen(url, timeout=60) as r, open(tmp, "wb") as f:
        total = int(r.headers.get("Content-Length") or 0)
        done = 0
        while chunk := r.read(1 << 20):
            f.write(chunk)
            done += len(chunk)
            if total:
                print(f"\r{INFO}{dest.name} : {done * 100 // total} %", end="", flush=True)
    print()
    tmp.replace(dest)


# --------------------------------------------------------------------------- Whisper
def setup_parakeet() -> None:
    title("Reconnaissance vocale (Parakeet, sur le processeur)")
    from vocalis import parakeet
    try:
        parakeet.download(lambda pct: print(f"\r{INFO}Parakeet : {pct} %", end="", flush=True))
        print()
        import numpy as np
        t = time.time()
        asr = parakeet.ParakeetAsr(CFG)
        asr.transcribe_final(np.zeros(16000, dtype=np.float32))
        print(f"{OK}Parakeet prêt ({time.time() - t:.0f} s)")
    except Exception as exc:
        problems.append(f"Parakeet : {exc}")
        print(f"{KO}Parakeet : {exc}")


def setup_whisper() -> None:
    from vocalis.hardware import has_nvidia_gpu, resolve_engine
    engine = resolve_engine(CFG)
    if engine == "parakeet":
        setup_parakeet()
        return
    if engine == "windows":
        title("Reconnaissance vocale (Windows, en ligne)")
        print(f"{INFO}Rien à télécharger")
        return
    title("Reconnaissance vocale (Whisper)")
    from vocalis.asr import Asr
    d = CFG["dictee"]
    if not has_nvidia_gpu() and d["appareil"] == "cuda":
        warnings.append("Pas de carte NVIDIA : Whisper sera lent, le moteur Parakeet est "
                        "conseillé")
        print(f"{KO}Pas de carte NVIDIA (pilote 570+) : modèle léger sur le processeur")
        d = dict(d, modele_whisper=d.get("modele_secours_cpu", "small"), appareil="cpu",
                 type_calcul="int8")
        cfg_local = dict(CFG, dictee=d)
    else:
        cfg_local = CFG
    print(f"{INFO}Téléchargement / chargement de {d['modele_whisper']} (environ 1,6 Go)…")
    t = time.time()
    try:
        asr = Asr(cfg_local)
    except Exception as exc:
        problems.append(f"Whisper : {exc}")
        print(f"{KO}Whisper n'a pas pu se charger : {exc}")
        return
    if asr.device == "cuda":
        print(f"{OK}Whisper fonctionne sur la carte graphique ({time.time() - t:.0f} s)")
    else:
        warnings.append("Whisper tourne sur le processeur (carte graphique non utilisée)")
        print(f"{KO}Carte graphique non utilisée : {asr.gpu_error[:300]}")
        print(f"{INFO}Vérifie que le pilote NVIDIA est à jour (version 570 ou plus récente).")
        print(f"{INFO}Modèle de secours utilisé : {asr.model_name}")


# --------------------------------------------------------------------------- voix
def setup_voices() -> None:
    title("Voix de lecture (Piper)")
    from vocalis import voices as V
    from vocalis.languages import active
    for lang in [l for l in active(CFG) if l in V.DEFAULT]:
        v = V.from_config(CFG["lecture"].get(f"voix_{lang}", ""), lang)
        try:
            V.download(v, lambda pct: print(f"\r{INFO}{v.name} : {pct} %", end="", flush=True))
            print(f"\r{OK}{v.name} ({v.accent})          ")
        except Exception as exc:
            problems.append(f"Voix {v.name} : {exc}")
            print(f"{KO}{v.name} : {exc}")
    try:
        from vocalis.tts import Tts
        speech = Tts(CFG).synth("Bonjour, ceci est un essai.", "fr", 1.0)
        aligned = "avec" if speech.word_bounds else "sans"
        print(f"{OK}Synthèse vocale testée ({aligned} alignement mot à mot)")
    except Exception as exc:
        problems.append(f"Synthèse vocale : {exc}")
        print(f"{KO}Synthèse vocale : {exc}")


# --------------------------------------------------------------------------- OCR
def check_ocr() -> None:
    title("Reconnaissance de texte à l'écran (OCR Windows)")
    from vocalis import ocr
    langs = ocr.available_languages()
    print(f"{INFO}Langues OCR installées : {', '.join(langs) or 'aucune'}")
    from vocalis.languages import active, lower_name
    for prefix, name in ((l, lower_name(l)) for l in active(CFG)):
        if any(l.lower().startswith(prefix) for l in langs):
            print(f"{OK}OCR {name}")
        else:
            warnings.append(f"OCR {name} absent : Paramètres > État > Réparer")
            print(f"{KO}OCR {name} absent : Paramètres > État > Réparer")


# --------------------------------------------------------------------------- Ollama
def find_ollama() -> str | None:
    exe = shutil.which("ollama")
    if exe:
        return exe
    guess = Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Ollama" / "ollama.exe"
    return str(guess) if guess.exists() else None


def ollama_up(url: str) -> bool:
    try:
        urllib.request.urlopen(f"{url}/api/tags", timeout=3)
        return True
    except Exception:
        return False


def setup_ollama() -> None:
    title("Nettoyage du texte (Ollama)")
    c = CFG["nettoyage"]
    if not c["actif"]:
        print(f"{INFO}Nettoyage désactivé dans config.toml")
        return
    url = c["url"].rstrip("/")
    if not ollama_up(url):
        exe = find_ollama()
        if not exe:
            problems.append("Ollama n'est pas installé")
            print(f"{KO}Ollama introuvable. Installe-le depuis https://ollama.com/download "
                  "puis relance l'installation de Vocalis")
            return
        print(f"{INFO}Démarrage d'Ollama…")
        subprocess.Popen([exe, "serve"], creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        for _ in range(30):
            time.sleep(1)
            if ollama_up(url):
                break
    if not ollama_up(url):
        problems.append("Ollama ne répond pas")
        print(f"{KO}Ollama ne répond pas sur {url}")
        return
    model = c["modele"]
    print(f"{INFO}Téléchargement du modèle {model} (environ 4,7 Go, une seule fois)…")
    req = urllib.request.Request(f"{url}/api/pull", data=json.dumps({"model": model}).encode(),
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=3600) as r:
            last = ""
            for line in r:
                msg = json.loads(line)
                if "error" in msg:
                    raise RuntimeError(msg["error"])
                status = msg.get("status", "")
                if msg.get("total"):
                    pct = msg.get("completed", 0) * 100 // msg["total"]
                    print(f"\r{INFO}{status[:40]} : {pct} %   ", end="", flush=True)
                elif status != last:
                    print(f"\r{INFO}{status:<60}")
                last = status
        print()
    except Exception as exc:
        problems.append(f"Modèle {model} : {exc}")
        print(f"{KO}Téléchargement de {model} impossible : {exc}")
        return
    from vocalis.cleaner import Cleaner
    cleaner = Cleaner(CFG)
    # 1. Chargement du modèle en mémoire (la première fois, cela peut être long).
    print(f"{INFO}Chargement du modèle en mémoire…")
    t = time.time()
    cleaner.warmup()
    print(f"{INFO}Modèle chargé en {time.time() - t:.1f} s")
    # 2. Où tourne-t-il ? (carte graphique ou processeur)
    try:
        with urllib.request.urlopen(f"{url}/api/ps", timeout=5) as r:
            running = json.loads(r.read()).get("models", [])
        info = next((m for m in running if m.get("name", "").startswith(model.split(":")[0])),
                    None)
        if info:
            vram, total = info.get("size_vram", 0), info.get("size", 0) or 1
            share = vram * 100 // total
            if share >= 90:
                print(f"{OK}Le modèle tourne sur la carte graphique ({vram / 1e9:.1f} Go)")
            else:
                warnings.append(f"Nettoyage : seulement {share} % du modèle sur la carte "
                                "graphique, il sera lent")
                print(f"{KO}Seulement {share} % du modèle sur la carte graphique : "
                      "le nettoyage sera lent")
    except Exception as exc:
        print(f"{INFO}Impossible de vérifier où tourne le modèle : {exc}")
    # 3. Vrai test, modèle déjà chargé : c'est le temps que tu auras en dictée.
    raw = "euh bonjour euh je voulais je voulais te dire que ça marche bien"
    t = time.time()
    out = cleaner.clean(raw, "fr")
    took = time.time() - t
    if out.strip() == raw:
        warnings.append("Le test de nettoyage n'a rien corrigé (délai dépassé ?)")
        print(f"{KO}Test de nettoyage sans effet ({took:.1f} s) : {out}")
    else:
        print(f"{OK}Test de nettoyage ({took:.1f} s) : {out}")


# --------------------------------------------------------------------------- micro
def check_mic() -> None:
    title("Micro")
    try:
        import sounddevice as sd
        dev = sd.query_devices(kind="input")
        print(f"{OK}Micro par défaut : {dev['name']}")
    except Exception as exc:
        problems.append(f"Micro : {exc}")
        print(f"{KO}Aucun micro détecté : {exc}")


# --------------------------------------------------------------------------- icône
def make_icon_file() -> None:
    title("Icône")
    from PySide6.QtGui import QGuiApplication

    from vocalis.config import ICON_PATH
    from vocalis.icon import save_ico
    app = QGuiApplication.instance() or QGuiApplication([])  # noqa: F841
    if save_ico(ICON_PATH):
        print(f"{OK}{ICON_PATH.name}")
    else:
        warnings.append("Icône non créée")
        print(f"{KO}Icône non créée")


def verify_ocr() -> None:
    """Code de sortie 0 si l'OCR français et portugais sont installés."""
    from vocalis import ocr
    langs = [l.lower() for l in ocr.available_languages()]
    from vocalis.languages import active
    ok = all(any(l.startswith(p) for l in langs) for p in active(CFG))
    sys.exit(0 if ok else 1)


def main() -> None:
    MODELS_DIR.mkdir(exist_ok=True)
    only = sys.argv[1:]
    if only == ["verifier_ocr"]:
        verify_ocr()
    steps = {"whisper": setup_whisper, "voix": setup_voices, "ocr": check_ocr,
             "ollama": setup_ollama, "micro": check_mic, "icone": make_icon_file}
    for name, fn in steps.items():
        if not only or name in only:
            try:
                fn()
            except Exception as exc:
                problems.append(f"{name} : {exc}")
                print(f"{KO}{name} : {exc}")
    title("Résumé")
    if problems or warnings:
        print("Points à régler :")
        for p in problems + warnings:
            print(f"  - {p}")
    else:
        print("Tout est prêt.")
    # 0 = tout va bien, 2 = avertissements, 1 = erreur
    sys.exit(1 if problems else 2 if warnings else 0)


if __name__ == "__main__":
    main()
