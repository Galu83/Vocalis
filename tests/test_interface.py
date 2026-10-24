"""Test « fumée » de l'interface (sans écran : QT_QPA_PLATFORM=offscreen) : les
fenêtres s'ouvrent, s'affichent et se dessinent sans planter."""
import pytest


@pytest.fixture
def settings_window(qapp, monkeypatch):
    from vocalis import settings_ui
    # Les vérifications de fond (Ollama, modèles, micro) ne sont pas lancées.
    monkeypatch.setattr(settings_ui, "run_bg", lambda *a, **k: None)
    monkeypatch.setattr(settings_ui, "input_devices", lambda: ["Micro de test"])
    win = settings_ui.SettingsWindow()
    yield win
    win._saved = True                  # pas de question « Enregistrer ? » à la fermeture
    win.close()
    win.deleteLater()


def test_fenetre_parametres(settings_window, tmp_path):
    win = settings_window
    assert win._collect() == win.original          # rien de changé à l'ouverture
    win.show()
    for name, index in win.page_index.items():
        win.nav.setCurrentRow(index)
        assert win.stack.currentIndex() == index
    for page in ("voix", "dictionnaire", "prononciation", "actions"):
        win.show_page(page)
    assert win.grab().save(str(tmp_path / "parametres.png"))
    assert (tmp_path / "parametres.png").stat().st_size > 0


def test_parametres_reprend_le_narrateur_choisi(write_config, qapp, monkeypatch):
    write_config('[lecture]\nvoix_fr = "edge:fr-CH-ArianeNeural"\n'
                 'voix_pt = "edge:pt-PT-RaquelNeural"\n')
    from vocalis import settings_ui
    monkeypatch.setattr(settings_ui, "run_bg", lambda *a, **k: None)
    monkeypatch.setattr(settings_ui, "input_devices", lambda: [])
    win = settings_ui.SettingsWindow()
    try:
        cfg = win._collect()
        assert cfg["lecture"]["voix_fr"] == "edge:fr-CH-ArianeNeural"
        assert cfg["lecture"]["voix_pt"] == "edge:pt-PT-RaquelNeural"
        # Ouvrir les Paramètres sans rien toucher ne change aucun réglage.
        assert cfg == win.original
    finally:
        win._saved = True
        win.close()
        win.deleteLater()


@pytest.mark.parametrize("mode", ["answer", "transform", "install"])
def test_carte_de_reponse(qapp, tmp_path, mode):
    from vocalis.ui_answer import AnswerCard
    card = AnswerCard("Ctrl + Alt + Maj + L")
    try:
        card.start("De quoi parle ce texte ?", mode, can_replace=True)
        card.text.setPlainText("Une réponse de test, assez longue pour passer à la ligne. " * 4)
        card.show()
        qapp.processEvents()
        assert card.grab().save(str(tmp_path / f"carte_{mode}.png"))
    finally:
        card.close()
        card.deleteLater()


def test_icone(qapp):
    from vocalis.icon import make_icon
    assert not make_icon().isNull()


def test_parametres_config_illisible_message_et_fichier_intact(write_config, qapp,
                                                                monkeypatch):
    path = write_config('[lecture]\nvoix_fr = "edge:fr-CH-ArianeNeural"\nvitesse = = 1\n')
    before = path.read_bytes()
    from vocalis import settings_ui
    monkeypatch.setattr(settings_ui, "run_bg", lambda *a, **k: None)
    monkeypatch.setattr(settings_ui, "input_devices", lambda: [])
    shown = []
    monkeypatch.setattr(settings_ui.QMessageBox, "critical",
                        lambda parent, title, text: shown.append(text))
    win = settings_ui.SettingsWindow()
    try:
        win.chk_live.setChecked(not win.chk_live.isChecked())
        win._save()
        assert path.read_bytes() == before
        assert shown and "abîmé" in shown[0]
    finally:
        win._saved = True
        win.close()
        win.deleteLater()
