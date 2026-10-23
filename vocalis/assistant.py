"""« Demander » : une question (dite à voix haute ou choisie dans les boutons) sur
le texte sélectionné, et une réponse écrite au fur et à mesure par Qwen (Ollama),
sur ce PC. Qwen 7B avec une carte NVIDIA, Qwen 3B sur le processeur sinon."""
from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
import time
from pathlib import Path
from typing import Callable, Iterator

import requests

log = logging.getLogger(__name__)

CPU_MODEL = "qwen2.5:3b"
CPU_CONTEXT_CHARS = 6000          # sur le processeur, un long texte met du temps à être lu
GPU_CONTEXT_CHARS = 20000

# Boutons tout prêts : (clé, libellé, consigne)
PRESETS = [
    ("resumer", "Résumer", "Résume ce texte en quelques phrases."),
    ("expliquer", "Expliquer", "Explique ce texte simplement, comme à quelqu'un qui découvre "
                               "le sujet."),
    ("points", "Points clés", "Donne les points clés de ce texte, trois à cinq au plus."),
    ("repondre", "Répondre", "Rédige une réponse à ce message, prête à être envoyée, dans la "
                             "langue du message. Donne seulement la réponse."),
]
PRESET_PROMPTS = {k: p for k, _, p in PRESETS}

LANG_EN = {"fr": "French", "pt": "European Portuguese", "en": "English", "de": "German",
           "es": "Spanish", "it": "Italian", "nl": "Dutch", "pl": "Polish"}

SYSTEM = """You are the assistant built into Vocalis, a Windows voice tool. The user \
selected a text on screen (given between <text> tags, possibly empty) and asks a question \
about it, usually by voice, so the question may contain small transcription errors.
Rules:
- Answer in {lang}, unless the request explicitly asks for another language.
- Base your answer on the text. If the text does not contain the information, say so in \
one short sentence, then you may add what you know, saying clearly that it does not come \
from the text. Never invent facts about the text.
- Your answer is shown in a small window and READ ALOUD: plain text only, no Markdown, no \
asterisks, no headings, no tables, no emojis. For a list, write one short sentence per line.
- Be concise: about 120 words at most, unless the user asks for more detail.
- If there is no text, simply answer the question."""


def assistant_model(cfg: dict) -> str:
    from .hardware import has_nvidia_gpu
    chosen = (cfg.get("assistant", {}) or {}).get("modele", "")
    if chosen:
        return chosen
    return cfg["nettoyage"]["modele"] if has_nvidia_gpu() else CPU_MODEL


def context_limit(cfg: dict) -> int:
    from .hardware import has_nvidia_gpu
    return GPU_CONTEXT_CHARS if has_nvidia_gpu() else CPU_CONTEXT_CHARS


# =========================================================================== Ollama
def find_ollama() -> str | None:
    exe = shutil.which("ollama")
    if exe:
        return exe
    guess = Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Ollama" / "ollama.exe"
    return str(guess) if guess.exists() else None


def ollama_up(url: str) -> bool:
    try:
        requests.get(f"{url}/api/tags", timeout=2)
        return True
    except Exception:
        return False


def ensure_running(url: str, wait_s: int = 20) -> bool:
    """Démarre Ollama s'il est installé mais arrêté."""
    if ollama_up(url):
        return True
    exe = find_ollama()
    if not exe:
        return False
    subprocess.Popen([exe, "serve"], creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    for _ in range(wait_s):
        time.sleep(1)
        if ollama_up(url):
            return True
    return False


def model_present(url: str, model: str) -> bool:
    try:
        names = [m.get("name", "") for m in
                 requests.get(f"{url}/api/tags", timeout=3).json().get("models", [])]
    except Exception:
        return False
    return any(n == model or n == f"{model}:latest" or
               (":" not in model and n.split(":")[0] == model) for n in names)


class NotInstalled(RuntimeError):
    """L'assistant (Ollama ou son modèle) n'est pas encore installé."""


# =========================================================================== conversation
def clean_for_speech(text: str) -> str:
    """Retire la mise en forme Markdown qu'un modèle met parfois quand même."""
    text = re.sub(r"\*\*|__|`|^#+\s*", "", text, flags=re.M)
    text = re.sub(r"^\s*[-*•]\s+", "", text, flags=re.M)
    return text


class Conversation:
    """Une suite de questions sur le même texte (questions de suite comprises)."""

    def __init__(self, cfg: dict, context: str, lang: str):
        self.cfg = cfg
        self.url = cfg["nettoyage"]["url"].rstrip("/")
        self.model = assistant_model(cfg)
        self.context = context.strip()[:context_limit(cfg)]
        self.lang = lang or "fr"
        self.messages: list[dict] = []
        self._stop = False

    def _first_user(self, question: str) -> str:
        return f"<text>\n{self.context}\n</text>\n\nQuestion: {question}"

    def _system(self) -> dict:
        return {"role": "system",
                "content": SYSTEM.format(lang=LANG_EN.get(self.lang, "the user's language"))}

    def check(self) -> None:
        if not ensure_running(self.url):
            raise NotInstalled("Ollama n'est pas installé")
        if not model_present(self.url, self.model):
            raise NotInstalled(f"Modèle {self.model} absent")

    def prefill(self) -> None:
        """Pendant que l'on parle : charge le modèle et lit déjà le texte (Ollama garde
        ce début en mémoire, la vraie question repart de là)."""
        try:
            if not ollama_up(self.url) or not model_present(self.url, self.model):
                return
            body = {"model": self.model, "stream": False, "keep_alive": "30m",
                    "options": {"num_predict": 1, "temperature": 0.3,
                                "num_ctx": self._num_ctx()},
                    "messages": [self._system(),
                                 {"role": "user", "content": self._first_user("")}]}
            requests.post(f"{self.url}/api/chat", json=body, timeout=180)
        except Exception as exc:
            log.debug("Préchargement de l'assistant : %s", exc)

    def _num_ctx(self) -> int:
        return 4096 if len(self.context) < 8000 else 8192

    def stop(self) -> None:
        self._stop = True

    def ask(self, question: str) -> Iterator[str]:
        """Pose la question ; renvoie la réponse morceau par morceau."""
        self._stop = False
        first = not self.messages
        content = self._first_user(question) if first else question
        msgs = [self._system(), *self.messages, {"role": "user", "content": content}]
        body = {"model": self.model, "stream": True, "keep_alive": "30m",
                "options": {"temperature": 0.3, "num_ctx": self._num_ctx()},
                "messages": msgs}
        if "qwen3" in self.model:
            body["think"] = False
        answer = ""
        with requests.post(f"{self.url}/api/chat", json=body, stream=True,
                           timeout=(5, 300)) as r:
            r.raise_for_status()
            for line in r.iter_lines():
                if self._stop:
                    break
                if not line:
                    continue
                msg = json.loads(line)
                if "error" in msg:
                    raise RuntimeError(msg["error"])
                piece = msg.get("message", {}).get("content", "")
                if piece:
                    answer += piece
                    yield piece
                if msg.get("done"):
                    break
        self.messages += [{"role": "user", "content": content},
                          {"role": "assistant", "content": answer}]


def run_question(conv: Conversation, question: str, on_piece: Callable[[str], None]) -> str:
    out = ""
    for piece in conv.ask(question):
        out += piece
        on_piece(piece)
    return out
