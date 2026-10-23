"""Dictionnaire personnel de Vocalis (fichier dictionnaire.json, à côté de config.toml).

Deux parties :
- « dictee » : les mots à écrire d'une certaine façon (noms, marques, termes
  techniques), avec, facultativement, ce que la reconnaissance entend à la place.
  Ils servent d'indice à Whisper et à la correction, puis les erreurs connues sont
  remplacées dans le texte final. Vocalis en apprend aussi tout seul quand on
  corrige un mot juste après une dictée.
- « prononciation » : comment la voix doit prononcer certains mots écrits
  (sigles, noms étrangers…), pour toutes les langues ou une seule.

Le fichier est relu automatiquement quand il change : pas besoin de redémarrer.
"""
from __future__ import annotations

import difflib
import json
import logging
import re
import threading
from pathlib import Path

from .config import ROOT

log = logging.getLogger(__name__)

PATH = ROOT / "dictionnaire.json"
MAX_PROMPT_CHARS = 600            # indice donné à Whisper (son contexte est limité)

_lock = threading.Lock()
_cache: dict = {"mtime": None, "data": None, "compiled": None}


# =========================================================================== fichier
def empty() -> dict:
    return {"dictee": [], "prononciation": [], "apprendre": True}


def _clean(data: dict) -> dict:
    out = empty()
    out["apprendre"] = bool(data.get("apprendre", True))
    for e in data.get("dictee", []) or []:
        mot = str(e.get("mot", "")).strip()
        if not mot:
            continue
        heard = e.get("entendu", [])
        if isinstance(heard, str):
            heard = heard.split(",")
        heard = [h.strip() for h in heard if h and h.strip() and h.strip() != mot]
        out["dictee"].append({"mot": mot, "entendu": list(dict.fromkeys(heard)),
                              "appris": bool(e.get("appris", False))})
    for e in data.get("prononciation", []) or []:
        ecrit, dit = str(e.get("ecrit", "")).strip(), str(e.get("dit", "")).strip()
        if ecrit and dit:
            out["prononciation"].append({"ecrit": ecrit, "dit": dit,
                                         "langue": str(e.get("langue", "") or "")})
    return out


def load(path: Path | None = None) -> dict:
    """Le dictionnaire (copie). Un ancien « vocabulaire » des réglages est repris
    la première fois."""
    path = path or PATH
    with _lock:
        try:
            mtime = path.stat().st_mtime_ns
        except OSError:
            mtime = None
        if path == PATH and _cache["data"] is not None and _cache["mtime"] == mtime:
            return json.loads(json.dumps(_cache["data"]))
        data = empty()
        if mtime is not None:
            try:
                data = _clean(json.loads(path.read_text(encoding="utf-8")))
            except Exception:
                log.exception("Dictionnaire illisible : %s", path)
        elif path == PATH:
            data = _from_old_vocabulary()
        if path == PATH:
            _cache.update(mtime=mtime, data=data, compiled=None)
        return json.loads(json.dumps(data))


def _from_old_vocabulary() -> dict:
    data = empty()
    try:
        from .config import load_config
        vocab = load_config()["dictee"].get("vocabulaire", "")
    except Exception:
        vocab = ""
    for w in [x.strip() for x in vocab.split(",") if x.strip()]:
        data["dictee"].append({"mot": w, "entendu": [], "appris": False})
    return data


def save(data: dict, path: Path | None = None) -> None:
    path = path or PATH
    data = _clean(data)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)
    with _lock:
        if path == PATH:
            _cache.update(mtime=None, data=None, compiled=None)


# =========================================================================== dictée
_EDGE = r"[^\w]"


def _phrase_regex(phrase: str) -> str:
    """Expression qui retrouve une suite de mots, espaces ou tirets variables."""
    parts = [re.escape(p) for p in re.split(r"[\s\-]+", phrase.strip()) if p]
    return r"(?<!\w)" + r"[\s\-]+".join(parts) + r"(?!\w)"


def _compiled() -> list[tuple[re.Pattern, str]]:
    data = load()
    with _lock:
        if _cache["compiled"] is not None:
            return _cache["compiled"]
    rules: list[tuple[int, re.Pattern, str]] = []
    for e in data["dictee"]:
        mot = e["mot"]
        sources = list(e["entendu"])
        if mot != mot.lower():
            sources.append(mot)            # « vocalis » -> « Vocalis »
        for src in sources:
            try:
                rules.append((len(src), re.compile(_phrase_regex(src), re.I), mot))
            except re.error:
                continue
    rules.sort(key=lambda r: -r[0])        # les plus longues d'abord
    out = [(p, m) for _, p, m in rules]
    with _lock:
        _cache["compiled"] = out
    return out


def apply_dictation(text: str) -> str:
    """Remplace les erreurs connues par l'orthographe du dictionnaire."""
    if not text:
        return text
    for pattern, mot in _compiled():
        def repl(m, mot=mot):
            start = m.start()
            before = m.string[:start].rstrip()
            sentence_start = not before or before[-1] in ".!?\n"
            if sentence_start and mot[:1].islower():
                return mot[:1].upper() + mot[1:]
            return mot
        text = pattern.sub(repl, text)
    return text


def vocabulary() -> list[str]:
    return [e["mot"] for e in load()["dictee"]]


def prompt_words() -> str:
    """Les mots du dictionnaire, en indice pour Whisper (longueur limitée)."""
    out = ""
    for w in vocabulary():
        nxt = f"{out}, {w}" if out else w
        if len(nxt) > MAX_PROMPT_CHARS:
            break
        out = nxt
    return out


