<#
.SYNOPSIS
  Genera dist\HyperXBattery.exe (PyInstaller --onefile --noconsole) y, con -Installer,
  el instalador dist\HyperXBattery-Setup-<versión>.exe (Inno Setup 6).

.DESCRIPTION
  Usa el Python del venv (.venv). Si PyInstaller no está instalado lo instala.
  Uso:  .\build.ps1               (compila el .exe)
        .\build.ps1 -Test         (corre pytest antes de compilar)
        .\build.ps1 -Installer    (compila el .exe y luego el instalador)
  La versión del instalador sale de hyperx_battery\__init__.py (__version__).
#>
param(
    [switch]$Test,
    [switch]$Installer
)

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

$py = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $py)) {
    throw "No encuentro $py. Crea el venv: python -m venv .venv ; .\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt"
}

# Con -Installer se comprueba Inno Setup antes de empezar, para no fallar al final de la compilación.
$iscc = $null
if ($Installer) {
    $candidates = @(
        (Join-Path ${env:ProgramFiles(x86)} "Inno Setup 6\ISCC.exe"),
        (Join-Path $env:ProgramFiles "Inno Setup 6\ISCC.exe"),
        (Join-Path $env:LOCALAPPDATA "Programs\Inno Setup 6\ISCC.exe")
    )
    $iscc = $candidates | Where-Object { $_ -and (Test-Path $_) } | Select-Object -First 1
    if (-not $iscc) {
        $cmd = Get-Command ISCC.exe -ErrorAction SilentlyContinue
        if ($cmd) { $iscc = $cmd.Source }
    }
    if (-not $iscc) {
        throw ("No encuentro Inno Setup 6 (ISCC.exe), necesario para -Installer. Instálalo con:`n" +
               "  winget install --id JRSoftware.InnoSetup -e --accept-source-agreements --accept-package-agreements")
    }
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

if ($Installer) {
    # Una sola fuente de la versión: __version__ en hyperx_battery\__init__.py
    $initPy = Join-Path $PSScriptRoot "hyperx_battery\__init__.py"
    $m = Select-String -Path $initPy -Pattern '^__version__\s*=\s*"([^"]+)"' | Select-Object -First 1
    if (-not $m) { throw "No encuentro __version__ en $initPy" }
    $version = $m.Matches[0].Groups[1].Value

    Write-Host ""
    Write-Host "Compilando el instalador $version con $iscc ..."
    & $iscc "/DAppVersion=$version" (Join-Path $PSScriptRoot "installer\HyperXBattery.iss")
    if ($LASTEXITCODE -ne 0) { throw "Inno Setup falló (código $LASTEXITCODE)" }

    $setup = Join-Path $PSScriptRoot "dist\HyperXBattery-Setup-$version.exe"
    if (-not (Test-Path $setup)) { throw "No se generó $setup" }
    $setupSize = [math]::Round((Get-Item $setup).Length / 1MB, 1)
    Write-Host ""
    Write-Host "Listo: $setup ($setupSize MB)"
}
