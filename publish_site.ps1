<#
Syncs the generated season site (docs/index.html + docs/data/*.json) to the dedicated public
repo challenge-season-table, so the site keeps its own short URL
(warlord1986pl.github.io/challenge-season-table/) instead of living under this repo's name.

Only index.html and data/*.json are copied -- never the rest of docs/, which also holds this
repo's internal config CSVs (archetype_rules.csv, user_deck_mapping.csv, ...) that must not be
published to the public site repo.

Keeps a persistent local clone of the site repo at $SiteRepoPath (a sibling of this repo, not
nested inside it -- a git repo inside another git repo's working tree causes its own problems)
and reuses it on every call, so each run is a small incremental commit instead of a fresh clone.
#>
param(
    [string]$SiteRepoPath = "E:/github/challenge-season-table",
    [string]$SiteRepoUrl = "https://github.com/Warlord1986pl/challenge-season-table.git"
)

Set-Location $PSScriptRoot

$docsIndex = Join-Path $PSScriptRoot "docs/index.html"
$docsData = Join-Path $PSScriptRoot "docs/data"

if (-not (Test-Path $docsIndex) -or -not (Test-Path $docsData)) {
    Write-Host "[publish-site] docs/index.html or docs/data not found -- nothing to publish"
    exit 0
}

if (-not (Test-Path $SiteRepoPath)) {
    Write-Host "[publish-site] cloning $SiteRepoUrl into $SiteRepoPath"
    git clone $SiteRepoUrl $SiteRepoPath
    if ($LASTEXITCODE -ne 0) {
        Write-Host "[publish-site] ERROR: clone failed, aborting publish"
        exit 1
    }
}

Push-Location $SiteRepoPath
try {
    git pull --ff-only origin main
    if ($LASTEXITCODE -ne 0) {
        Write-Host "[publish-site] ERROR: pull failed, aborting publish (resolve manually in $SiteRepoPath)"
        exit 1
    }

    Copy-Item $docsIndex (Join-Path $SiteRepoPath "index.html") -Force
    New-Item -ItemType Directory -Force -Path (Join-Path $SiteRepoPath "data") | Out-Null
    Copy-Item (Join-Path $docsData "*.json") (Join-Path $SiteRepoPath "data") -Force

    git add index.html data
    $changed = git status --porcelain
    if ($changed) {
        git commit -m "Sync season site from MTG-Metagame-Input-Generator"
        git push origin main
        if ($LASTEXITCODE -eq 0) {
            Write-Host "[publish-site] published update to https://warlord1986pl.github.io/challenge-season-table/"
        } else {
            Write-Host "[publish-site] ERROR: push failed"
            exit 1
        }
    } else {
        Write-Host "[publish-site] no changes to publish"
    }
} finally {
    Pop-Location
}
