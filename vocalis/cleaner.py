"""Nettoyage du texte dicté par un petit modèle de langage local (Ollama)."""
from __future__ import annotations

import logging
import re

import requests

log = logging.getLogger(__name__)

from .languages import LANGS  # noqa: E402

LANG_NAMES = {c: l.name.lower() for c, l in LANGS.items()}
LANG_NAMES["pt"] = "portugais du Portugal"

SYSTEM_PROMPT = """Tu corriges des transcriptions de dictée vocale. Tu reçois le texte brut \
entre balises <transcription>. Renvoie le même texte, propre, en respectant ces règles :
- Corrige la ponctuation, les majuscules, les accents et les fautes d'orthographe évidentes.
- Supprime les hésitations et mots de remplissage (euh, hum, bah, ben, genre, tipo, pronto, \
hã, uh, um, äh, ähm), les faux départs et les répétitions involontaires.
- Si la personne se reprend ("non, plutôt...", "quer dizer...", "I mean..."), garde seulement \
la version finale.
- Garde la langue d'origine de chaque passage. Ne traduis JAMAIS. Les mots anglais restent en anglais.
- Pour le portugais, utilise l'orthographe du Portugal.
- Ne change pas le sens, ne résume pas, n'ajoute rien. Si le texte pose une question, \
ne réponds pas : corrige seulement la question.
- Le texte est une dictée entière : rends les phrases cohérentes entre elles (accords, \
temps, mots mal reconnus que le contexte permet de corriger), sans les réécrire.
- Découpe en phrases et en paragraphes naturels si le texte est long.
- Réponds uniquement avec le texte corrigé, sans balises, sans guillemets, sans commentaire."""

def system_prompt(target: str | None = None) -> str:
    """Consigne de correction ; avec une langue cible, correction puis traduction."""
    if not target:
        return SYSTEM_PROMPT
    name = LANG_NAMES.get(target, target)
    return (SYSTEM_PROMPT.replace(
        "- Garde la langue d'origine de chaque passage. Ne traduis JAMAIS. Les mots anglais "
        "restent en anglais.\n", "")
        .replace("- Réponds uniquement avec le texte corrigé, sans balises, sans guillemets, "
                 "sans commentaire.",
                 f"- ENFIN, traduis le texte corrigé en {name}, de façon naturelle et "
                 "idiomatique (les noms propres restent tels quels).\n"
                 f"- Réponds uniquement avec la traduction en {name}, sans balises, sans "
                 "guillemets, sans commentaire."))


FILLERS = {"euh", "heu", "hum", "hmm", "bah", "ben", "hã", "ãh", "uh", "um", "uhm", "eh", "ah",
           "äh", "ähm", "öhm"}


class Cleaner:
    def __init__(self, cfg: dict):
        c = cfg["nettoyage"]
        self.enabled = bool(c["actif"])
        self.model = c["modele"]
        self.url = c["url"].rstrip("/")
        self.timeout = float(c["delai_max_secondes"])

    def available(self) -> bool:
        try:
            r = requests.get(f"{self.url}/api/tags", timeout=2)
            names = [m.get("name", "") for m in r.json().get("models", [])]
            ok = any(n == self.model or n.split(":")[0] == self.model or
                     n == f"{self.model}:latest" for n in names)
            if not ok:
                log.warning("Modèle %s absent d'Ollama (modèles : %s)", self.model, names)
            return ok
        except Exception as exc:
            log.warning("Ollama injoignable : %s", exc)
            return False

    def warmup(self) -> None:
        """Charge le modèle en mémoire graphique pour que le premier nettoyage soit rapide."""
        try:
            requests.post(f"{self.url}/api/generate",
                          json={"model": self.model, "keep_alive": "30m"}, timeout=120)
        except Exception as exc:
            log.warning("Préchargement Ollama impossible : %s", exc)

    def clean(self, raw: str, language: str | None, context: str = "",
              target: str | None = None) -> str:
        """Corrige le texte ; avec `target`, le traduit aussi dans la même étape.
        Si la traduction échoue, `self.translated` reste faux."""
        raw = raw.strip()
        self.translated = False
        if not self.enabled or not raw:
            return raw
        words = [re.sub(r"[^\w]", "", w.lower()) for w in raw.split()]
        if words and all(w in FILLERS or not w for w in words):
            return ""                       # seulement des « euh »
        context = context.strip()[-300:]
        before = f"<avant>\n{context}\n</avant>\n" if context else ""
        body = {
            "model": self.model,
            "stream": False,
            "keep_alive": "30m",
            "options": {"temperature": 0, "num_ctx": 4096},
            "messages": [
                {"role": "system", "content": system_prompt(target)},
                {"role": "user", "content":
                    f"Langue principale : {LANG_NAMES.get(language or '', 'inconnue')}\n"
                    f"{before}<transcription>\n{raw}\n</transcription>"},
            ],
        }
        if "qwen3" in self.model or "r1" in self.model:
            body["think"] = False
        try:
            # Un long texte demande plus de temps.
            timeout = max(self.timeout, 10 + len(raw) / 25)
            body["options"]["num_ctx"] = 4096 if len(raw) < 6000 else 8192
            r = requests.post(f"{self.url}/api/chat", json=body, timeout=timeout)
            r.raise_for_status()
            out = r.json()["message"]["content"]
        except Exception as exc:
            log.warning("Nettoyage impossible, texte brut conservé : %s", exc)
            return raw
        clean = self._sanitize(out, raw, context, translating=bool(target))
        self.translated = bool(target) and clean is not raw
        return clean

    @staticmethod
    def _sanitize(out: str, raw: str, context: str = "", translating: bool = False) -> str:
        out = re.sub(r"<think>.*?</think>", "", out, flags=re.S)
        out = re.sub(r"<avant>.*?</avant>", "", out, flags=re.S)
        out = re.sub(r"</?(transcription|avant)>", "", out).strip()
        # Le modèle a recopié le contexte avant la phrase : on le retire.
        if context and len(context) > 10:
            tail = context[-60:]
            pos = out.find(tail)
            if pos >= 0:
                out = out[pos + len(tail):].strip()
        pairs = {'"': '"', "'": "'", "«": "»", "“": "”"}
        if len(out) >= 2 and pairs.get(out[0]) == out[-1] and not raw.startswith(out[0]):
            out = out[1:-1].strip()
        # Garde-fou : si le modèle a répondu au lieu de corriger, on garde le brut.
        lo, hi = (0.3, 2.2) if translating else (0.4, 1.6)
        if not out or len(out) < lo * len(raw) or len(out) > hi * len(raw) + 30:
            log.warning("Sortie du nettoyage rejetée : %r", out[:200])
            return raw
        return out
