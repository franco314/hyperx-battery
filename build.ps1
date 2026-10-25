<#
.SYNOPSIS
  Genera dist\HyperXBattery.exe (PyInstaller --onefile --noconsole).

.DESCRIPTION
  Usa el Python del venv (.venv). Si PyInstaller no está instalado lo instala.
  Uso:  .\build.ps1            (compila)
        .\build.ps1 -Test      (corre pytest antes de compilar)
#>
param(
    [switch]$Test
)

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

$py = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $py)) {
    throw "No encuentro $py. Crea el venv: python -m venv .venv ; .\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt"
}

& $py -c "import PyInstaller" 2>$null
if ($LASTEXITCODE -ne 0) {
    Write-Host "Instalando dependencias de desarrollo..."
    & $py -m pip install -r requirements-dev.txt
    if ($LASTEXITCODE -ne 0) { throw "pip falló" }
}

if ($Test) {
    & $py -m pytest
    if ($LASTEXITCODE -ne 0) { throw "Los tests fallaron; no se compila." }
}

# Ícono del .exe (mismo dibujo que la bandeja)
$icon = Join-Path $PSScriptRoot "build\HyperXBattery.ico"
& $py tools\make_icon.py $icon
if ($LASTEXITCODE -ne 0) { throw "No se pudo generar el ícono" }

# Un proceso anterior bloquearía dist\HyperXBattery.exe
Get-Process -Name HyperXBattery -ErrorAction SilentlyContinue | ForEach-Object {
    Write-Host "Cerrando HyperXBattery.exe en ejecución (PID $($_.Id))..."
    $_ | Stop-Process -Force
    $_.WaitForExit(5000) | Out-Null
}

& $py -m PyInstaller `
    --noconfirm --clean `
    --onefile --noconsole `
    --name HyperXBattery `
    --icon $icon `
    --distpath (Join-Path $PSScriptRoot "dist") `
    --workpath (Join-Path $PSScriptRoot "build\pyinstaller") `
    --specpath (Join-Path $PSScriptRoot "build") `
    --paths $PSScriptRoot `
    --collect-submodules pystray `
    run_tray.py
if ($LASTEXITCODE -ne 0) { throw "PyInstaller falló" }

$exe = Join-Path $PSScriptRoot "dist\HyperXBattery.exe"
if (-not (Test-Path $exe)) { throw "No se generó $exe" }
$size = [math]::Round((Get-Item $exe).Length / 1MB, 1)
Write-Host ""
Write-Host "Listo: $exe ($size MB)"
