# Met à jour le radar : relève les campagnes puis refait le classement (RADAR.md, campagnes.csv).
$py = if ($env:RADAR_PYTHON) { $env:RADAR_PYTHON } else { "python" }
& $py -X utf8 (Join-Path $PSScriptRoot "collect.py")
if ($LASTEXITCODE -eq 0) { & $py -X utf8 (Join-Path $PSScriptRoot "classement.py") }
