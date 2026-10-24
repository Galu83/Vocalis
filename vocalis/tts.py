"""Synthèse vocale locale : voix Piper (téléchargées) ou voix intégrées à Windows."""
from __future__ import annotations

import io
import logging
import re
import threading
import time
import wave
from dataclasses import dataclass

import numpy as np

from . import voices as V

log = logging.getLogger(__name__)

_SKIP = {"^", "$", "_"}                    # début, fin, remplissage
_PUNCT = set(",.;:!?¡¿—–-…\"«»“”()")


@dataclass
class Speech:
    audio: np.ndarray                      # int16 mono
    sample_rate: int
    word_bounds: list[tuple[int, int]] | None   # (début, fin) en échantillons, par mot


def word_bounds_from_alignments(alignments, offset: int = 0) -> list[tuple[int, int]]:
    """Regroupe les phonèmes alignés en mots (séparés par l'espace phonétique)."""
    bounds: list[tuple[int, int]] = []
    pos = offset
    start = end = None
    for al in alignments:
        ph, n = al.phoneme, int(al.num_samples)
        if ph == " " or ph == "$":
            if start is not None:
                bounds.append((start, end))
                start = end = None
            pos += n
            continue
        if ph in _SKIP or ph in _PUNCT:
            pos += n
            continue
        if start is None:
            start = pos
        pos += n
        end = pos
    if start is not None:
        bounds.append((start, end))
    return bounds


def _wav_to_int16(data: bytes) -> tuple[np.ndarray, int]:
    with wave.open(io.BytesIO(data)) as w:
        rate, ch, width = w.getframerate(), w.getnchannels(), w.getsampwidth()
        raw = w.readframes(w.getnframes())
    if width == 2:
        a = np.frombuffer(raw, dtype=np.int16)
    elif width == 1:
        a = ((np.frombuffer(raw, dtype=np.uint8).astype(np.int16) - 128) << 8)
    else:
        a = (np.frombuffer(raw, dtype=np.int32) >> 16).astype(np.int16)
    if ch > 1:
        a = a.reshape(-1, ch).mean(axis=1).astype(np.int16)
    return a, rate


