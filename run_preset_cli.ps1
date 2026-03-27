Set-Location $PSScriptRoot

$pythonExe = "E:/github/.venv/Scripts/python.exe"
if (-not (Test-Path $pythonExe)) {
    $pythonExe = "python"
}

& $pythonExe "src/preset_cli.py"
