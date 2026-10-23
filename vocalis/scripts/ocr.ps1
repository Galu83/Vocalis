#  Vocalis - ajoute la lecture d'écran (OCR de Windows) pour des langues.
#  Lancé avec les droits administrateur (Windows demande l'accord).
#    -Languages fr,pt,en,de   codes de langue ; la variante préférée est essayée d'abord
param([string]$Languages = 'fr,pt,en,de', [string]$LogPath = '')

$Preferred = @{
    fr = @('fr-FR', 'fr-CA'); pt = @('pt-PT', 'pt-BR'); en = @('en-US', 'en-GB')
    de = @('de-DE'); es = @('es-ES', 'es-MX'); it = @('it-IT'); nl = @('nl-NL')
    pl = @('pl-PL'); sv = @('sv-SE'); da = @('da-DK'); nb = @('nb-NO'); fi = @('fi-FI')
    cs = @('cs-CZ'); ro = @('ro-RO'); hu = @('hu-HU'); el = @('el-GR'); tr = @('tr-TR')
    ru = @('ru-RU'); uk = @('uk-UA'); ja = @('ja-JP'); ko = @('ko-KR'); zh = @('zh-CN', 'zh-TW')
    ar = @('ar-SA')
}

function Log([string]$m) {
    Write-Host $m
    if ($LogPath) { Add-Content -LiteralPath $LogPath -Value $m -Encoding UTF8 }
}

try {
    $caps = Get-WindowsCapability -Online -ErrorAction Stop | Where-Object { $_.Name -like 'Language.OCR~~~*' }
} catch {
    Log "Impossible de consulter les modules de Windows : $($_.Exception.Message)"
    exit 1
}
$failed = $false
foreach ($code in ($Languages -split '[,; ]+' | Where-Object { $_ })) {
    $code = $code.ToLower()
    $tags = @()
    if ($Preferred.ContainsKey($code)) { $tags += $Preferred[$code] }
    $tags += ($caps | ForEach-Object { ($_.Name -split '~~~')[1] -replace '~.*$', '' } |
              Where-Object { $_ -like "$code-*" })
    $done = $false
    foreach ($tag in ($tags | Select-Object -Unique)) {
        $cap = $caps | Where-Object { $_.Name -like "Language.OCR~~~$tag~*" } | Select-Object -First 1
        if (-not $cap) { continue }
        if ($cap.State -eq 'Installed') {
            Log "Lecture d'écran ($tag) : déjà prête"
        } else {
            Log "Lecture d'écran ($tag) : installation en cours (quelques minutes)…"
            try {
                Add-WindowsCapability -Online -Name $cap.Name -ErrorAction Stop | Out-Null
                Log "Lecture d'écran ($tag) : installée"
            } catch {
                Log "Lecture d'écran ($tag) : échec ($($_.Exception.Message))"
                continue
            }
        }
        $done = $true
        break
    }
    if (-not $done) { Log "Lecture d'écran : langue « $code » non proposée par Windows"; $failed = $true }
}
if ($failed) { exit 2 }
exit 0
