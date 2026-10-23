# ============================================================
#  Vocalis - désinstallation (Paramètres Windows > Applications > Vocalis)
# ============================================================
param([switch]$Confirmed, [string]$Root = '')
$ErrorActionPreference = 'Continue'
Add-Type -AssemblyName System.Windows.Forms

if (-not $Root) { $Root = Split-Path -Parent (Split-Path -Parent $PSScriptRoot) }

# Le script ne peut pas supprimer le dossier dans lequel il se trouve : il se
# recopie d'abord dans le dossier temporaire.
if (-not $Confirmed) {
    $answer = [System.Windows.Forms.MessageBox]::Show(
        "Désinstaller Vocalis ?`r`n`r`nTes réglages, les modèles de reconnaissance vocale et les voix téléchargées seront supprimés.",
        'Vocalis', 'YesNo', 'Question')
    if ($answer -ne 'Yes') { exit 0 }
    $copy = Join-Path $env:TEMP 'vocalis-desinstallation.ps1'
    Copy-Item -LiteralPath $PSCommandPath -Destination $copy -Force
    Start-Process powershell.exe -WindowStyle Hidden -WorkingDirectory $env:TEMP -ArgumentList `
        "-NoProfile -ExecutionPolicy Bypass -File `"$copy`" -Confirmed -Root `"$Root`""
    exit 0
}

if (-not $Root -or -not (Test-Path -LiteralPath (Join-Path $Root 'vocalis'))) { exit 1 }

# 1. Fermer Vocalis
Get-CimInstance Win32_Process -Filter "Name='pythonw.exe' OR Name='python.exe'" -ErrorAction SilentlyContinue |
    Where-Object { $_.CommandLine -and $_.CommandLine -like '*-m vocalis*' } |
    ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
Start-Sleep -Seconds 1

# 2. Raccourcis et entrée « Applications »
foreach ($p in @((Join-Path ([Environment]::GetFolderPath('Desktop')) 'Vocalis.lnk'),
                 (Join-Path ([Environment]::GetFolderPath('Programs')) 'Vocalis.lnk'),
                 (Join-Path ([Environment]::GetFolderPath('Startup')) 'Vocalis.lnk'))) {
    Remove-Item -LiteralPath $p -Force -ErrorAction SilentlyContinue
}
Remove-Item -Path 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\Vocalis' -Recurse -Force -ErrorAction SilentlyContinue

# 3. Dossier du programme
for ($try = 0; $try -lt 5; $try++) {
    Remove-Item -LiteralPath $Root -Recurse -Force -ErrorAction SilentlyContinue
    if (-not (Test-Path -LiteralPath $Root)) { break }
    Start-Sleep -Seconds 2
}
if (Test-Path -LiteralPath $Root) {
    [System.Windows.Forms.MessageBox]::Show(
        "Vocalis est désinstallé, mais certains fichiers n'ont pas pu être supprimés :`r`n$Root",
        'Vocalis', 'OK', 'Information') | Out-Null
} else {
    [System.Windows.Forms.MessageBox]::Show(
        "Vocalis a été désinstallé.`r`n`r`n(Ollama, utilisé pour la correction du texte, reste installé : tu peux le désinstaller séparément dans Paramètres > Applications.)",
        'Vocalis', 'OK', 'Information') | Out-Null
}
