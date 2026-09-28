param(
    [string]$Format = "Modern",
    [int]$MetagameWindowDays = 14,
    [double]$RogueThreshold = 0.5,
    [string]$AnchorSunday = "",
    [bool]$IncludeChallengeDecklist = $true
)

Set-Location $PSScriptRoot

$pythonExe = "E:/github/.venv/Scripts/python.exe"
if (-not (Test-Path $pythonExe)) {
    $pythonExe = "python"
}

$CmdLine = @(
    "src/metagame_input_generator.py",
    "--format", $Format,
    "--history-points", "18",
    "--metagame-window-days", "$MetagameWindowDays",
    "--rogue-threshold", "$RogueThreshold"
)

if ($AnchorSunday -ne "") {
    $CmdLine += @("--anchor-sunday", $AnchorSunday)
}

if ($IncludeChallengeDecklist) {
    $CmdLine += "--include-challenge-decklist"
}

& $pythonExe $CmdLine
