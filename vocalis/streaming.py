"""Transcription en continu pour l'aperçu, puis transcription finale complète.

Aperçu (pendant que la personne parle) :
- « validés » : mots sur lesquels deux passes successives de Whisper sont d'accord
  (principe LocalAgreement) ; ils ne bougent plus ;
- « provisoires » : la fin de la phrase en cours, qui peut encore changer.
L'aperçu affiché = validés + provisoires, mis à jour à chaque passe.

Final (quand la personne arrête) : tout l'enregistrement est retranscrit d'un coup,
avec la meilleure précision et tout le contexte.

La langue est choisie une seule fois par dictée (un texte = une langue).
"""
from __future__ import annotations

import logging
import re
import unicodedata

import numpy as np

from .asr import SAMPLE_RATE, Word

log = logging.getLogger(__name__)

LOCK_PROB = 0.8          # confiance nécessaire pour fixer la langue (sans langue choisie)
SWITCH_PROB = 0.85       # confiance nécessaire pour quitter la langue choisie sur la pastille


def norm_word(text: str) -> str:
    t = unicodedata.normalize("NFC", text.strip().lower())
    t = re.sub(r"[^\w']", "", t)
    return t or text.strip()


def words_to_text(words: list[Word]) -> str:
    return "".join(w.text for w in words).strip()


