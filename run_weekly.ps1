param(
    [string]$Format = "Modern",
    [string]$MyDeck = "Domain Zoo",
    [int]$MetagameWindowDays = 14,
    [int]$MyWindowDays = 90,
    [int]$MyFallbackWindowDays = 180,
    [double]$RogueThreshold = 0.5,
    [bool]$IncludeChallengeDecklist = $true,
    [bool]$PublishSite = $true
)

Set-Location $PSScriptRoot

$pythonExe = "E:/github/.venv/Scripts/python.exe"
if (-not (Test-Path $pythonExe)) {
    $pythonExe = "python"
}

$cliArgs = @(
    "src/metagame_input_generator.py",
    "--format", $Format,
    "--history-points", "1",
    "--metagame-window-days", "$MetagameWindowDays",
    "--my-deck", $MyDeck,
    "--my-window-days", "$MyWindowDays",
    "--my-fallback-window-days", "$MyFallbackWindowDays",
    "--rogue-threshold", "$RogueThreshold"
)

if ($IncludeChallengeDecklist) {
    $cliArgs += "--include-challenge-decklist"
}

& $pythonExe $cliArgs

if ($LASTEXITCODE -eq 0 -and $PublishSite) {
    & (Join-Path $PSScriptRoot "publish_site.ps1")
}
