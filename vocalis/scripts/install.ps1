# ============================================================
#  Vocalis - étapes d'installation (appelées par l'installateur Vocalis-Setup.exe)
#  Chaque étape affiche des lignes simples ; code de sortie 0 = réussi.
#    -Step python   : Python 3.12 (téléchargé depuis python.org si absent)
#    -Step libs     : environnement et bibliothèques
#    -Step models   : reconnaissance vocale, voix, icône
#    -Step cleaner  : correction du texte (Ollama + Qwen, seulement avec une carte NVIDIA)
#    -Step ocr      : lecture d'écran pour les langues choisies (demande l'accord Windows)
#    -Step register : raccourcis et entrée « Applications » de Windows
# ============================================================
param([Parameter(Mandatory = $true)][string]$Step,
      [string]$Languages = 'fr,pt,en,de')

$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$env:PYTHONUTF8 = '1'
$env:PYTHONIOENCODING = 'utf-8'
$env:PIP_DISABLE_PIP_VERSION_CHECK = '1'
$env:HF_HUB_DISABLE_SYMLINKS_WARNING = '1'

$Root = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)     # dossier d'installation
$Venv = Join-Path $Root '.venv'
$VenvPy = Join-Path $Venv 'Scripts\python.exe'
$VenvPyw = Join-Path $Venv 'Scripts\pythonw.exe'
$Ico = Join-Path $Root 'vocalis.ico'
$Version = (Select-String -Path (Join-Path $Root 'vocalis\__init__.py') -Pattern '__version__ = "(.+)"').Matches[0].Groups[1].Value

function Say([string]$m) { Write-Output $m }

function Find-Python {
    $cands = @((Join-Path $env:LOCALAPPDATA 'Programs\Python\Python312\python.exe'),
               (Join-Path $env:ProgramFiles 'Python312\python.exe'))
    foreach ($c in $cands) { if (Test-Path -LiteralPath $c) { return $c } }
    foreach ($l in @((Join-Path $env:LOCALAPPDATA 'Programs\Python\Launcher\py.exe'),
                     (Join-Path $env:WINDIR 'py.exe'))) {
        if (-not (Test-Path -LiteralPath $l)) { continue }
        try {
            $out = & $l -3.12 -c "import sys; print(sys.executable)" 2>$null
            if ($LASTEXITCODE -eq 0 -and $out -and (Test-Path -LiteralPath ([string]$out).Trim())) {
                return ([string]$out).Trim()
            }
        } catch {}
    }
    return $null
}

function Has-NvidiaGpu {
    $smi = Get-Command nvidia-smi.exe -ErrorAction SilentlyContinue
    $path = if ($smi) { $smi.Source } else { Join-Path $env:WINDIR 'System32\nvidia-smi.exe' }
    if (-not (Test-Path -LiteralPath $path)) { return $false }
    try {
        $v = & $path --query-gpu=driver_version --format=csv,noheader 2>$null | Select-Object -First 1
        return ([int](([string]$v).Split('.')[0])) -ge 570
    } catch { return $false }
}

