"""Actions de « Demander » : des boutons qui appliquent une consigne au texte
sélectionné (fichier actions.json, à côté de config.toml).

Deux types :
- « repondre » : la réponse s'affiche dans la carte et elle est lue (Résumer…) ;
- « transformer » : le résultat remplace le texte sélectionné, après aperçu
  (Plus poli, Corriger…).

Mode avancé (facultatif, par action) : un prompt complet écrit à la main, des
instructions système, la créativité et le modèle. Variables possibles dans le
prompt : {texte}, {langue}, {question}.
"""
from __future__ import annotations

import difflib
import json
import logging
import re
import threading
import uuid
from pathlib import Path

from .config import ROOT

log = logging.getLogger(__name__)

PATH = ROOT / "actions.json"
_lock = threading.Lock()
_cache: dict = {"mtime": None, "data": None}

DEFAULTS = [
    ("resumer", "Résumer", "repondre", "Résume ce texte en quelques phrases.", True),
    ("expliquer", "Expliquer", "repondre",
     "Explique ce texte simplement, comme à quelqu'un qui découvre le sujet.", True),
    ("points", "Points clés", "repondre",
     "Donne les points clés de ce texte, trois à cinq au plus.", True),
    ("repondre", "Répondre", "repondre",
     "Rédige une réponse à ce message, prête à être envoyée, dans la langue du message. "
     "Donne seulement la réponse.", True),
    ("corriger", "Corriger", "transformer",
     "Corrige les fautes d'orthographe, de grammaire et de ponctuation, sans rien changer "
     "d'autre.", True),
    ("poli", "Plus poli", "transformer",
     "Rends ce texte plus poli et plus aimable, sans changer le sens.", True),
    ("court", "Plus court", "transformer",
     "Raccourcis ce texte en gardant toutes les informations importantes.", False),
    ("pro", "Ton professionnel", "transformer",
     "Réécris ce texte sur un ton professionnel et clair.", False),
    ("puces", "Liste à puces", "transformer",
     "Transforme ce texte en liste à puces claire, une idée par ligne.", False),
]

# Verbes qui transforment le texte (sinon : on répond)
TRANSFORM_VERBS = ("rends", "rendre", "corrige", "corriger", "traduis", "traduire", "réécris",
                   "réécrire", "reformule", "reformuler", "raccourcis", "raccourcir",
                   "transforme", "transformer", "mets", "mettre", "simplifie", "simplifier",
                   "allonge", "améliore", "améliorer", "change", "remplace", "ajoute",
                   "enlève", "supprime", "convertis", "adapte", "rewrite", "make", "fix",
                   "translate", "shorten", "rephrase", "corrige-le", "torna", "corrige",
                   "traduz", "reescreve")


def _default(key, name, kind, instruction, active) -> dict:
    return {"id": key, "nom": name, "type": kind, "consigne": instruction, "actif": active,
            "lire": kind == "repondre", "usages": 0, "expert": False, "prompt": "",
            "systeme": "", "creativite": 0.3, "modele": ""}


def _clean(a: dict) -> dict | None:
    name = str(a.get("nom", "")).strip()
    if not name:
        return None
    kind = a.get("type", "repondre")
    kind = kind if kind in ("repondre", "transformer") else "repondre"
    try:
        temp = float(a.get("creativite", 0.3))
    except (TypeError, ValueError):
        temp = 0.3
    return {"id": str(a.get("id") or uuid.uuid4().hex[:8]), "nom": name, "type": kind,
            "consigne": str(a.get("consigne", "")).strip(),
            "actif": bool(a.get("actif", True)),
            "lire": bool(a.get("lire", kind == "repondre")),
            "usages": int(a.get("usages", 0) or 0),
            "expert": bool(a.get("expert", False)),
            "prompt": str(a.get("prompt", "")),
            "systeme": str(a.get("systeme", "")),
            "creativite": max(0.0, min(1.5, temp)),
            "modele": str(a.get("modele", "")).strip()}


def load(path: Path | None = None) -> list[dict]:
    path = path or PATH
    with _lock:
        try:
            mtime = path.stat().st_mtime_ns
        except OSError:
            mtime = None
        if path == PATH and _cache["data"] is not None and _cache["mtime"] == mtime:
            return json.loads(json.dumps(_cache["data"]))
        if mtime is None:
            data = [_default(*d) for d in DEFAULTS]
        else:
            try:
                raw = json.loads(path.read_text(encoding="utf-8"))
                data = [x for x in (_clean(a) for a in raw.get("actions", [])) if x]
            except Exception:
                log.exception("Actions illisibles : %s", path)
                data = [_default(*d) for d in DEFAULTS]
        if path == PATH:
            _cache.update(mtime=mtime, data=data)
        return json.loads(json.dumps(data))