class Tts:
    def __init__(self, cfg: dict):
        lec = cfg["lecture"]
        from .languages import ALL
        self.choice = {code: V.from_config(lec.get(f"voix_{code}", ""), code) for code in ALL
                       if code in V.DEFAULT}
        # Autres langues (allemand, espagnol…) : une voix Edge multilingue les lit
        # dans leur langue.
        multi = V.find("edge:fr-FR-VivienneMultilingualNeural", "fr")
        if multi is not None:
            self.choice["multi"] = multi
        self._piper: dict = {}
        self._lock = threading.Lock()
        self._piper_run = threading.Lock()      # une seule synthèse Piper à la fois
        # pause gardée après un point (en secondes à vitesse normale)
        self.pause = float(lec.get("pause_phrases", 0.2))

    def voice_for(self, lang: str) -> V.Voice:
        return self.choice.get(lang) or V.from_config("", lang)

    def available(self, lang: str) -> bool:
        return lang in self.choice and V.installed(self.choice[lang])

    # ------------------------------------------------------------------ Piper
    def _piper_voice(self, v: V.Voice):
        with self._lock:
            if v.file not in self._piper:
                from piper import PiperVoice
                path = V.local_path(v)
                if not path.exists():
                    raise FileNotFoundError(f"Voix non téléchargée : {v.name}")
                try:
                    voice = PiperVoice.load(str(path), include_alignments=True)
                except TypeError:  # ancienne version de Piper
                    voice = PiperVoice.load(str(path))
                self._piper[v.file] = voice
                log.info("Voix chargée : %s (%s)", v.name, path.name)
            return self._piper[v.file]

    def _synth_piper(self, v: V.Voice, text: str, speed: float) -> Speech:
        with self._piper_run:
            return self._synth_piper_locked(v, text, speed)

    def _synth_piper_locked(self, v: V.Voice, text: str, speed: float) -> Speech:
        voice = self._piper_voice(v)
        from piper import SynthesisConfig
        cfg = SynthesisConfig(length_scale=1.0 / max(0.3, speed), speaker_id=v.speaker)
        parts: list[np.ndarray] = []
        bounds: list[tuple[int, int]] = []
        aligned = True
        offset = 0
        try:
            chunks = voice.synthesize(text, syn_config=cfg, include_alignments=True)
        except TypeError:
            chunks = voice.synthesize(text, syn_config=cfg)
        for chunk in chunks:
            audio = chunk.audio_int16_array
            als = getattr(chunk, "phoneme_alignments", None)
            if als:
                bounds += word_bounds_from_alignments(als, offset)
            else:
                aligned = False
            parts.append(audio)
            offset += len(audio)
        audio = np.concatenate(parts) if parts else np.zeros(0, dtype=np.int16)
        return Speech(audio, int(voice.config.sample_rate), bounds if aligned else None)

    # ------------------------------------------------------------------ Windows
    def _synth_windows(self, v: V.Voice, text: str, speed: float) -> Speech:
        import asyncio

        from winrt.windows.media.speechsynthesis import SpeechSynthesizer
        from winrt.windows.storage.streams import DataReader

        voice_id = v.id.split(":", 1)[1]

        async def run():
            synth = SpeechSynthesizer()
            info = next((i for i in SpeechSynthesizer.all_voices if i.id == voice_id), None)
            if info is None:
                raise RuntimeError(f"Voix Windows introuvable : {v.name}")
            synth.voice = info
            synth.options.include_word_boundary_metadata = True
            synth.options.speaking_rate = max(0.5, min(6.0, speed))
            stream = await synth.synthesize_text_to_stream_async(text)
            size = int(stream.size)
            reader = DataReader(stream.get_input_stream_at(0))
            await reader.load_async(size)
            buf = bytearray(size)
            reader.read_bytes(buf)
            cues = []
            try:
                for track in stream.timed_metadata_tracks:
                    for cue in track.cues:
                        pos = getattr(cue, "start_position_in_input", None)
                        start = getattr(cue, "start_time", None)
                        if pos is not None and start is not None:
                            cues.append((int(pos), start.total_seconds(),
                                         cue.duration.total_seconds()))
            except Exception:
                log.debug("Repères de mots indisponibles", exc_info=True)
            return bytes(buf), cues

        data, cues = asyncio.run(run())
        audio, rate = _wav_to_int16(data)
        bounds = None
        spans = [(m.start(), m.end()) for m in re.finditer(r"\S+", text)]
        if cues and spans:
            starts: list[int | None] = []
            for a, b in spans:
                hit = next((c for c in cues if a <= c[0] < b), None)
                starts.append(int(hit[1] * rate) if hit else None)
            # mots sans repère (ponctuation seule…) : position du mot précédent
            last = 0
            for i, s in enumerate(starts):
                if s is None:
                    starts[i] = last
                last = starts[i]
            bounds = [(s, s) for s in starts]
        return Speech(audio, rate, bounds)

    # ------------------------------------------------------------------ Edge (en ligne)
    def _synth_edge(self, v: V.Voice, text: str, speed: float) -> Speech:
        import asyncio

        import edge_tts

        pct = int(round((max(0.5, min(3.0, speed)) - 1.0) * 100))
        rate = f"{pct:+d}%"

        async def run():
            com = edge_tts.Communicate(text, v.key, rate=rate, boundary="WordBoundary",
                                       connect_timeout=8, receive_timeout=30)
            audio = bytearray()
            marks = []
            async for chunk in com.stream():
                if chunk["type"] == "audio":
                    audio += chunk["data"]
                elif chunk["type"] == "WordBoundary":
                    marks.append((chunk["text"], chunk["offset"] / 1e7))
            return bytes(audio), marks

        try:
            mp3, marks = asyncio.run(run())
        except edge_tts.exceptions.NoAudioReceived:
            # Morceau sans rien à prononcer (tiret, symbole…) : un court silence.
            return Speech(np.zeros(2400, dtype=np.int16), 24000, None)
        audio, rate_hz = decode_mp3(mp3)
        return Speech(audio, rate_hz, align_marks(text, marks, rate_hz))

    # ------------------------------------------------------------------ API
    def synth(self, text: str, lang: str, speed: float) -> Speech:
        sp = self._synth_pronounced(text, lang, speed)
        gap = self.pause / max(0.5, speed)
        try:
            return shape_pauses(sp.audio, sp.sample_rate, sp.word_bounds, gap)
        except Exception:
            log.exception("Raccourcissement des pauses impossible")
            return sp

    def _synth_pronounced(self, text: str, lang: str, speed: float) -> Speech:
        """Applique le dictionnaire de prononciation ; les repères de mots restent
        rattachés aux mots du texte d'origine."""
        from . import dictionary
        try:
            spoken, mapping = dictionary.pronounce(text, lang)
        except Exception:
            log.exception("Dictionnaire de prononciation illisible")
            spoken, mapping = text, None
        if mapping is None:
            return self._synth_raw(text, lang, speed)
        sp = self._synth_raw(spoken, lang, speed)
        n = len(spoken.split())
        if sp.word_bounds and len(sp.word_bounds) == n:
            sp.word_bounds = [sp.word_bounds[m] for m in mapping]
        else:
            sp.word_bounds = None
        return sp

    def _offline_voice(self, lang: str):
        """Voix de secours sans Internet : Piper si elle est installée, sinon une voix
        intégrée à Windows dans la langue (toujours présente pour la langue du système)."""
        cache = self.__dict__.setdefault("_offline_cache", {})
        if lang in cache:
            return cache[lang]
        found = None
        base = lang if lang in V.DEFAULT else "fr"
        piper = V.from_config(V.DEFAULT[base], base)
        if piper.engine == "piper" and V.installed(piper):
            found = piper
        else:
            for cand in ([lang] if lang in V.DEFAULT else []) + ["fr", "en"]:
                try:
                    voices = V.windows_voices(cand)
                except Exception:
                    voices = []
                if voices:
                    found = voices[0]
                    break
        cache[lang] = found
        return found

    def _synth_raw(self, text: str, lang: str, speed: float) -> Speech:
        v = self.voice_for(lang)
        if v.engine == "edge":
            try:
                from .hardware import offline
                if offline():
                    raise RuntimeError("pas d'Internet (mode test hors ligne) il y a peu")
                if time.monotonic() < getattr(self, "_edge_down_until", 0.0):
                    raise RuntimeError("service en ligne injoignable il y a peu")
                return self._synth_edge(v, text, speed)
            except Exception as exc:
                if "il y a peu" not in str(exc):
                    self._edge_down_until = time.monotonic() + 60
                # Pas d'Internet (ou service indisponible) : voix installée sur le PC.
                fallback = self._offline_voice(lang)
                if fallback is None:
                    raise
                log.warning("Voix en ligne %s indisponible (%s) : voix %s utilisée",
                            v.name, exc, fallback.name)
                if fallback.engine == "windows":
                    return self._synth_windows(fallback, text, speed)
                return self._synth_piper(fallback, text, speed)
        if v.engine == "windows":
            return self._synth_windows(v, text, speed)
        return self._synth_piper(v, text, speed)


