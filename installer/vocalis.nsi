; ============================================================
;  Vocalis - installateur Windows (NSIS)
;  Construit par installer/build.sh -> dist/Vocalis-Setup.exe
; ============================================================
Unicode true
SetCompressor /SOLID lzma
!include "MUI2.nsh"
!include "LogicLib.nsh"

!ifndef VERSION
  !define VERSION "0.0.0"
!endif
!ifndef PAYLOAD
  !define PAYLOAD "payload"
!endif

Name "Vocalis"
OutFile "${OUTFILE}"
InstallDir "$LOCALAPPDATA\Programs\Vocalis"
RequestExecutionLevel user
BrandingText "Vocalis ${VERSION}"
ShowInstDetails hide
VIProductVersion "${VERSION}.0"
VIAddVersionKey /LANG=1036 "ProductName" "Vocalis"
VIAddVersionKey /LANG=1036 "FileDescription" "Installation de Vocalis"
VIAddVersionKey /LANG=1036 "ProductVersion" "${VERSION}"
VIAddVersionKey /LANG=1036 "FileVersion" "${VERSION}"
VIAddVersionKey /LANG=1036 "LegalCopyright" "Vocalis"

!define MUI_ICON "vocalis.ico"
!define MUI_ABORTWARNING
!define MUI_ABORTWARNING_TEXT "Arrêter l'installation de Vocalis ?"
!define MUI_WELCOMEPAGE_TITLE "Bienvenue dans Vocalis"
!define MUI_WELCOMEPAGE_TEXT "Vocalis écrit ce que tu dis, n'importe où dans Windows, et lit à voix haute n'importe quel texte, dans plusieurs langues.$\r$\n$\r$\nL'installation télécharge les composants nécessaires (de 2 à 12 Go selon ton ordinateur). Compte 10 à 30 minutes selon ta connexion : tu peux faire autre chose pendant ce temps.$\r$\n$\r$\nClique sur « Installer » pour commencer."
!define MUI_TEXT_INSTALLING_TITLE "Installation de Vocalis"
!define MUI_TEXT_INSTALLING_SUBTITLE "Vocalis se prépare. Tu peux faire autre chose pendant ce temps."
!define MUI_FINISHPAGE_TITLE "Vocalis est installé"
!define MUI_FINISHPAGE_TEXT "Vocalis se trouve dans le menu Démarrer.$\r$\n$\r$\nUne fois lancé, Vocalis se place près de l'horloge. Clique sur son icône pour voir les raccourcis et les réglages."
!define MUI_FINISHPAGE_RUN
!define MUI_FINISHPAGE_RUN_TEXT "Lancer Vocalis maintenant"
!define MUI_FINISHPAGE_RUN_FUNCTION LaunchVocalis

!insertmacro MUI_PAGE_WELCOME
!define MUI_COMPONENTSPAGE_NODESC
!define MUI_PAGE_HEADER_TEXT "Options d'installation"
!define MUI_PAGE_HEADER_SUBTEXT "Choisis où placer les raccourcis de Vocalis."
!define MUI_COMPONENTSPAGE_TEXT_TOP "Vocalis est toujours ajouté au menu Démarrer. Coche les options voulues, puis clique sur « Installer »."
!define MUI_COMPONENTSPAGE_TEXT_COMPLIST "Options :"
!insertmacro MUI_PAGE_COMPONENTS
!define MUI_PAGE_CUSTOMFUNCTION_SHOW InstFilesShow
!insertmacro MUI_PAGE_INSTFILES
!insertmacro MUI_PAGE_FINISH
!insertmacro MUI_LANGUAGE "French"

Var Failed

Function InstFilesShow
  ; le détail technique reste replié (bouton « Afficher les détails »)
FunctionEnd

Function LaunchVocalis
  SetOutPath "$INSTDIR"
  Exec '"$INSTDIR\.venv\Scripts\pythonw.exe" -m vocalis'
FunctionEnd

; Exécute une étape d'installation (PowerShell, fenêtre cachée).
!macro STEP NUM TOTAL LABEL NAME CRITICAL
  SetDetailsPrint both
  DetailPrint "Étape ${NUM} sur ${TOTAL} : ${LABEL}"
  SetDetailsPrint listonly
  nsExec::ExecToLog 'powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "$INSTDIR\vocalis\scripts\install.ps1" -Step ${NAME}'
  Pop $0
  ${If} $0 != "0"
    ${If} ${CRITICAL} == 1
      SetDetailsPrint both
      DetailPrint "Échec : ${LABEL}"
      MessageBox MB_ICONSTOP "L'installation n'a pas pu se terminer (${LABEL}).$\r$\n$\r$\nVérifie ta connexion Internet, puis relance l'installation : ce qui est déjà téléchargé est conservé."
      StrCpy $Failed "1"
      Abort
    ${Else}
      DetailPrint "Attention : ${LABEL} n'a pas abouti (ce pourra être refait plus tard)."
    ${EndIf}
  ${EndIf}
!macroend

Section "Vocalis et menu Démarrer (obligatoire)" SecMain
  SectionIn RO
  SetShellVarContext current
  StrCpy $Failed "0"

  SetDetailsPrint both
  DetailPrint "Fermeture de Vocalis s'il est ouvert…"
  SetDetailsPrint listonly
  nsExec::Exec 'powershell.exe -NoProfile -WindowStyle Hidden -Command "Get-CimInstance Win32_Process -Filter \"Name=''pythonw.exe'' OR Name=''python.exe''\" | Where-Object { $$_.CommandLine -like ''*-m vocalis*'' } | ForEach-Object { Stop-Process -Id $$_.ProcessId -Force }"'
  Pop $0

  SetDetailsPrint both
  DetailPrint "Étape 1 sur 8 : copie de Vocalis"
  SetDetailsPrint listonly
  SetOutPath "$INSTDIR"
  RMDir /r "$INSTDIR\vocalis"
  File /r "${PAYLOAD}\*.*"

  !insertmacro STEP 2 8 "Python" python 1
  !insertmacro STEP 3 8 "composants de Vocalis" libs 1
  !insertmacro STEP 4 8 "reconnaissance vocale et voix" models 0
  !insertmacro STEP 5 8 "correction du texte" cleaner 0
  !insertmacro STEP 6 8 "assistant « Demander »" assistant 0
  !insertmacro STEP 7 8 "lecture d'écran (Windows va demander ton accord)" ocr 0
  !insertmacro STEP 8 8 "menu Démarrer" register 1

  SetDetailsPrint both
  DetailPrint "Vocalis est prêt."
SectionEnd

Section "Raccourci sur le Bureau" SecDesktop
  ${If} $Failed == "0"
    nsExec::Exec 'powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "$INSTDIR\vocalis\scripts\install.ps1" -Step desktop'
    Pop $0
  ${EndIf}
SectionEnd