# =========================================================================== apprentissage
def _core(word: str) -> str:
    return re.sub(rf"^{_EDGE}+|{_EDGE}+$", "", word)


def is_learnable(old: str, new: str) -> bool:
    """Une correction faite à la main ressemble-t-elle à un mot mal reconnu ?
    Prudent : on n'apprend que les noms, marques, sigles et mots techniques
    (majuscule, chiffre, symbole), ou les mots collés / décollés (« vocal is » ->
    « Vocalis »). Un simple changement de mot courant (maison -> raison) est une
    reformulation, pas une erreur de reconnaissance."""
    o, n = _core(old), _core(new)
    if not o or not n or o.lower() == n.lower() or len(n) < 2 or len(n) > 40:
        return False
    if n.isdigit() or len(n.split()) > 3:
        return False
    flat_o, flat_n = re.sub(r"[\s\-']", "", o.lower()), re.sub(r"[\s\-']", "", n.lower())
    ratio = difflib.SequenceMatcher(None, flat_o, flat_n).ratio()
    if ratio < 0.5:
        return False
    special = (any(c.isupper() for c in n[1:]) or any(c.isdigit() for c in n)
               or bool(re.search(r"[^\w\s\-']", n)))
    proper = n[:1].isupper() and not o[:1].isupper()
    joined = (" " in o) != (" " in n) and ratio >= 0.75
    return special or proper or joined


def learn(old: str, new: str) -> bool:
    """Ajoute « old » comme erreur connue de « new ». Renvoie vrai si c'est nouveau."""
    old, new = _core(old), _core(new)
    data = load()
    if not data.get("apprendre", True):
        return False
    for e in data["dictee"]:
        if e["mot"] == new:
            if old in e["entendu"]:
                return False
            e["entendu"].append(old)
            break
        if e["mot"].lower() == old.lower():
            return False                      # on ne contredit pas un mot choisi
    else:
        data["dictee"].append({"mot": new, "entendu": [old], "appris": True})
    save(data)
    log.info("Dictionnaire : « %s » appris (au lieu de « %s »)", new, old)
    return True


def _words(text: str) -> list[str]:
    return text.split()


def find_corrections(before: str, after: str, inserted: str) -> list[tuple[str, str]]:
    """Compare le texte de la case juste après le collage (before) et un peu plus
    tard (after) : renvoie les mots du texte dicté remplacés à la main."""
    norm = lambda s: re.sub(r"\s+", " ", s.replace("\r", "\n")).strip()  # noqa: E731
    before, after, inserted = norm(before), norm(after), norm(inserted)
    if not inserted or before == after:
        return []
    pos = before.rfind(inserted)
    if pos < 0:
        return []
    ws = len(_words(before[:pos]))
    we = ws + len(_words(inserted))
    a, b = _words(before), _words(after)
    out = []
    sm = difflib.SequenceMatcher(None, a, b, autojunk=False)
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag != "replace" or i1 < ws or i2 > we:
            continue
        if not (1 <= i2 - i1 <= 3 and 1 <= j2 - j1 <= 3):
            continue
        old, new = " ".join(a[i1:i2]), " ".join(b[j1:j2])
        if is_learnable(old, new):
            out.append((_core(old), _core(new)))
    return out


# =========================================================================== prononciation
_TOKEN = re.compile(r"^(\W*)(.*?)(\W*)$", re.S)


def _split_token(tok: str) -> tuple[str, str, str]:
    m = _TOKEN.match(tok)
    return (m.group(1), m.group(2), m.group(3)) if m else ("", tok, "")


def _pron_rules(lang: str) -> list[tuple[list[str], bool, list[str]]]:
    rules = []
    for e in load()["prononciation"]:
        if e["langue"] and e["langue"] != lang:
            continue
        src = [_core(w) for w in e["ecrit"].split()]
        src = [w for w in src if w]
        if not src:
            continue
        exact = all(w.isupper() and len(w) >= 2 for w in src)   # sigles : « US » ≠ « us »
        rules.append((src, exact, e["dit"].split()))
    rules.sort(key=lambda r: -len(r[0]))
    return rules


def pronounce(text: str, lang: str) -> tuple[str, list[int] | None]:
    """Texte à faire dire à la voix, et pour chaque mot du texte d'origine
    (séparé par des espaces) l'indice du mot prononcé correspondant.
    (texte, None) si rien n'a changé."""
    rules = _pron_rules(lang)
    if not rules or not text:
        return text, None
    toks = text.split()
    parts = [_split_token(t) for t in toks]
    out: list[str] = []
    mapping: list[int] = []
    i, changed = 0, False
    while i < len(toks):
        for src, exact, said in rules:
            n = len(src)
            if i + n > len(toks):
                continue
            cores = [p[1] for p in parts[i:i + n]]
            same = cores == src if exact else [c.lower() for c in cores] == [s.lower() for s in src]
            if not same:
                continue
            first = len(out)
            words = list(said)
            words[0] = parts[i][0] + words[0]
            words[-1] = words[-1] + parts[i + n - 1][2]
            out.extend(words)
            mapping.extend(first + min(k, len(words) - 1) for k in range(n))
            i += n
            changed = True
            break
        else:
            mapping.append(len(out))
            out.append(toks[i])
            i += 1
    if not changed:
        return text, None
    return " ".join(out), mapping