def shape_pauses(audio: np.ndarray, rate: int, bounds, gap: float) -> Speech:
    """Raccourcit les silences : au début (30 ms), à la fin et entre les phrases
    (au plus « gap » secondes). Les voix, surtout celles d'Edge, marquent de longues
    pauses après un point ou un paragraphe ; enchaînées, elles cassent le rythme.
    Les repères de mots sont décalés en conséquence."""
    n = len(audio)
    frame = max(1, int(0.01 * rate))
    k = n // frame
    if k < 3:
        return Speech(audio, rate, bounds)
    a32 = audio[: k * frame].astype(np.float32).reshape(k, frame)
    silent = np.sqrt(np.mean(a32 ** 2, axis=1)) < 250
    if silent.all():
        return Speech(audio[: int(gap * rate)], rate, None)
    keep_gap = max(frame, int(gap * rate))
    cuts: list[tuple[int, int]] = []           # [début, fin[ en échantillons à retirer
    i = 0
    while i < k:
        if not silent[i]:
            i += 1
            continue
        j = i
        while j < k and silent[j]:
            j += 1
        s0, s1 = i * frame, (j * frame if j < k else n)
        if i == 0:                                  # silence de début
            cut = (0, max(0, s1 - int(0.03 * rate)))
        elif j >= k:                                # silence de fin
            cut = (s0 + keep_gap, n)
        elif s1 - s0 > keep_gap:                    # pause trop longue au milieu
            half = keep_gap // 2
            cut = (s0 + half, s1 - (keep_gap - half))
        else:
            cut = None
        if cut and cut[1] > cut[0]:
            cuts.append(cut)
        i = j
    if not cuts:
        return Speech(audio, rate, bounds)
    keep = []
    pos = 0
    for c0, c1 in cuts:
        keep.append(audio[pos:c0])
        pos = c1
    keep.append(audio[pos:])
    out = np.concatenate(keep)

    starts = np.array([c0 for c0, _ in cuts])
    removed = np.cumsum([c1 - c0 for c0, c1 in cuts])

    def remap(x: int) -> int:
        idx = int(np.searchsorted(starts, x, side="right")) - 1
        if idx < 0:
            return x
        c0, c1 = cuts[idx]
        before = int(removed[idx - 1]) if idx > 0 else 0
        if x < c1:                                  # dans une partie retirée
            return c0 - before
        return x - int(removed[idx])

    if bounds:
        bounds = [(remap(int(a)), remap(int(b))) for a, b in bounds]
    return Speech(out, rate, bounds)


