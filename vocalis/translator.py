"""Traduction : Qwen sur ce PC (Ollama) quand il est là, sinon le traducteur en ligne
gratuit de Microsoft Edge (même principe que les voix Ariane et Raquel)."""
from __future__ import annotations

import logging
import threading
import time

import requests

from .hardware import offline, ollama_options

log = logging.getLogger(__name__)

from .languages import LANGS  # noqa: E402

NAMES = {c: l.name.lower() for c, l in LANGS.items()}
EDGE_CODES = {c: l.edge_translate for c, l in LANGS.items()}

# Consigne en anglais (suivie plus fidèlement par Qwen), et ne parlant QUE de la langue
# d'arrivée : une règle sur le portugais faisait traduire en portugais au lieu du
# français.
ENGLISH = {c: l.english for c, l in LANGS.items()}
PROMPT = """You are a professional translator. Translate the text between <text> tags \
into {target}.
- The output language MUST be {target}, whatever the language of the input.
- Faithful, natural and idiomatic; keep the tone, punctuation and paragraphs.
- Keep names, addresses, numbers and links unchanged.{extra}
- Reply with the {target} translation only: no tags, no quotes, no comments."""
EXTRA = {c: ("\n- " + l.translate_rule if l.translate_rule else "") for c, l in LANGS.items()}


class Translator:
    def __init__(self, cfg: dict):
        c = cfg["nettoyage"]
        self.model = c["modele"]
        from .hardware import has_nvidia_gpu
        if not has_nvidia_gpu():
            # Sans carte graphique : le petit modèle de l'assistant (installé pour
            # « Demander ») traduit aussi, sans Internet.
            from .assistant import assistant_model
            self.model = assistant_model(cfg)
        self.url = c["url"].rstrip("/")
        self._cache: dict[tuple[str, str], str] = {}
        self._lock = threading.Lock()
        self._ollama_ok: tuple[float, bool] = (0.0, False)
        self._edge_token: tuple[float, str] = (0.0, "")

    # ------------------------------------------------------------------ moteurs
    def local_available(self) -> bool:
        t, ok = self._ollama_ok
        if time.monotonic() - t < 60:
            return ok
        try:
            r = requests.get(f"{self.url}/api/tags", timeout=1.5)
            names = [m.get("name", "") for m in r.json().get("models", [])]
            ok = any(n == self.model or n.split(":")[0] == self.model or
                     n == f"{self.model}:latest" for n in names)
        except Exception:
            ok = False
        self._ollama_ok = (time.monotonic(), ok)
        return ok

    def _local(self, text: str, target: str, strict: bool = False) -> str:
        name = ENGLISH[target]
        user = f"<text>\n{text}\n</text>"
        if strict:
            user = f"Translate into {name} (and only {name}):\n" + user
        body = {
            "model": self.model, "stream": False, "keep_alive": "30m",
            "options": ollama_options({"temperature": 0,
                                       "num_ctx": 4096 if len(text) < 5000 else 8192}),
            "messages": [
                {"role": "system", "content": PROMPT.format(target=name,
                                                            extra=EXTRA.get(target, ""))},
                {"role": "user", "content": user},
            ],
        }
        r = requests.post(f"{self.url}/api/chat", json=body, timeout=20 + len(text) / 20)
        r.raise_for_status()
        out = r.json()["message"]["content"].strip()
        for tag in ("<text>", "</text>", "<texte>", "</texte>"):
            out = out.replace(tag, "")
        out = out.strip()
        if not out:
            raise RuntimeError("traduction vide")
        return out

    def _edge(self, text: str, target: str) -> str:
        if offline():
            raise RuntimeError("pas d'Internet (mode test hors ligne)")
        t, token = self._edge_token
        if not token or time.monotonic() - t > 8 * 60:
            r = requests.get("https://edge.microsoft.com/translate/auth", timeout=8)
            r.raise_for_status()
            token = r.text.strip()
            self._edge_token = (time.monotonic(), token)
        r = requests.post(
            "https://api-edge.cognitive.microsofttranslator.com/translate",
            params={"api-version": "3.0", "to": EDGE_CODES[target]},
            headers={"Authorization": f"Bearer {token}",
                     "Content-Type": "application/json"},
            json=[{"Text": text}], timeout=15)
        r.raise_for_status()
        return r.json()[0]["translations"][0]["text"].strip()

    @staticmethod
    def _is_language(text: str, target: str) -> bool:
        """La traduction est-elle bien dans la langue demandée ? (textes assez longs)"""
        from . import langdetect
        if len(text.split()) < 6:
            return True
        return langdetect.detect(text, target, tuple(LANGS)) == target

    # ------------------------------------------------------------------ API
    def translate(self, text: str, target: str) -> str:
        text = text.strip()
        if not text or target not in NAMES:
            return text
        key = (text, target)
        with self._lock:
            if key in self._cache:
                return self._cache[key]
        t0 = time.monotonic()
        out = ""
        if self.local_available():
            try:
                out = self._local(text, target)
                if not self._is_language(out, target):
                    log.warning("Traduction locale dans la mauvaise langue (%r…) : nouvel "
                                "essai", out[:60])
                    out = self._local(text, target, strict=True)
                    if not self._is_language(out, target):
                        log.warning("Toujours la mauvaise langue : traduction en ligne")
                        out = ""
                if out:
                    log.info("Traduction vers %s (Qwen) en %.1f s : %s…", target,
                             time.monotonic() - t0, out[:50])
            except Exception as exc:
                log.warning("Traduction locale impossible (%s) : essai en ligne", exc)
                out = ""
        if not out:
            out = self._edge(text, target)
            log.info("Traduction vers %s (en ligne) en %.1f s", target, time.monotonic() - t0)
        with self._lock:
            self._cache[key] = out
        return out
