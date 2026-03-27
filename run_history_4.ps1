param(
    [string]$Format = "Modern",
    [string]$MyDeck = "Domain Zoo",
    [int]$MetagameWindowDays = 14,
    [int]$MyWindowDays = 90,
    [int]$MyFallbackWindowDays = 180,
    [double]$RogueThreshold = 0.5,
    [string]$AnchorSunday = ""
)

Set-Location $PSScriptRoot

$pythonExe = "E:/github/.venv/Scripts/python.exe"
if (-not (Test-Path $pythonExe)) {
    $pythonExe = "python"
}

$args = @(
    "src/metagame_input_generator.py",
    "--format", $Format,
    "--history-points", "4",
    "--metagame-window-days", "$MetagameWindowDays",
    "--my-deck", $MyDeck,
    "--my-window-days", "$MyWindowDays",
    "--my-fallback-window-days", "$MyFallbackWindowDays",
    "--rogue-threshold", "$RogueThreshold"
)

if ($AnchorSunday -ne "") {
    $args += @("--anchor-sunday", $AnchorSunday)
}

& $pythonExe $args