def _norm(word: str) -> str:
    return re.sub(r"[^\w]", "", word.lower())


def align_marks(text: str, marks: list[tuple[str, float]], rate: int):
    """Rattache les repères de mots du service (texte, instant) aux mots du texte
    (séparés par des espaces). Renvoie (début, début) en échantillons par mot."""
    spans = re.findall(r"\S+", text)
    if not spans or not marks:
        return None
    starts: list[int | None] = [None] * len(spans)
    j = 0
    for word, t in marks:
        w = _norm(word)
        if not w:
            continue
        for k in range(j, min(j + 4, len(spans))):
            s = _norm(spans[k])
            if s and (s.startswith(w) or w.startswith(s) or w in s):
                if starts[k] is None:
                    starts[k] = int(t * rate)
                j = k + 1 if s == w or w.endswith(s) else k
                break
    last = 0
    for i, s in enumerate(starts):
        if s is None:
            starts[i] = last
        last = starts[i]
    return [(s, s) for s in starts]


def decode_mp3(data: bytes) -> tuple[np.ndarray, int]:
    """MP3 -> int16 mono. miniaudio d'abord ; sinon le décodeur intégré à Windows."""
    try:
        import miniaudio
        d = miniaudio.decode(bytes(data), output_format=miniaudio.SampleFormat.SIGNED16,
                             nchannels=1, sample_rate=24000)
        return np.frombuffer(d.samples, dtype=np.int16).copy(), d.sample_rate
    except ImportError:
        pass
    return _wav_to_int16(_mp3_to_wav_windows(data))


def _mp3_to_wav_windows(data: bytes) -> bytes:
    import asyncio

    from winrt.windows.media.mediaproperties import AudioEncodingQuality, MediaEncodingProfile
    from winrt.windows.media.transcoding import MediaTranscoder
    from winrt.windows.storage.streams import (DataReader, DataWriter,
                                               InMemoryRandomAccessStream)

    async def run():
        src = InMemoryRandomAccessStream()
        w = DataWriter(src)
        w.write_bytes(data)
        await w.store_async()
        w.detach_stream()
        src.seek(0)
        dst = InMemoryRandomAccessStream()
        profile = MediaEncodingProfile.create_wav(AudioEncodingQuality.MEDIUM)
        prep = await MediaTranscoder().prepare_stream_transcode_async(src, dst, profile)
        if not prep.can_transcode:
            raise RuntimeError(f"Décodage MP3 impossible ({prep.failure_reason})")
        await prep.transcode_async()
        size = int(dst.size)
        reader = DataReader(dst.get_input_stream_at(0))
        await reader.load_async(size)
        buf = bytearray(size)
        reader.read_bytes(buf)
        return bytes(buf)

    return asyncio.run(run())
