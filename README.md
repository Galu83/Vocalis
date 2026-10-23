# Vocalis

**Dictée vocale, lecture à voix haute et traduction, partout dans Windows.**

Vocalis écrit ce que tu dis dans n'importe quelle application, et lit à voix haute n'importe quel texte à l'écran, en plusieurs langues (français, portugais, anglais, allemand, espagnol, italien, néerlandais, polonais).

## Installer

1. Télécharge **[Vocalis-Setup.exe](https://github.com/Galu83/Vocalis/raw/main/dist/Vocalis-Setup.exe)**.
2. Double-clique dessus. Si Windows affiche « Windows a protégé votre ordinateur », clique sur **Informations complémentaires** puis **Exécuter quand même**. Vocalis n'est pas encore signé numériquement.
3. Coche **Raccourci sur le Bureau** si tu en veux un, puis clique sur **Installer**. Vocalis télécharge ce dont il a besoin (de 2 à 12 Go selon ton ordinateur). Compte 10 à 30 minutes.
4. À la fin, Vocalis se trouve dans le **menu Démarrer**.

Vocalis se range près de l'horloge. Clique sur son icône pour voir les raccourcis, choisir le moteur de dictée et ouvrir les paramètres.

Les mises à jour sont proposées automatiquement au démarrage, avec un bouton « Mettre à jour maintenant ».

Pour désinstaller : **Paramètres Windows > Applications > Vocalis > Désinstaller**.

## Utilisation (touches par défaut)

| Touche | Action |
|---|---|
| **Ctrl + Alt + Maj + K** | Commencer une dictée ; la même touche la valide (le texte est corrigé puis inséré). La croix ✕ annule. |
| **Ctrl + Alt + Maj + J** | Lire à voix haute : le texte sélectionné, ou clique sur un paragraphe (ou trace un rectangle). |
| **Échap** | Arrêter la lecture. Pendant une lecture, un clic arrête, un double-clic reprend au mot cliqué. |

**Traduire :** pendant une dictée, clique sur une langue en bas de la fenêtre. Ce que tu dis est traduit. Si un texte était sélectionné et que tu ne parles pas, c'est lui qui est traduit et remplacé.

**Écouter dans une autre langue :** pendant une lecture, le badge de langue de la barre permet de choisir la langue d'écoute. Le texte est traduit si besoin, et ce choix est mémorisé.

## Moteurs de dictée

| Moteur | Pour qui |
|---|---|
| **Whisper** | PC avec carte graphique NVIDIA : le plus précis, tout reste sur le PC. |
| **Parakeet** | N'importe quel PC : rapide sur le processeur, tout reste sur le PC. |
| **Windows** (en ligne) | N'importe quel PC avec Internet : rien à télécharger. |

Par défaut, Vocalis choisit Whisper s'il y a une carte NVIDIA, sinon Parakeet. Pour changer, clique sur l'icône Vocalis.

## Ajouter une langue

Paramètres > Dictée > Langues : coche la langue, puis **Enregistrer**. Vocalis prépare tout seul la dictée, la traduction, les voix et la lecture d'écran, avec une barre de progression. Windows demande une fois ton accord pour la lecture d'écran. Vocalis redémarre ensuite.

## Confidentialité

- Avec Whisper ou Parakeet, la dictée reste sur ton PC. La correction du texte (Qwen, avec une carte NVIDIA) aussi.
- Les voix « en ligne » (Microsoft Edge), le moteur de dictée Windows et le traducteur de secours passent par Internet.
