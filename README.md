# Mutschiketten

Etiketten-Designer für den **Katasymbol / SUPVAN T50M Pro** unter Windows. Er druckt direkt über USB und braucht weder den SUPVAN Editor noch einen Treiber.

## Start

1. Python 3.10 oder neuer installieren (python.org, Haken bei „Add to PATH“ setzen).
2. Drucker einschalten und per USB-Kabel anschließen.
3. `start.bat` doppelklicken. Beim ersten Start wird eine `.venv` angelegt und die Pakete werden installiert, das dauert etwa eine Minute.

**Ohne Python:** Unter [Releases](https://github.com/mutschello/Mutschiketten/releases/latest) die Datei `Mutschiketten-<version>-windows.zip` herunterladen, mit Rechtsklick → „Alle extrahieren“ entpacken, am besten an einen dauerhaften Ort wie `Dokumente`, und im entpackten Ordner `Mutschiketten.exe` starten. Nicht direkt aus dem ZIP heraus starten.

Das Programm ist nicht digital signiert. Windows SmartScreen kann deshalb „Der Computer wurde durch Windows geschützt“ melden: „Weitere Informationen“ → „Trotzdem ausführen“.

**Tipp – Warnung ganz vermeiden:** Vor dem Entpacken mit Rechtsklick auf die ZIP → **Eigenschaften** → unten bei „Sicherheit“ den Haken **„Zulassen“** setzen → OK. Erst danach entpacken. Windows markiert heruntergeladene Dateien als „aus dem Internet“; „Zulassen“ entfernt diese Markierung für die ZIP und alles, was daraus entpackt wird.

Selbst bauen: `build.ps1` in PowerShell ausführen. Gebaut wird mit Nuitka statt PyInstaller und ausgeliefert als Programmordner im ZIP, weil Virenscanner wie Norton PyInstaller-Programme und selbstentpackende Dateien häufig fälschlich als Bedrohung melden.

## Funktionen

- **Elemente:** Text, Bild, QR-Code, Barcode (Code 128, EAN-13/8, Code 39, UPC-A, ITF, ISBN), Rechteck, Ellipse, Linie
- **Bearbeiten:**
  - Elemente per Drag & Drop verschieben; die Größe änderst du an der Ecke unten rechts
  - Magnetische Hilfslinien an Rändern, Mitte und anderen Elementen
  - Rückgängig/Wiederholen, Kopieren/Einfügen, Duplizieren, Ausrichten, Ebenen
- **Text:**
  - Jede installierte Windows-Schrift, z. B. Eurostile oder Arista
  - Fett, kursiv, unterstrichen, mehrzeilig
  - Automatisch verkleinern, bis der Text passt
  - Invertiert (weiß auf schwarz)
- **Bilder:**
  - Per Drag & Drop oder Strg+V einfügen
  - Rasterung für Fotos oder harte Schwelle für Logos
  - Helligkeit und Kontrast einstellbar
- **1-Bit-Vorschau (Strg+B):** zeigt Punkt für Punkt, was der Drucker druckt
- **Platzhalter:** `{datum}`, `{zeit}`, `{kw}`, `{nr}`, `{nr:3}` (ergibt 001) sowie jede CSV-Spalte als `{Spaltenname}`
- **Serien:**
  - Einzeln mit Kopien; die Anzahl steht direkt neben dem Drucken-Button
  - Nummeriert (Start, Schrittweite, Anzahl)
  - CSV-Liste mit Zeilenauswahl, z. B. `1-5, 8`. Eine Spalte „Anzahl“ legt die Stückzahl pro Zeile fest.
- **Drucker:**
  - Live-Status (Deckel offen, keine Rolle, Akku, Hitze)
  - Liest die Etikettengröße von der eingelegten Rolle
  - Druckdichte 0–15
- **Vorlagen:** werden als `.etikett` gespeichert (JSON, Bilder eingebettet). Export als PNG ist möglich.

## Erster Druck / Kalibrierung

Unter **Datei → Testdruck**:

- Kommt „▲ OBEN“ falsch herum heraus, im Reiter **Drucken** die Option **Ausgabe um 180° drehen** aktivieren.
- Sitzt der Druck versetzt, bei **Versatz X/Y** korrigieren (in Schritten von 0,125 mm = 1 Druckpunkt).
- Ist ein Querformat-Etikett gespiegelt bzw. um 180° verdreht, **Querformat im Uhrzeigersinn** umschalten.
- Werden lange Etiketten nur teilweise gedruckt, bei **Übertragung** auf „Kompatibel“ wechseln (siehe Technik).

## CSV-Beispiel (Preisschilder)

```
Name;Preis;Anzahl
Kissen Waldgeflüster;34,90 €;2
Tasche Blütenzauber;59,00 €;1
```

Im Etikett die Platzhalter `{Name}` und `{Preis}` verwenden. Dann im Reiter **Drucken** → **CSV-Liste** → **CSV laden** wählen und drucken. Excel-Exporte (Semikolon, Windows-1252) werden automatisch erkannt.

## Technik

- `studio/protocol.py`: Druckpuffer (4096 Byte), Spaltenformat, Blockbildung wie im Desktop-Tool des Herstellers
- `studio/lzma_raw.py`: eigener LZMA-Encoder **ohne Endmarker**. Laut heeen/supvan-cups verschluckt die Firmware sonst den letzten Puffer eines Blocks. Die Ausgabe wird in den Tests mit dem LZMA-Decoder von Python geprüft. Die Option „Kompatibel“ nutzt stattdessen die Standardbibliothek.
- `studio/printer.py`: HID-Kommunikation (VID 0x1820) und Statusabfragen
- `tests/test_core.py`: Kodierung und ein kompletter Druckjob gegen einen simulierten Drucker (`python -m pytest`)

Das Protokoll basiert auf den MIT-lizenzierten Projekten heeen/supvan-cups, zpan45/t50-windows-driver und KamilBakDev/katasymbol-t50m-print. Das Projekt hat keine Verbindung zu SUPVAN oder Katasymbol.

**Hinweis:** Der SUPVAN Editor darf nicht gleichzeitig laufen, weil er die USB-Verbindung belegt.

## Autor & Lizenz

© 2026 Jürgen Mutscheller – [mutschweb.de](https://mutschweb.de)

Veröffentlicht unter der [MIT-Lizenz](LICENSE). Hinweise zu den Projekten, auf denen das Druckerprotokoll beruht, stehen in [NOTICE](NOTICE). Fehler und Wünsche gern als Issue melden, Pull Requests sind willkommen.
