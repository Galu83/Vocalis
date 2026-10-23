"""Catalogue des langues de Vocalis. Pour ajouter une langue, il suffit de la
déclarer ici : dictée, traduction, lecture, voix et lecture d'écran suivent."""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Lang:
    code: str
    name: str                 # en français (« Espagnol »)
    english: str              # pour les consignes de traduction
    edge_translate: str       # code du traducteur en ligne
    windows_speech: tuple[str, ...]          # variantes pour la dictée Windows
    edge_voices: tuple[tuple[str, str, str], ...] = field(default=())  # (clé, nom, genre)
    translate_rule: str = ""  # précision pour la traduction (ex. portugais du Portugal)


LANGS: dict[str, Lang] = {l.code: l for l in (
    Lang("fr", "Français", "French", "fr", ("fr-FR", "fr-CA", "fr-BE", "fr-CH")),
    Lang("pt", "Portugais", "European Portuguese (Portugal)", "pt-pt", ("pt-PT", "pt-BR"),
         translate_rule="Use European Portuguese spelling and phrasing (not Brazilian)."),
    Lang("en", "Anglais", "English", "en", ("en-US", "en-GB", "en-AU", "en-CA", "en-IN")),
    Lang("de", "Allemand", "German", "de", ("de-DE", "de-AT", "de-CH")),
    Lang("es", "Espagnol", "Spanish (Spain)", "es", ("es-ES", "es-MX", "es-US"),
         (("es-ES-ElviraNeural", "Elvira", "femme"), ("es-ES-AlvaroNeural", "Álvaro", "homme"),
          ("es-ES-XimenaNeural", "Ximena", "femme"), ("es-MX-DaliaNeural", "Dalia", "femme"),
          ("es-MX-JorgeNeural", "Jorge", "homme"))),
    Lang("it", "Italien", "Italian", "it", ("it-IT",),
         (("it-IT-ElsaNeural", "Elsa", "femme"), ("it-IT-IsabellaNeural", "Isabella", "femme"),
          ("it-IT-DiegoNeural", "Diego", "homme"), ("it-IT-GiuseppeMultilingualNeural",
                                                   "Giuseppe", "homme"))),
    Lang("nl", "Néerlandais", "Dutch", "nl", ("nl-NL", "nl-BE"),
         (("nl-NL-ColetteNeural", "Colette", "femme"), ("nl-NL-FennaNeural", "Fenna", "femme"),
          ("nl-NL-MaartenNeural", "Maarten", "homme"), ("nl-BE-DenaNeural", "Dena", "femme"))),
    Lang("pl", "Polonais", "Polish", "pl", ("pl-PL",),
         (("pl-PL-ZofiaNeural", "Zofia", "femme"), ("pl-PL-MarekNeural", "Marek", "homme"))),
)}

ALL = tuple(LANGS)                      # ordre d'affichage
SHORT = {c: c.upper() for c in ALL}


def name(code: str) -> str:
    return LANGS[code].name if code in LANGS else code


def lower_name(code: str) -> str:
    return name(code).lower()


def active(cfg: dict) -> tuple[str, ...]:
    """Langues choisies par la personne (dans l'ordre du catalogue)."""
    chosen = [c for c in cfg.get("dictee", {}).get("langues", []) if c in LANGS]
    return tuple(c for c in ALL if c in chosen) or ("fr",)