def save(actions: list[dict], path: Path | None = None) -> None:
    path = path or PATH
    data = [x for x in (_clean(a) for a in actions) if x]
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps({"actions": data}, ensure_ascii=False, indent=2),
                   encoding="utf-8")
    tmp.replace(path)
    with _lock:
        if path == PATH:
            _cache.update(mtime=None, data=None)


def get(action_id: str) -> dict | None:
    return next((a for a in load() if a["id"] == action_id), None)


def visible(limit: int = 5) -> tuple[list[dict], list[dict]]:
    """(boutons affichés, autres actions actives) : les plus utilisées d'abord."""
    acts = [a for a in load() if a["actif"]]
    order = {a["id"]: i for i, a in enumerate(acts)}
    acts.sort(key=lambda a: (-a["usages"], order[a["id"]]))
    return acts[:limit], acts[limit:]


def count_use(action_id: str) -> None:
    acts = load()
    for a in acts:
        if a["id"] == action_id:
            a["usages"] += 1
            save(acts)
            return


# =========================================================================== création
def guess_type(text: str) -> str:
    first = re.findall(r"[\wÀ-ÿ'-]+", text.lower())[:3]
    return "transformer" if any(w in TRANSFORM_VERBS for w in first) else "repondre"


def suggest_name(question: str) -> str:
    words = re.sub(r"[.!?]+$", "", question.strip()).split()
    name = " ".join(words[:5])
    if len(words) > 5:
        name += "…"
    return name[:1].upper() + name[1:]


def add_from_question(name: str, question: str, kind: str) -> dict:
    acts = load()
    a = _clean({"nom": name, "consigne": question, "type": kind, "actif": True,
                "lire": kind == "repondre", "usages": 1})
    acts.append(a)
    save(acts)
    log.info("Nouvelle action : %s (%s)", name, kind)
    return a


# =========================================================================== voix
def _norm(s: str) -> str:
    return re.sub(r"[^\w ]", "", s.lower()).strip()


def match_spoken(text: str) -> dict | None:
    """La phrase dite est-elle le nom d'une action (« plus poli ») ?"""
    said = _norm(text)
    if not said or len(said.split()) > 6:
        return None
    best, score = None, 0.0
    for a in load():
        if not a["actif"]:
            continue
        r = difflib.SequenceMatcher(None, said, _norm(a["nom"])).ratio()
        if r > score:
            best, score = a, r
    return best if score >= 0.85 else None


# =========================================================================== messages
TRANSFORM_SYSTEM = """You rewrite a text selected by the user, following an instruction.
Rules:
- Output ONLY the rewritten text: no introduction, no comments, no quotes, no Markdown \
unless the instruction asks for a list.
- Keep the language of the text, unless the instruction asks for another language.
- Keep the meaning, names, numbers and line breaks unless the instruction says otherwise."""


def build_messages(action: dict, context: str, lang_name: str, question: str = "",
                   system_default: str = "") -> tuple[list[dict], float, str]:
    """(messages, créativité, modèle) pour une action."""
    temp = float(action.get("creativite", 0.3))
    model = action.get("modele", "")
    if action.get("expert") and action.get("prompt", "").strip():
        prompt = action["prompt"]
        values = {"texte": context, "langue": lang_name, "question": question}
        has_text = "{texte}" in prompt
        for k, v in values.items():
            prompt = prompt.replace("{" + k + "}", v)
        if context and not has_text:
            prompt += f"\n\n<text>\n{context}\n</text>"
        msgs = []
        if action.get("systeme", "").strip():
            msgs.append({"role": "system", "content": action["systeme"].strip()})
        msgs.append({"role": "user", "content": prompt})
        return msgs, temp, model
    instruction = action.get("consigne", "").strip() or action["nom"]
    if len(instruction) > 180 or instruction.count("\n") >= 2:
        # Consigne longue (un rôle, des règles…) : ce sont des instructions, et le texte
        # sélectionné est ce qu'il faut traiter (pas une question sur la consigne).
        system = (instruction + "\n\nApply these instructions to the text given by the "
                  "user. Output only the final result, ready to use: no introduction, no "
                  "comment, no quotes.")
        user = f"<text>\n{context}\n</text>"
        if question:
            user += f"\n\n{question}"
        return ([{"role": "system", "content": system},
                 {"role": "user", "content": user}], temp, model)
    if question:
        instruction += " " + question
    if action["type"] == "transformer":
        return ([{"role": "system", "content": TRANSFORM_SYSTEM},
                 {"role": "user", "content":
                  f"<text>\n{context}\n</text>\n\nInstruction: {instruction}"}], temp, model)
    return ([{"role": "system", "content": system_default},
             {"role": "user", "content": f"<text>\n{context}\n</text>\n\nQuestion: "
                                         f"{instruction}"}], temp, model)
