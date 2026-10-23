"""Modèle du texte à lire (mots, phrases) et lecteur audio avec suivi du mot en cours."""
from __future__ import annotations

import logging
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field

import numpy as np
from PySide6.QtCore import QObject, Signal

log = logging.getLogger(__name__)

_SENT_END = re.compile(r"[.!?…]+[\"'»”)\]]*$")
_SOFT_END = re.compile(r"[;:,]$")


@dataclass
class RWord:
    text: str
    start: int                    # position dans le texte complet
    end: int
    rect: tuple[float, float, float, float] | None = None   # x, y, w, h (écran, OCR)
    para: int = 0                 # paragraphe


@dataclass
class Document:
    text: str
    words: list[RWord]
    pieces: list[tuple[int, int]] = field(default_factory=list)   # [début, fin[ en mots
    start_word: int = 0           # où commencer la lecture

    def piece_of(self, word_idx: int) -> int:
        for i, (a, b) in enumerate(self.pieces):
            if a <= word_idx < b:
                return i
        return max(0, len(self.pieces) - 1)

    def para_words(self, para: int) -> list[int]:
        return [i for i, w in enumerate(self.words) if w.para == para]


def from_text(text: str) -> Document:
    words = [RWord(m.group(), m.start(), m.end()) for m in re.finditer(r"\S+", text)]
    doc = Document(text, words)
    doc.pieces = split_pieces(doc)
    return doc


def from_paragraphs(paragraphs: list[str], start_para: int = 0,
                    start_offset: int = 0) -> Document:
    """Paragraphes de texte (UI Automation). start_offset : position du clic,
    en caractères, dans le paragraphe start_para."""
    parts, words, pos, start_word = [], [], 0, 0
    for p, text in enumerate(paragraphs):
        if parts:
            parts.append("\n\n")
            pos += 2
        for m in re.finditer(r"\S+", text):
            if p == start_para and m.end() <= start_offset:
                start_word = len(words) + 1
            words.append(RWord(m.group(), pos + m.start(), pos + m.end(), None, p))
        parts.append(text)
        pos += len(text)
    doc = Document("".join(parts), words)
    doc.pieces = split_pieces(doc)
    para_first = next((i for i, w in enumerate(words) if w.para == start_para), 0)
    doc.start_word = min(max(start_word, para_first), max(0, len(words) - 1))
    return doc


def from_ocr_paragraphs(paras) -> Document:
    """paras : paragraphes -> lignes -> mots OCR (coordonnées écran logiques)."""
    parts: list[str] = []
    words: list[RWord] = []
    pos = 0
    for p, lines in enumerate(paras):
        for li, line in enumerate(lines):
            if parts:
                sep = "\n\n" if li == 0 else "\n"
                parts.append(sep)
                pos += len(sep)
            for j, w in enumerate(line):
                if j:
                    parts.append(" ")
                    pos += 1
                words.append(RWord(w.text, pos, pos + len(w.text), (w.x, w.y, w.w, w.h), p))
                parts.append(w.text)
                pos += len(w.text)
    doc = Document("".join(parts), words)
    doc.pieces = split_pieces(doc)
    return doc


def from_ocr_lines(lines) -> Document:
    """Compatibilité : lignes OCR -> paragraphes déduits de la mise en page."""
    from .ocr_layout import group_paragraphs
    return from_ocr_paragraphs(group_paragraphs(lines))


def split_pieces(doc: Document, max_words: int = 32) -> list[tuple[int, int]]:
    """Découpe en phrases (ou morceaux courts) pour une lecture fluide."""
    pieces = []
    start = 0
    n = len(doc.words)
    for i, w in enumerate(doc.words):
        nxt = doc.words[i + 1] if i + 1 < n else None
        between = doc.text[w.end:nxt.start] if nxt else ""
        length = i + 1 - start
        end_here = (
            nxt is None
            or bool(_SENT_END.search(w.text))
            or "\n\n" in between
            or (length >= max_words * 0.6 and _SOFT_END.search(w.text))
            or length >= max_words
        )
        if end_here:
            pieces.append((start, i + 1))
            start = i + 1
    return pieces


def speak_text(doc: Document, a: int, b: int) -> str:
    """Texte envoyé à la voix pour les mots [a, b[ (recolle les mots coupés en fin de ligne)."""
    out = ""
    for i in range(a, b):
        w = doc.words[i].text
        if i > a:
            prev = doc.words[i - 1]
            between = doc.text[prev.end:doc.words[i].start]
            if prev.text.endswith("-") and "\n" in between and w[:1].islower():
                out = out[:-1]          # "exem-" + "ple" -> "exemple"
            else:
                out += " "
        out += w
    return out


