<#
    Baut Mutschiketten als Windows-Programm und packt es zur Weitergabe in ein ZIP.

    Gebaut wird mit Nuitka, nicht mit PyInstaller: Virenscanner (u. a. Norton)
    melden PyInstaller-Programme regelmaessig als Fehlerkennung, weil alle
    denselben Bootloader enthalten - erst recht als selbstentpackende
    "Onefile"-EXE. Nuitka uebersetzt den Python-Code nach C und erzeugt eine
    native EXE ohne diesen gemeinsamen Nenner. Ausgeliefert wird der
    Programmordner als ZIP, kein Installer und keine Onefile-EXE.
    (Erfahrungen aus dem Foto-Sortierer.)

    Aufruf:   .\build.ps1
    Ergebnis: dist\Mutschiketten\                        (Programmordner)
              dist\Mutschiketten-<version>-windows.zip   (zum Weitergeben)

    Die C-Uebersetzung dauert einige Minuten. Laeuft das Skript aus einer
    App im App-Container (z. B. der Claude-App) und scheitert der C-Compiler
    an einer fehlenden Header-Datei, vorher setzen:
        $env:NUITKA_CACHE_DIR = "C:\nuitka-cache"
#>

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

$python = ".\.venv\Scripts\python.exe"
if (-not (Test-Path $python)) {
    Write-Host "Keine virtuelle Umgebung gefunden - wird angelegt..." -ForegroundColor Yellow
    py -3.12 -m venv .venv
    if ($LASTEXITCODE -ne 0) { python -m venv .venv }
}
& $python -m pip install -q -r requirements.txt nuitka
if ($LASTEXITCODE -ne 0) { throw "Paketinstallation fehlgeschlagen." }

$version = (Select-String -Path studio\__init__.py -Pattern '^__version__ = "(.+)"').Matches[0].Groups[1].Value
Write-Host "`nMutschiketten $version" -ForegroundColor Cyan

Write-Host "`n[1/3] Uebersetze mit Nuitka (dauert einige Minuten)..." -ForegroundColor Cyan
Remove-Item -Recurse -Force build_nuitka -ErrorAction SilentlyContinue
& $python -m nuitka `
    --standalone `
    --assume-yes-for-downloads `
    --enable-plugin=pyside6 `
    --include-package=barcode `
    --include-package=studio `
    --include-package-data=studio `
    --windows-console-mode=disable `
    --windows-icon-from-ico=studio\mutschiketten.ico `
    --company-name="Juergen Mutscheller - mutschweb" `
    --product-name="Mutschiketten" `
    --file-version="$version.0" `
    --product-version="$version.0" `
    --file-description="Etiketten-Designer fuer Katasymbol / SUPVAN T50M Pro" `
    --copyright="(c) 2026 Juergen Mutscheller - MIT-Lizenz" `
    --output-dir=build_nuitka `
    --output-filename=Mutschiketten.exe `
    main.py
if ($LASTEXITCODE -ne 0) { throw "Nuitka-Build fehlgeschlagen." }

$ziel = "dist\Mutschiketten"
if (Test-Path $ziel) { Remove-Item -Recurse -Force $ziel }
New-Item -ItemType Directory -Force "dist" | Out-Null
Move-Item "build_nuitka\main.dist" $ziel

# Qt/PySide6 stehen unter der LGPL-3.0. Die verlangt, dass dem Programm eine
# Kopie des Lizenztextes beiliegt; die GPL-3.0 muss mit, weil die LGPL-3.0
# auf sie verweist. NOTICE nennt alle gebuendelten Bibliotheken.
Write-Host "`n[2/3] Lege Lizenztexte und Beispielvorlage bei..." -ForegroundColor Cyan
Copy-Item "LICENSE" "$ziel\LIZENZ.txt"
Copy-Item "NOTICE" "$ziel\NOTICE.txt"
New-Item -ItemType Directory "$ziel\Lizenzen" | Out-Null
Copy-Item "lizenzen\*.txt" "$ziel\Lizenzen\"
New-Item -ItemType Directory "$ziel\Vorlagen" | Out-Null
Copy-Item "Messvorlage_40x30.etikett" "$ziel\Vorlagen\"

$fehlend = @("Mutschiketten.exe", "LIZENZ.txt", "NOTICE.txt", "Lizenzen\LGPL-3.0.txt", "Lizenzen\GPL-3.0.txt") |
    Where-Object { -not (Test-Path (Join-Path $ziel $_)) }
if ($fehlend) { throw "Im Paket fehlt: $($fehlend -join ', ')" }

Write-Host "`n[3/3] Packe Programmordner fuer die Weitergabe..." -ForegroundColor Cyan
$paket = "dist\Mutschiketten-$version-windows.zip"
if (Test-Path $paket) { Remove-Item $paket }
Compress-Archive -Path $ziel -DestinationPath $paket

Write-Host "`nFertig: $paket" -ForegroundColor Green
Write-Host "Entpacken und Mutschiketten\Mutschiketten.exe starten." -ForegroundColor Green
