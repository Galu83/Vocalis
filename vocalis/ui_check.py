"""Cases à cocher et boutons d'option faciles à reconnaître.

Le style de Windows 11, mélangé à la feuille de style des paramètres, ne dessinait
qu'un petit ✓ nu (et rien du tout quand la case était décochée). Ici : un vrai carré
bordé, rempli de la couleur d'accent avec un ✓ blanc quand c'est coché, un retour
au survol, et toute la ligne est cliquable.
"""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QCheckBox, QRadioButton

TICK = (Path(__file__).parent / "images" / "coche.svg").as_posix()


def style(accent: str, accent_hover: str) -> str:
    """Règles à ajouter à la feuille de style d'une fenêtre claire."""
    return f"""
QCheckBox, QRadioButton {{ spacing: 9px; padding: 2px 0; }}
QCheckBox::indicator, QRadioButton::indicator {{
    width: 16px; height: 16px; background: white; border: 2px solid #8E8E96; }}
QCheckBox::indicator {{ border-radius: 5px; }}
QRadioButton::indicator {{ border-radius: 10px; }}
QCheckBox::indicator:hover, QRadioButton::indicator:hover {{
    border-color: {accent}; background: #FDECEC; }}
QCheckBox::indicator:checked {{ background: {accent}; border-color: {accent};
                                image: url("{TICK}"); }}
QCheckBox::indicator:checked:hover {{ background: {accent_hover};
                                      border-color: {accent_hover}; }}
QRadioButton::indicator:checked {{ background: white; border: 5px solid {accent};
                                   width: 10px; height: 10px; }}
QCheckBox:disabled, QRadioButton:disabled {{ color: #A0A0A5; }}
QCheckBox::indicator:disabled, QRadioButton::indicator:disabled {{
    background: #F2F2F5; border-color: #CFCFD4; }}
QCheckBox::indicator:checked:disabled {{ background: #CFCFD4; }}
"""


class CheckBox(QCheckBox):
    """Case à cocher dont toute la ligne est cliquable."""

    def __init__(self, text: str = "", parent=None):
        super().__init__(text, parent)
        self.setCursor(Qt.PointingHandCursor)

    def hitButton(self, pos) -> bool:
        return self.rect().contains(pos)


class RadioButton(QRadioButton):
    """Bouton d'option dont toute la ligne est cliquable."""

    def __init__(self, text: str = "", parent=None):
        super().__init__(text, parent)
        self.setCursor(Qt.PointingHandCursor)

    def hitButton(self, pos) -> bool:
        return self.rect().contains(pos)