def word_weights(words: list[RWord]) -> list[float]:
    weights = []
    for w in words:
        core = len(re.sub(r"\W", "", w.text)) or 1
        bonus = 4.0 if _SENT_END.search(w.text) else 2.5 if _SOFT_END.search(w.text) else 0.0
        weights.append(core + 1.5 + bonus)
    return weights


def _proportional(audio, words) -> list[int]:
    """Début estimé de chaque mot, proportionnel à sa longueur (sans repères)."""
    loud = np.flatnonzero(np.abs(audio.astype(np.int32)) > 600)
    lead = int(loud[0]) if len(loud) else 0
    tail = int(loud[-1]) if len(loud) else len(audio)
    span = max(1, tail - lead)
    weights = word_weights(words) or [1.0]
    total = sum(weights)
    starts, acc = [], 0.0
    for wt in weights:
        starts.append(lead + int(span * acc / total))
        acc += wt
    return starts


class Player(QObject):
    """Lit un Document à partir d'un mot ; émet wordChanged(indice) pendant la lecture."""

    wordChanged = Signal(int)
    stateChanged = Signal(str)          # playing, paused, stopped
    translation = Signal(str)           # lecture traduite : texte du morceau en cours
    translatedWord = Signal(int)        # lecture traduite : mot lu dans ce texte
    finished = Signal()                 # lecture arrivée au bout du texte
    error = Signal(str)

    def __init__(self, tts, doc: Document, lang: str, speed: float, translator=None):
        super().__init__()
        self.tts = tts
        self.translator = translator
        self.target: str | None = None      # langue de la traduction lue (None = original)
        self.auto_voice = False             # écoute automatique : voix selon chaque morceau
        self.doc = doc
        self.lang = lang
        self.speed = speed
        self.current = 0
        self._gen = 0
        self._paused = threading.Event()
        self._pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="synthese")
        self._thread: threading.Thread | None = None
        self.playing = False

    # ----------------------------------------------------------------- contrôle
    def play_from(self, word_idx: int) -> None:
        if not self.doc.words:
            return
        self._gen += 1
        self._paused.clear()
        self.current = max(0, min(word_idx, len(self.doc.words) - 1))
        gen = self._gen
        self._thread = threading.Thread(target=self._run, args=(gen, self.current),
                                        daemon=True, name="lecture")
        self.playing = True
        self._thread.start()
        self.stateChanged.emit("playing")

    def toggle_pause(self) -> None:
        if not self.playing:
            self.play_from(self.current)
        elif self._paused.is_set():
            self._paused.clear()
            self.stateChanged.emit("playing")
        else:
            self._paused.set()
            self.stateChanged.emit("paused")

    def stop(self) -> None:
        self._gen += 1
        self._paused.clear()
        self.playing = False
        self.stateChanged.emit("stopped")

    def set_speed(self, speed: float) -> None:
        self.speed = round(max(0.5, min(2.5, speed)), 2)
        if self.playing and not self._paused.is_set():
            self.play_from(self.current)

    def set_lang(self, lang: str) -> None:
        self.lang = lang
        if self.playing and not self._paused.is_set():
            self.play_from(self.current)

    def set_target(self, target: str | None) -> None:
        """Lire une traduction (fr, pt, en) ou l'original (None)."""
        self.target = target or None
        if self.playing:
            self.play_from(self.current)

    def shutdown(self) -> None:
        self.stop()
        self._pool.shutdown(wait=False, cancel_futures=True)

    # ----------------------------------------------------------------- interne
    def _segments(self, start_word: int):
        """Morceaux à lire : le reste de la phrase en cours, puis les phrases suivantes.
        Avec une voix en ligne, les phrases suivantes sont regroupées (jusqu'à environ
        60 mots) : moins de requêtes, et les pauses entre phrases restent naturelles."""
        p = self.doc.piece_of(start_word)
        a, b = self.doc.pieces[p]
        # traduction : toujours des phrases entières (une demi-phrase se traduit mal)
        yield (a if self.target else max(a, start_word)), b
        online = False
        try:
            online = self.tts.voice_for(self.target or self.lang).engine == "edge"
        except Exception:
            pass
        group = None
        first_group = True
        for a, b in self.doc.pieces[p + 1:]:
            if not online:
                yield a, b
                continue
            # premier groupe court (il doit être prêt vite), puis jusqu'à 60 mots
            limit = 20 if first_group else 60
            if group and (b - group[0] > limit or self._paragraph_break(group[1], a)):
                first_group = False
                yield group
                group = None
            group = (group[0], b) if group else (a, b)
        if group:
            yield group

    def _paragraph_break(self, end_word: int, next_word: int) -> bool:
        if end_word <= 0 or next_word >= len(self.doc.words):
            return False
        between = self.doc.text[self.doc.words[end_word - 1].end:self.doc.words[next_word].start]
        return "\n\n" in between

    @staticmethod
    def _segment_lang(text: str, default: str) -> str:
        """Langue d'un morceau lu (une page peut mélanger plusieurs langues)."""
        from . import langdetect
        from .languages import LANGS
        if len(text.split()) >= 12:
            code = langdetect.detect_any(text, default)
            return code if code in LANGS else default
        return langdetect.detect(text, default, tuple(LANGS))

    def _synth(self, a: int, b: int, speed: float, lang: str, target: str | None = None):
        text = speak_text(self.doc, a, b)
        if target and self.translator is not None:
            if self._segment_lang(text, target) == target:
                # Morceau déjà dans la langue d'écoute : lu tel quel, sans traduction.
                speech = self.tts.synth(text, target, speed)
                speech.translated = text
                return speech
            translated = self.translator.translate(text, target)
            speech = self.tts.synth(translated, target, speed)
            speech.translated = translated
            return speech
        if self.auto_voice:
            # Écoute « automatique » : chaque morceau avec la voix de sa langue.
            seg = self._segment_lang(text, lang)
            if seg != lang and self.tts.available(seg):
                return self.tts.synth(text, seg, speed)
        return self.tts.synth(text, lang, speed)

    def _run(self, gen: int, start_word: int) -> None:
        import sounddevice as sd

        speed, lang, target = self.speed, self.lang, self.target
        segs = list(self._segments(start_word))
        try:
            futures = {}
            if segs:
                futures[0] = self._pool.submit(self._synth, *segs[0], speed, lang, target)
            stream = None
            for k, (a, b) in enumerate(segs):
                if gen != self._gen:
                    return
                for nxt in (k + 1, k + 2):      # prépare les deux morceaux suivants
                    if nxt < len(segs) and nxt not in futures:
                        futures[nxt] = self._pool.submit(self._synth, *segs[nxt], speed, lang,
                                                         target)
                speech = futures.pop(k).result()
                if gen != self._gen:
                    return
                if stream is None or stream.samplerate != speech.sample_rate:
                    if stream is not None:
                        stream.close()
                    stream = sd.OutputStream(samplerate=speech.sample_rate, channels=1,
                                             dtype="int16")
                    stream.start()
                if not self._play(stream, speech, a, b, gen):
                    stream.abort()
                    stream.close()
                    return
            if stream is not None:
                stream.stop()
                stream.close()
            if gen == self._gen:
                self.playing = False
                self.wordChanged.emit(-1)
                self.finished.emit()
                self.stateChanged.emit("stopped")
        except Exception as exc:
            log.exception("Erreur de lecture")
            if gen == self._gen:
                self.playing = False
                self.error.emit(str(exc))
                self.stateChanged.emit("stopped")

    def _timeline(self, speech, a: int, b: int) -> list[int]:
        """Échantillon de début de chaque mot [a, b[."""
        words = self.doc.words[a:b]
        if speech.word_bounds and len(speech.word_bounds) == len(words):
            return [s for s, _ in speech.word_bounds]
        return _proportional(speech.audio, words)

    def _play(self, stream, speech, a: int, b: int, gen: int) -> bool:
        translated = getattr(speech, "translated", None)
        tstarts: list[int] = []
        shown_t = -1
        if translated is not None:
            # Lecture d'une traduction : les repères de mots portent sur le texte
            # traduit ; l'original avance proportionnellement (pour le cadre).
            twords = translated.split()
            tb = speech.word_bounds
            if tb and len(tb) == len(twords):
                tstarts = [s for s, _ in tb]
            else:
                tstarts = _proportional(speech.audio, [RWord(w, 0, 0) for w in twords])
            starts = _proportional(speech.audio, self.doc.words[a:b])
            if gen == self._gen:
                self.translation.emit(translated)
        else:
            starts = self._timeline(speech, a, b)
        audio = speech.audio
        sr = speech.sample_rate
        block = int(sr * 0.04)
        latency = int(float(stream.latency) * sr)
        pos = 0
        shown = -1
        while pos < len(audio):
            if gen != self._gen:
                return False
            if self._paused.is_set():
                time.sleep(0.05)
                continue
            stream.write(audio[pos:pos + block].reshape(-1, 1))
            pos += block
            heard = max(0, pos - latency)
            idx = a
            for j, s in enumerate(starts):
                if s <= heard:
                    idx = a + j
                else:
                    break
            if idx != shown and gen == self._gen:
                shown = idx
                self.current = idx
                self.wordChanged.emit(idx)
            if tstarts:
                j = 0
                for k, s in enumerate(tstarts):
                    if s <= heard:
                        j = k
                    else:
                        break
                if j != shown_t and gen == self._gen:
                    shown_t = j
                    self.translatedWord.emit(j)
        return True
