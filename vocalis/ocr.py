"""Reconnaissance de texte à l'écran avec l'OCR intégré à Windows (local)."""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass

log = logging.getLogger(__name__)


@dataclass
class OcrWord:
    text: str
    x: float
    y: float
    w: float
    h: float


def available_languages() -> list[str]:
    try:
        from winrt.windows.media.ocr import OcrEngine
        return [l.language_tag for l in OcrEngine.available_recognizer_languages]
    except Exception:
        log.exception("OCR Windows indisponible")
        return []


def max_dimension() -> int:
    try:
        from winrt.windows.media.ocr import OcrEngine
        return int(OcrEngine.max_image_dimension)
    except Exception:
        return 2600


def _engine(lang_prefix: str):
    from winrt.windows.globalization import Language
    from winrt.windows.media.ocr import OcrEngine
    for tag in available_languages():
        if tag.lower().startswith(lang_prefix):
            engine = OcrEngine.try_create_from_language(Language(tag))
            if engine:
                return engine
    return OcrEngine.try_create_from_user_profile_languages()


def has_language(lang_prefix: str) -> bool:
    return any(t.lower().startswith(lang_prefix) for t in available_languages())


def recognize(bgra: bytes, width: int, height: int, lang_prefix: str) -> list[list[OcrWord]]:
    """Renvoie les lignes de texte (listes de mots avec position en pixels de l'image)."""
    from winrt.windows.graphics.imaging import BitmapPixelFormat, SoftwareBitmap
    from winrt.windows.storage.streams import DataWriter

    engine = _engine(lang_prefix)
    if engine is None:
        raise RuntimeError("Aucune langue OCR installée dans Windows")
    writer = DataWriter()
    writer.write_bytes(bgra)
    bitmap = SoftwareBitmap.create_copy_from_buffer(writer.detach_buffer(),
                                                    BitmapPixelFormat.BGRA8, width, height)

    async def run():
        return await engine.recognize_async(bitmap)

    result = asyncio.run(run())
    lines: list[list[OcrWord]] = []
    for line in result.lines:
        words = []
        for w in line.words:
            r = w.bounding_rect
            words.append(OcrWord(w.text, r.x, r.y, r.width, r.height))
        if words:
            lines.append(words)
    return lines
