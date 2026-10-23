"""Icône de l'application : « Voix et texte » (une bulle de voix devant une bulle de texte).

Deux dessins : le détaillé à partir de 32 px, un simplifié en dessous (pour l'icône
près de l'horloge en 16-24 px), où les petits détails deviendraient de la bouillie.
"""
from __future__ import annotations

from PySide6.QtCore import QByteArray, QRectF, Qt
from PySide6.QtGui import QIcon, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer

ACCENT = "#E5484D"
GREY = "#8A8D93"

# Dessin détaillé (repris de la planche d'icônes, proposition 04).
_FULL = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64">
<path d="M30 4h22a9 9 0 0 1 9 9v13a9 9 0 0 1-9 9h-2v7l-8-7H30a9 9 0 0 1-9-9V13a9 9 0 0 1 9-9z"
 fill="#16161A" stroke="#F4F4F6" stroke-width="2"/>
<rect x="28" y="12" width="26" height="4" rx="2" fill="#F4F4F6"/>
<rect x="28" y="21" width="17" height="4" rx="2" fill="#F4F4F6"/>
<path d="M12 27h22a9 9 0 0 1 9 9v12a9 9 0 0 1-9 9H20l-8 6v-6.3A9 9 0 0 1 3 48V36a9 9 0 0 1 9-9z"
 fill="{accent}" stroke="#FFFFFF" stroke-width="3"/>
<rect x="12" y="39" width="4" height="6" rx="2" fill="#FFFFFF"/>
<rect x="19" y="35" width="4" height="14" rx="2" fill="#FFFFFF"/>
<rect x="26" y="37" width="4" height="10" rx="2" fill="#FFFFFF"/>
<rect x="33" y="39" width="4" height="6" rx="2" fill="#FFFFFF"/>
</svg>"""

# Dessin simplifié pour 16-24 px : formes plus grosses, une seule ligne de texte,
# trois barres épaisses.
_SMALL = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64">
<path d="M28 2h25a9 9 0 0 1 9 9v14a9 9 0 0 1-9 9h-1v8l-9-8H28a9 9 0 0 1-9-9V11a9 9 0 0 1 9-9z"
 fill="#16161A" stroke="#F4F4F6" stroke-width="3"/>
<rect x="27" y="14" width="27" height="7" rx="3.5" fill="#F4F4F6"/>
<path d="M11 26h25a9 9 0 0 1 9 9v15a9 9 0 0 1-9 9H20l-9 5v-5.5A9 9 0 0 1 2 50V35a9 9 0 0 1 9-9z"
 fill="{accent}" stroke="#FFFFFF" stroke-width="3.5"/>
<rect x="11" y="37" width="6" height="11" rx="3" fill="#FFFFFF"/>
<rect x="20.5" y="33" width="6" height="19" rx="3" fill="#FFFFFF"/>
<rect x="30" y="37" width="6" height="11" rx="3" fill="#FFFFFF"/>
</svg>"""


def make_pixmap(size: int = 64, color: str = ACCENT) -> QPixmap:
    svg = (_SMALL if size <= 24 else _FULL).format(accent=color)
    renderer = QSvgRenderer(QByteArray(svg.encode()))
    pix = QPixmap(size, size)
    pix.fill(Qt.transparent)
    p = QPainter(pix)
    p.setRenderHint(QPainter.Antialiasing)
    renderer.render(p, QRectF(0, 0, size, size))
    p.end()
    return pix


def make_icon(color: str = ACCENT) -> QIcon:
    icon = QIcon()
    for size in (16, 20, 24, 32, 48, 64, 256):
        icon.addPixmap(make_pixmap(size, color))
    return icon


def save_ico(path) -> bool:
    """Crée le fichier .ico (plusieurs tailles) utilisé par les raccourcis Windows."""
    from PySide6.QtGui import QImage
    images = [make_pixmap(s).toImage().convertToFormat(QImage.Format_ARGB32)
              for s in (16, 24, 32, 48, 256)]
    try:
        return _write_ico(path, images)
    except Exception:
        return make_pixmap(256).save(str(path), "ICO")


def _write_ico(path, images) -> bool:
    """Écrit un .ico multi-tailles (PNG à l'intérieur, format accepté depuis Windows Vista)."""
    import struct

    from PySide6.QtCore import QBuffer, QIODevice
    blobs = []
    for img in images:
        buf = QBuffer()
        buf.open(QIODevice.WriteOnly)
        img.save(buf, "PNG")
        blobs.append((img.width(), bytes(buf.data())))
    header = struct.pack("<HHH", 0, 1, len(blobs))
    offset = 6 + 16 * len(blobs)
    entries, data = b"", b""
    for size, png in blobs:
        dim = 0 if size >= 256 else size
        entries += struct.pack("<BBBBHHII", dim, dim, 0, 0, 1, 32, len(png), offset)
        offset += len(png)
        data += png
    with open(path, "wb") as f:
        f.write(header + entries + data)
    return True
