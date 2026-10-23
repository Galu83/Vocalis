# ============================================================
#  Passage de VoixLocale (dossier décompressé) à Vocalis installé
#  dans %LOCALAPPDATA%\Programs\Vocalis, sans rien retélécharger :
#  réglages, modèles et voix sont conservés.
# ============================================================
param([string]$Source = '', [switch]$Relaunched)
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing

if (-not $Source) { $Source = Split-Path -Parent (Split-Path -Parent $PSScriptRoot) }
$Target = Join-Path $env:LOCALAPPDATA 'Programs\Vocalis'

# Le dossier va être déplacé : on travaille depuis une copie du script.
if (-not $Relaunched) {
    $copy = Join-Path $env:TEMP 'vocalis-migration.ps1'
    Copy-Item -LiteralPath $PSCommandPath -Destination $copy -Force
    Start-Process powershell.exe -WorkingDirectory $env:TEMP -WindowStyle Hidden -ArgumentList `
        "-NoProfile -ExecutionPolicy Bypass -File `"$copy`" -Relaunched -Source `"$Source`""
    exit 0
}
Set-Location $env:TEMP

# --- petite fenêtre de progression
$form = New-Object System.Windows.Forms.Form
$form.Text = 'Vocalis'
$form.Size = New-Object System.Drawing.Size(460, 170)
$form.StartPosition = 'CenterScreen'
$form.FormBorderStyle = 'FixedDialog'
$form.MaximizeBox = $false
$form.TopMost = $true
$form.Font = New-Object System.Drawing.Font('Segoe UI', 10)
$label = New-Object System.Windows.Forms.Label
$label.Location = New-Object System.Drawing.Point(18, 18)
$label.Size = New-Object System.Drawing.Size(410, 44)
$label.Text = 'Installation de Vocalis…'
$bar = New-Object System.Windows.Forms.ProgressBar
$bar.Location = New-Object System.Drawing.Point(18, 70)
$bar.Size = New-Object System.Drawing.Size(410, 18)
$bar.Maximum = 6
$form.Controls.AddRange(@($label, $bar))
$form.Show()
function Step([int]$n, [string]$text) {
    $label.Text = $text; $bar.Value = $n
    [System.Windows.Forms.Application]::DoEvents()
}
function Fail([string]$text) {
    $form.Close()
    [System.Windows.Forms.MessageBox]::Show($text, 'Vocalis', 'OK', 'Warning') | Out-Null
    exit 1
}

try {
    Step 1 'Fermeture de VoixLocale…'
    Get-CimInstance Win32_Process -Filter "Name='pythonw.exe' OR Name='python.exe'" -ErrorAction SilentlyContinue |
        Where-Object { $_.CommandLine -and ($_.CommandLine -like '*-m voixlocale*' -or $_.CommandLine -like '*-m vocalis*') } |
        ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
    Start-Sleep -Seconds 2

    Step 2 'Déplacement vers le dossier des programmes…'
    if (-not (Test-Path -LiteralPath (Join-Path $Source 'vocalis\__init__.py'))) {
        Fail "La nouvelle version de Vocalis est introuvable dans :`r`n$Source"
    }
    if (Test-Path -LiteralPath $Target) {
        Fail "Vocalis est déjà installé ici :`r`n$Target`r`n`r`nRien n'a été modifié."
    }
    New-Item -ItemType Directory -Force -Path (Split-Path -Parent $Target) | Out-Null
    $moved = $false
    for ($i = 0; $i -lt 5 -and -not $moved; $i++) {
        try { Move-Item -LiteralPath $Source -Destination $Target; $moved = $true }
        catch { Start-Sleep -Seconds 2 }
    }
    if (-not $moved) {
        Fail "Le dossier VoixLocale est utilisé par un autre programme (une fenêtre de l'Explorateur ouverte dessus ?).`r`nFerme-la, puis relance « Passer à Vocalis »."
    }

    Step 3 'Nettoyage des anciens fichiers…'
    foreach ($old in @('voixlocale', 'Installer.bat', 'Lancer VoixLocale.bat', 'Parametres.bat',
                       'Arreter VoixLocale.bat', 'Mettre a jour.bat', 'lancer_debug.bat', 'lancer.bat',
                       'installer_console.bat', 'installer_ocr.bat', 'installer_ocr.ps1',
                       'installateur.ps1', 'demarrage_auto.bat', 'setup_modeles.py', 'LISEZMOI.md',
                       'journal_installation', 'Passer a Vocalis.cmd')) {
        $p = Join-Path $Target $old
        if (Test-Path -LiteralPath $p) { Remove-Item -LiteralPath $p -Recurse -Force -ErrorAction SilentlyContinue }
    }
    if (Test-Path -LiteralPath (Join-Path $Target 'voixlocale.ico')) {
        Move-Item -LiteralPath (Join-Path $Target 'voixlocale.ico') -Destination (Join-Path $Target 'vocalis.ico') -Force
    }
    Get-ChildItem -LiteralPath $Target -Filter 'voixlocale.log*' -ErrorAction SilentlyContinue | Remove-Item -Force
    $stamp = Join-Path $Target '.venv\voixlocale_bibliotheques.txt'
    if (Test-Path -LiteralPath $stamp) { Remove-Item -LiteralPath $stamp -Force }

    Step 4 'Raccourcis et entrée « Applications »…'
    $startupOld = Join-Path ([Environment]::GetFolderPath('Startup')) 'VoixLocale.lnk'
    $hadStartup = Test-Path -LiteralPath $startupOld
    foreach ($p in @((Join-Path ([Environment]::GetFolderPath('Desktop')) 'VoixLocale.lnk'),
                     (Join-Path ([Environment]::GetFolderPath('Programs')) 'VoixLocale.lnk'),
                     (Join-Path ([Environment]::GetFolderPath('Programs')) 'VoixLocale - Paramètres.lnk'),
                     $startupOld)) {
        Remove-Item -LiteralPath $p -Force -ErrorAction SilentlyContinue
    }
    & powershell.exe -NoProfile -ExecutionPolicy Bypass -File (Join-Path $Target 'vocalis\scripts\install.ps1') -Step register | Out-Null
    if ($hadStartup) {
        $shell = New-Object -ComObject WScript.Shell
        $s = $shell.CreateShortcut((Join-Path ([Environment]::GetFolderPath('Startup')) 'Vocalis.lnk'))
        $s.TargetPath = Join-Path $Target '.venv\Scripts\pythonw.exe'
        $s.Arguments = '-m vocalis'
        $s.WorkingDirectory = $Target
        $s.IconLocation = Join-Path $Target 'vocalis.ico'
        $s.Save()
    }

    Step 5 'Démarrage de Vocalis…'
    Start-Process -FilePath (Join-Path $Target '.venv\Scripts\pythonw.exe') -ArgumentList '-m vocalis' -WorkingDirectory $Target
    Step 6 'Terminé.'
    Start-Sleep -Milliseconds 600
    $form.Close()
    [System.Windows.Forms.MessageBox]::Show(
        "Vocalis est installé.`r`n`r`nUtilise désormais le raccourci « Vocalis » du Bureau. Il apparaît aussi dans Paramètres Windows > Applications.",
        'Vocalis', 'OK', 'Information') | Out-Null
} catch {
    Fail "Le passage à Vocalis s'est arrêté : $($_.Exception.Message)"
}