class StreamingTranscriber:
    def __init__(self, asr, forced_language: str = "", preferred: str = "",
                 live_beam: int = 1, final_beam: int = 5, trim_seconds: float | None = None):
        self.asr = asr
        self.forced = forced_language or ""     # imposée (réglage avancé) ou cliquée
        self.preferred = preferred or ""        # langue choisie sur la pastille
        self.lang = self.forced or None
        self._checked_switch = False
        self.live_beam = live_beam
        self.final_beam = final_beam
        self.trim_seconds = trim_seconds or getattr(asr, "live_window", 15.0)
        self.chunks: list[np.ndarray] = []      # tout l'enregistrement (pour la fin)
        self.buf = np.zeros(0, dtype=np.float32)
        self.buf_t0 = 0.0                       # instant (s) du début du tampon
        self.committed: list[Word] = []
        self.pending: list[Word] = []
        self._best_guess: tuple[str, float] | None = None

    # ------------------------------------------------------------------ état
    @property
    def duration(self) -> float:
        return len(self.buf) / SAMPLE_RATE

    @property
    def total_duration(self) -> float:
        return sum(len(c) for c in self.chunks) / SAMPLE_RATE

    @property
    def preview(self) -> str:
        """Texte de l'aperçu : mots validés puis provisoires."""
        return words_to_text(self.committed + self.pending)

    def live_parts(self) -> tuple[str, str, list[str]]:
        """Pour la fenêtre de dictée : (phrase en cours : partie sûre, partie
        provisoire, phrases validées). Une phrase est validée quand tous ses mots
        sont sûrs et qu'elle se termine par . ! ? ou …"""
        done: list[str] = []
        current: list[Word] = []
        for w in self.committed:
            current.append(w)
            if re.search(r"[.!?…]['\"»”)]*$", w.text.strip()):
                done.append(words_to_text(current))
                current = []
        return words_to_text(current), words_to_text(self.pending), done

    def add_audio(self, chunk: np.ndarray) -> None:
        if len(chunk):
            chunk = chunk.astype(np.float32, copy=False)
            self.chunks.append(chunk)
            self.buf = np.concatenate([self.buf, chunk])

    # ------------------------------------------------------------------ langue
    def set_language(self, lang: str) -> None:
        """Choix explicite pendant la dictée (clic sur la pastille) : il est imposé."""
        self.forced = self.lang = self.preferred = lang
        log.info("Langue imposée pendant la dictée : %s", lang)

    def _ensure_language(self) -> bool:
        """Avec une langue choisie : on l'utilise tout de suite ; on n'en change que si
        une autre est détectée à ≥ 85 % (une seule vérification, sur ~2 s de parole).
        Sans langue choisie : on fixe la langue dès qu'elle est sûre (≥ 80 %)."""
        if self.forced:
            self.lang = self.forced
            return True
        if self.preferred:
            if self.lang is None:
                self.lang = self.preferred
            if not self._checked_switch and self.duration >= 2.0 and (self.committed or
                                                                       self.pending):
                self._checked_switch = True
                lang, prob = self.asr.detect_language(self.buf)
                if lang != self.preferred and prob >= SWITCH_PROB:
                    log.info("Langue choisie %s, mais %s détectée à %.2f : on change",
                             self.preferred, lang, prob)
                    self.lang = lang
            return True
        if self.lang:
            return True
        if self.duration < 1.0:
            return False
        lang, prob = self.asr.detect_language(self.buf)
        if prob >= LOCK_PROB or self.total_duration >= 8.0:
            self.lang = lang
            log.info("Langue fixée pour cette dictée : %s (%.2f)", lang, prob)
        else:
            self._best_guess = (lang, prob)
        return True

    @property
    def live_language(self) -> str:
        if self.lang:
            return self.lang
        if self._best_guess:
            return self._best_guess[0]
        return self.asr.allowed[0]

    # ------------------------------------------------------------------ interne
    def _prompt(self) -> str:
        old = [w.text for w in self.committed if w.end <= self.buf_t0 + 0.01]
        return "".join(old).strip()[-200:]

    def _hypothesis(self) -> list[Word]:
        words = self.asr.transcribe_words(self.buf, self.live_language, self._prompt(),
                                          self.live_beam)
        words = [Word(w.start + self.buf_t0, w.end + self.buf_t0, w.text) for w in words]
        last_end = self.committed[-1].end if self.committed else 0.0
        words = [w for w in words if w.start > last_end - 0.1]
        if words and self.committed and words[0].start < last_end + 0.05:
            for n in range(min(5, len(self.committed), len(words)), 0, -1):
                tail = [norm_word(w.text) for w in self.committed[-n:]]
                head = [norm_word(w.text) for w in words[:n]]
                if tail == head:
                    words = words[n:]
                    break
        return words

    def _cut(self, t: float) -> None:
        if t > self.buf_t0:
            drop = min(len(self.buf), int((t - self.buf_t0) * SAMPLE_RATE))
            self.buf = self.buf[drop:]
            self.buf_t0 += drop / SAMPLE_RATE

    def _trim(self) -> None:
        if not self.committed and not self.pending and self.duration > 3.0:
            self._cut(self.buf_t0 + self.duration - 1.0)   # silence avant de parler
            return
        if self.duration <= self.trim_seconds:
            return
        if self.committed:
            self._cut(self.committed[-1].end)
        elif self.duration > 25.0:
            self.committed.extend(self.pending)
            cut = self.pending[-1].end if self.pending else self.buf_t0 + self.duration - 5
            self.pending = []
            self._cut(cut)

    # ------------------------------------------------------------------ API
    def process(self) -> str:
        """Nouvelle passe pendant la dictée ; renvoie le texte d'aperçu."""
        if self.duration < 0.4 or not self._ensure_language():
            return self.preview
        hyp = self._hypothesis()
        agreed = 0
        for old, cur in zip(self.pending, hyp):
            if norm_word(old.text) != norm_word(cur.text):
                break
            agreed += 1
        self.committed.extend(hyp[:agreed])
        self.pending = hyp[agreed:]
        self._trim()
        guess = getattr(self.asr, "guess_language", None)
        if guess and not self.forced and len(self.committed) >= 6:
            # Moteur qui choisit la langue tout seul (Parakeet) : on la devine d'après
            # le texte, pour le badge et pour la correction.
            self.lang = guess(self.preview, self.preferred or self.live_language)
        return self.preview

    def final_text(self) -> tuple[str, str]:
        """Retranscrit tout l'enregistrement. Renvoie (texte, langue)."""
        if not self.chunks:
            return "", self.live_language
        audio = np.concatenate(self.chunks)
        if len(audio) < int(0.3 * SAMPLE_RATE):
            return "", self.live_language
        lang = self.forced
        if not lang:
            detected, prob = self.asr.detect_language(audio)
            if self.preferred:
                lang = detected if (detected != self.preferred and prob >= SWITCH_PROB) \
                    else self.preferred
            else:
                lang = detected if (prob >= 0.5 or not self.lang) else self.lang
        if hasattr(self.asr, "transcribe_final"):
            text = self.asr.transcribe_final(audio, lang, self.final_beam)
            guess = getattr(self.asr, "guess_language", None)
            if guess and not self.forced and text:
                lang = guess(text, self.preferred or lang)
            return text, lang
        words = self.asr.transcribe_words(audio, lang, "", self.final_beam,
                                          condition_on_previous_text=True)
        return words_to_text(words), lang


def common_prefix(a: str, b: str) -> int:
    n = min(len(a), len(b))
    i = 0
    while i < n and a[i] == b[i]:
        i += 1
    return i