function Download([string]$url, [string]$dest) {
    [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
    $ProgressPreference = 'SilentlyContinue'
    Invoke-WebRequest -Uri $url -OutFile $dest -UseBasicParsing
}

function Run-Py([string[]]$arguments) {
    & $VenvPy @arguments 2>&1 | ForEach-Object { Say ([string]$_) }
    return $LASTEXITCODE
}

function New-Shortcut([string]$Path, [string]$Arguments, [string]$Description) {
    $shell = New-Object -ComObject WScript.Shell
    $s = $shell.CreateShortcut($Path)
    $s.TargetPath = $VenvPyw
    $s.Arguments = $Arguments
    $s.WorkingDirectory = $Root
    $s.Description = $Description
    if (Test-Path -LiteralPath $Ico) { $s.IconLocation = $Ico }
    $s.Save()
}

try {
    switch ($Step) {
        'python' {
            $py = Find-Python
            if ($py) { Say "Python 3.12 est déjà présent."; exit 0 }
            Say "Téléchargement de Python 3.12 (environ 25 Mo)…"
            $setup = Join-Path $env:TEMP 'python-3.12-vocalis.exe'
            Download 'https://www.python.org/ftp/python/3.12.10/python-3.12.10-amd64.exe' $setup
            Say "Installation de Python 3.12…"
            $p = Start-Process -FilePath $setup -Wait -PassThru -ArgumentList `
                '/quiet', 'InstallAllUsers=0', 'PrependPath=0', 'Include_launcher=1', `
                'Include_test=0', 'Include_doc=0', 'Shortcuts=0'
            Remove-Item -LiteralPath $setup -ErrorAction SilentlyContinue
            if (-not (Find-Python)) { throw "Python 3.12 n'a pas pu être installé (code $($p.ExitCode))." }
            Say "Python 3.12 installé."
        }
        'libs' {
            $py = Find-Python
            if (-not $py) { throw 'Python 3.12 introuvable.' }
            if (-not (Test-Path -LiteralPath $VenvPy)) {
                Say "Création de l'environnement de Vocalis…"
                & $py -m venv $Venv 2>&1 | ForEach-Object { Say ([string]$_) }
                if ($LASTEXITCODE -ne 0) { throw "Environnement impossible à créer." }
            }
            Say "Installation des composants (plusieurs centaines de Mo, patience)…"
            if ((Run-Py @('-m', 'pip', 'install', '--upgrade', 'pip', '--quiet')) -ne 0) { throw 'pip' }
            $req = @('-m', 'pip', 'install', '--progress-bar', 'off', '-r', (Join-Path $Root 'requirements.txt'))
            if (Has-NvidiaGpu) {
                Say "Carte graphique NVIDIA détectée : composants de calcul graphique en plus."
                $req += @('-r', (Join-Path $Root 'requirements-gpu.txt'))
            }
            if ((Run-Py $req) -ne 0) { throw "Les composants n'ont pas pu être installés." }
            Run-Py @('-m', 'vocalis.updater', '--marquer') | Out-Null
            Say "Composants installés."
        }
        'models' {
            Say "Préparation de la reconnaissance vocale et des voix…"
            $code = Run-Py @('-m', 'vocalis.setup_models', 'whisper', 'voix', 'micro', 'icone')
            if ($code -eq 1) { throw "Certains modèles n'ont pas pu être téléchargés." }
        }
        'cleaner' {
            if (-not (Has-NvidiaGpu)) {
                Say "Pas de carte NVIDIA : la correction du texte locale n'est pas installée (elle serait trop lente)."
                exit 0
            }
            $ollama = Get-Command ollama.exe -ErrorAction SilentlyContinue
            $path = Join-Path $env:LOCALAPPDATA 'Programs\Ollama\ollama.exe'
            if (-not $ollama -and -not (Test-Path -LiteralPath $path)) {
                Say "Téléchargement d'Ollama (moteur de correction)…"
                $setup = Join-Path $env:TEMP 'OllamaSetup.exe'
                Download 'https://ollama.com/download/OllamaSetup.exe' $setup
                Say "Installation d'Ollama…"
                Start-Process -FilePath $setup -Wait -ArgumentList '/VERYSILENT', '/NORESTART', '/SUPPRESSMSGBOXES' | Out-Null
                Remove-Item -LiteralPath $setup -ErrorAction SilentlyContinue
            }
            Say "Téléchargement du modèle de correction (environ 4,7 Go, une seule fois)…"
            $code = Run-Py @('-m', 'vocalis.setup_models', 'ollama')
            if ($code -eq 1) { throw "Le modèle de correction n'a pas pu être installé." }
        }
        'ocr' {
            $script = Join-Path $PSScriptRoot 'ocr.ps1'
            $log = Join-Path $env:TEMP 'vocalis-ocr.log'
            Remove-Item -LiteralPath $log -ErrorAction SilentlyContinue
            Say "Lecture d'écran : Windows va demander ton accord (bouton « Oui »)…"
            try {
                $p = Start-Process powershell.exe -Verb RunAs -Wait -PassThru -WindowStyle Hidden -ArgumentList `
                    "-NoProfile -ExecutionPolicy Bypass -File `"$script`" -Languages $Languages -LogPath `"$log`""
            } catch {
                Say "Accord refusé : la lecture d'écran pourra être ajoutée plus tard (Paramètres > Langues)."
                exit 0
            }
            if (Test-Path -LiteralPath $log) { Get-Content -LiteralPath $log -Encoding UTF8 | ForEach-Object { Say $_ } }
        }
        'register' {
            $desktop = Join-Path ([Environment]::GetFolderPath('Desktop')) 'Vocalis.lnk'
            $menu = Join-Path ([Environment]::GetFolderPath('Programs')) 'Vocalis.lnk'
            New-Shortcut $desktop '-m vocalis' 'Vocalis'
            New-Shortcut $menu '-m vocalis' 'Vocalis'
            $key = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\Vocalis'
            New-Item -Path $key -Force | Out-Null
            $uninstall = "powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$Root\vocalis\scripts\uninstall.ps1`""
            $values = @{
                DisplayName = 'Vocalis'; DisplayVersion = $Version; Publisher = 'Vocalis'
                DisplayIcon = $Ico; InstallLocation = $Root; UninstallString = $uninstall
                QuietUninstallString = $uninstall
            }
            foreach ($k in $values.Keys) { Set-ItemProperty -Path $key -Name $k -Value $values[$k] }
            Set-ItemProperty -Path $key -Name NoModify -Value 1 -Type DWord
            Set-ItemProperty -Path $key -Name NoRepair -Value 1 -Type DWord
            Say "Raccourci créé sur le Bureau ; Vocalis apparaît dans Paramètres > Applications."
        }
        default { throw "Étape inconnue : $Step" }
    }
    exit 0
} catch {
    Say "ERREUR : $($_.Exception.Message)"
    exit 1
}
