"""Mises à jour : numéros de version, version.json, fichiers remplacés ou gardés
(GitHub est simulé : aucun téléchargement)."""
import io
import json
import re
import zipfile
from pathlib import Path

import pytest

from vocalis import __version__, updates

REPO = Path(__file__).resolve().parent.parent

SHA = "a" * 40


def test_parse():
    assert updates.parse("2.5.10") == (2, 5, 10)
    assert updates.parse("v2.6") == (2, 6)
    assert updates.parse("2.5.10") > updates.parse("2.5.9")       # pas un tri alphabétique
    assert updates.parse("2.10.0") > updates.parse("2.9.9")


def test_version_json_concorde_avec_le_programme():
    info = json.loads((REPO / "version.json").read_text(encoding="utf-8"))
    assert info["version"] == __version__
    assert info["branch"] == "main"
    assert info["notes"].strip().startswith("•")
    assert re.fullmatch(r"\d+\.\d+\.\d+", __version__)


def _fake_get(answers):
    def get(url, timeout, accept=""):
        for prefix, body in answers.items():
            if url.startswith(prefix):
                if isinstance(body, Exception):
                    raise body
                return body
        raise AssertionError(f"adresse inattendue : {url}")
    return get


def test_latest_lit_le_dernier_envoi(monkeypatch):
    monkeypatch.setattr(updates, "_get", _fake_get({
        updates.HEAD_URL: SHA.encode(),
        updates.VERSION_AT.format(ref=SHA): b'{"version": "9.0.0", "notes": "x"}',
    }))
    assert updates.latest() == {"version": "9.0.0", "notes": "x", "ref": SHA}
    assert updates.check()["version"] == "9.0.0"


def test_latest_sans_api_lecture_directe(monkeypatch):
    monkeypatch.setattr(updates, "_get", _fake_get({
        updates.HEAD_URL: OSError("limite atteinte"),
        updates.VERSION_URL: b'{"version": "1.0.0", "branch": "main"}',
    }))
    info = updates.latest()
    assert info["ref"] == "refs/heads/main"
    assert updates.check() is None                     # version plus ancienne


def test_check_meme_version(monkeypatch):
    monkeypatch.setattr(updates, "latest", lambda timeout=8: {"version": __version__})
    assert updates.check() is None


def test_installed_copy(isolated, monkeypatch):
    monkeypatch.setattr(updates, "ROOT", isolated)
    assert not updates.installed_copy()
    (isolated / ".venv").mkdir()
    assert updates.installed_copy()
    (isolated / ".git").mkdir()
    assert not updates.installed_copy()               # dossier de développement


# =========================================================================== installation
def _zip(files: dict[str, str]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for name, text in files.items():
            z.writestr(name, text)
    return buf.getvalue()


class _Download(io.BytesIO):
    headers = {}

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _install(monkeypatch, root, archive: bytes):
    monkeypatch.setattr(updates, "ROOT", root)
    seen = {}

    def urlopen(req, timeout=None):
        seen["url"] = req.full_url
        return _Download(archive)

    monkeypatch.setattr(updates.urllib.request, "urlopen", urlopen)
    steps = []
    updates.install({"version": "9.0.0", "ref": SHA}, lambda pct, msg: steps.append(pct))
    return seen, steps


def _old_install(root):
    for name, text in {"vocalis/__init__.py": "ancien", "vocalis/vieux.py": "x",
                       "requirements.txt": "ancien", "README.md": "ancien",
                       "config.toml": "[lecture]\nvoix_fr = \"edge:fr-CH-ArianeNeural\"\n",
                       "dictionnaire.json": "{}", "actions.json": "{}",
                       "modeles/m.bin": "modele", "voix/v.onnx": "voix",
                       ".venv/pyvenv.cfg": "venv"}.items():
        p = root / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")


def test_install_remplace_le_programme_et_garde_les_fichiers_perso(isolated, monkeypatch):
    _old_install(isolated)
    archive = _zip({f"Vocalis-{SHA}/vocalis/__init__.py": "nouveau",
                    f"Vocalis-{SHA}/requirements.txt": "nouveau",
                    f"Vocalis-{SHA}/README.md": "nouveau",
                    f"Vocalis-{SHA}/config.toml": "ne doit pas arriver",
                    f"Vocalis-{SHA}/tests/test_x.py": "ne doit pas arriver"})
    seen, steps = _install(monkeypatch, isolated, archive)
    assert seen["url"] == updates.ZIP_URL.format(ref=SHA)          # l'envoi précis
    assert steps[0] == 0 and steps[-1] == 100
    read = lambda n: (isolated / n).read_text(encoding="utf-8")    # noqa: E731
    assert read("vocalis/__init__.py") == "nouveau"
    assert not (isolated / "vocalis" / "vieux.py").exists()       # tout le dossier remplacé
    assert read("requirements.txt") == "nouveau"
    assert read("README.md") == "nouveau"
    # Fichiers de l'utilisateur intacts
    assert "Ariane" in read("config.toml")
    assert read("dictionnaire.json") == "{}" and read("actions.json") == "{}"
    assert read("modeles/m.bin") == "modele" and read("voix/v.onnx") == "voix"
    assert read(".venv/pyvenv.cfg") == "venv"
    assert not (isolated / "tests").exists()
    assert not (isolated / "_maj").exists()


def test_install_archive_invalide(isolated, monkeypatch):
    _old_install(isolated)
    with pytest.raises(RuntimeError):
        _install(monkeypatch, isolated, _zip({"autre/fichier.txt": "x"}))
    assert (isolated / "vocalis" / "__init__.py").read_text(encoding="utf-8") == "ancien"


def test_install_echec_retour_a_l_ancienne_version(isolated, monkeypatch):
    _old_install(isolated)
    real_move = updates.shutil.move

    def move(src, dst):
        if src.endswith("README.md") and "nouveau" in src:
            raise OSError("fichier verrouillé")
        return real_move(src, dst)

    monkeypatch.setattr(updates.shutil, "move", move)
    archive = _zip({"vocalis/__init__.py": "nouveau", "README.md": "nouveau"})
    with pytest.raises(OSError):
        _install(monkeypatch, isolated, archive)
    assert (isolated / "vocalis" / "__init__.py").read_text(encoding="utf-8") == "ancien"
    assert (isolated / "vocalis" / "vieux.py").exists()
    assert (isolated / "README.md").read_text(encoding="utf-8") == "ancien"


def test_liste_des_fichiers_remplaces():
    assert set(updates.REPLACED) == {"vocalis", "requirements.txt", "requirements-gpu.txt",
                                     "README.md"}
