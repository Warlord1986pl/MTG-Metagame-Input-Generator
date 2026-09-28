param(
    [string]$Format = "Modern",
    [int]$MetagameWindowDays = 14,
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
    "--rogue-threshold", "$RogueThreshold"
)

if ($IncludeChallengeDecklist) {
    $cliArgs += "--include-challenge-decklist"
}

& $pythonExe $cliArgs

if ($LASTEXITCODE -eq 0 -and $PublishSite) {
    & (Join-Path $PSScriptRoot "publish_site.ps1")
}
