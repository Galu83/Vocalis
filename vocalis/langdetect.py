"""Détection simple de la langue (français, portugais, anglais, allemand)."""
from __future__ import annotations

import re

_PT = set("""não nao é está estão são uma uns umas os as do da dos das no na nos nas
ao aos à às pelo pela pelos pelas com para por também mais muito muita como mas ou
eu ele ela eles elas você vocês isso isto este esta esse essa foi ser ter tem têm já
ainda quando onde porque então só seu sua seus suas nós vós lhe lhes há até sobre
entre depois antes agora sempre nunca aqui ali bem obrigado obrigada sim tudo nada
fazer pode podem deve muito hoje ontem amanhã cidade coisa coisas""".split())

_FR = set("""le les des du un une et est sont dans pour pas ne sur avec au aux ce cette
ces il ils elle elles je tu vous nous on son ses leur leurs mais plus très comme été
être avoir à y qui quoi dont où aussi quand alors donc car rien tout tous toute
toutes faire peut peuvent doit aujourd'hui hier demain ville chose choses c'est
qu'il n'est j'ai d'un d'une l'on cela ça voilà merci oui""".split())

_EN = set("""the and is are was were be been to of in that it for on with as at by this
these those i you he she we they my your his her our their not but or if so what which
who when where why how can could would should will just like have has had do does did
there here very really thanks thank yes no about from into than then them""".split())

_DE = set("""der die das und ist sind nicht ein eine einen dem den des mit für auf
auch als aber oder wenn wie wir ihr sie er es ich du sich noch nach bei aus zum zur
vom über unter heute morgen gestern sehr mehr kann können muss haben hat werden wird""".split())

_PT_PATTERNS = [r"ção\b", r"ções\b", r"ão\b", r"ões\b", r"õe", r"ã", r"\blh", r"nh", r"\bàs\b"]
_FR_PATTERNS = [r"eau", r"aux\b", r"\b[ldjnsqc]'", r"œ", r"è", r"ù", r"û", r"î", r"ï",
                r"aient\b", r"ait\b", r"oux\b", r"\bqu'"]


_ES = set("""el los las del al y es son está están una por para con como pero más muy
yo él ella nosotros ellos usted este esta ese esa eso hay fue ser tiene tienen también
cuando donde porque entonces sólo solo sobre entre después antes ahora siempre nunca aquí
hoy ayer mañana gracias sí nada todo hacer puede pueden debe ciudad cosa cosas país""".split())
_IT = set("""il lo gli della delle degli dello del che non sono una uno per con come
anche ma più molto io lui lei noi voi loro questo questa quello quella ci è sempre mai
oggi ieri domani grazie sì niente tutto fare può possono deve città cosa cose paese""".split())
_NL = set("""de het een en is zijn niet van in op met voor dat die dit ook maar of als
wij jij hij zij ik je we ze er bij naar uit om aan over nog heel wel geen meer kan
kunnen moet hebben heeft worden wordt vandaag morgen gisteren dank ja nee""".split())
_PL = set("""i w na z że się nie to jest są do od po za jak ale czy dla tak już tylko
ja ty on ona my wy oni jego jej ten ta te także bardzo może można musi mieć ma być
dzisiaj jutro wczoraj dziękuję tak nie wszystko""".split())

WORDS = {"fr": _FR, "pt": _PT, "en": _EN, "de": _DE, "es": _ES, "it": _IT, "nl": _NL,
         "pl": _PL}
PATTERNS = {
    "pt": (_PT_PATTERNS, 0.7),
    "fr": (_FR_PATTERNS, 0.7),
    "de": ([r"ß", r"\b\w+ung\b", r"\b\w+lich\b", r"sch"], 0.5),
    "es": ([r"ñ", r"ción\b", r"¿", r"¡"], 0.7),
    "pl": ([r"ł", r"ą", r"ę", r"ś", r"ż", r"ź", r"ć", r"ń"], 0.7),
    "nl": ([r"ij", r"\bvan\b", r"oe", r"aa"], 0.3),
}


def scores(text: str, allowed) -> dict[str, float]:
    t = text.lower().replace("’", "'")
    words = re.findall(r"[\w']+", t)
    out = {}
    for code in allowed:
        ws = WORDS.get(code)
        if ws is None:
            continue
        v = float(sum(1 for w in words if w in ws))
        pats, weight = PATTERNS.get(code, ((), 0))
        v += sum(len(re.findall(p, t)) for p in pats) * weight
        out[code] = v
    return out


def detect(text: str, default: str = "fr", allowed: tuple[str, ...] = ("fr", "pt")) -> str:
    sc = scores(text, allowed)
    if not sc:
        return default
    best = max(sc.values())
    winners = [k for k, v in sc.items() if v == best]
    if best == 0 or len(winners) > 1:
        return default if default in winners or best == 0 else winners[0]
    return winners[0]


def detect_any(text: str, default: str = "fr") -> str:
    """Langue du texte parmi toutes celles connues, ou « ?? » si aucune ne ressort."""
    words = re.findall(r"[\w'’]+", text.lower())
    if len(words) < 12:
        return detect(text, default, tuple(WORDS))
    sc = scores(text, tuple(WORDS))
    best = max(sc.values()) if sc else 0
    if best / len(words) < 0.04:
        return "??"
    return max(sc, key=sc.get)


def is_foreign(text: str, known: tuple[str, ...] = ("fr", "pt", "en", "de")) -> bool:
    """Texte dans une langue autre que celles de `known`."""
    lang = detect_any(text)
    return lang == "??" or lang not in known
