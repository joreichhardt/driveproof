# DriveProof

[![Buy Me a Coffee](https://img.shields.io/badge/Buy%20Me%20a%20Coffee-support-ffdd00?logo=buymeacoffee&logoColor=000000)](https://buymeacoffee.com/joreichhardt)

`DriveProof` ist eine Linux- und NixOS-orientierte Alternative zu CrystalDiskInfo und GSmartControl fuer den strukturierten Check gebrauchter HDDs, SSDs und NVMe-Laufwerke vor dem Verkauf.

Der Fokus liegt nicht nur auf SMART-Anzeige, sondern auf einem glaubwuerdigen Verkaufs-Workflow:
- Laufwerke automatisch erkennen
- passende Tests je nach Laufwerkstyp anbieten
- mehrere Platten parallel pruefen
- laufende Tests nach Reload wiederfinden
- Berichte fuer Weiterverkauf erzeugen
- als NixOS-Live-USB direkt im Kiosk-Modus booten

## Ziel

Die App ist fuer Werkbank-, Lager- und Server-Szenarien gebaut, in denen mehrere Laufwerke nacheinander oder parallel geprueft werden sollen, ohne dass erst ein volles Desktop-System eingerichtet werden muss.

## Funktionen

- SMART-Auswertung ueber `smartctl`
- menschenlesbare SMART-Attributtabelle
- Gesundheits-Score mit Verkaufszusammenfassung
- automatische Erkennung von `HDD`, `SSD` und `NVMe`
- testtyp-abhaengige UI:
  - HDD: `Quick`, `Deep Sample`, `SMART Extended`, `Full Read`
  - SSD/NVMe: `Quick`, `SMART Short`, `SMART Extended`, `Full Read`
- parallele Jobs fuer mehrere Laufwerke
- persistente Job-Datenbank fuer Reloads und Neustarts
- Erkennung bereits extern gestarteter SMART-Selbsttests
- sicheres Entfernen
- optionale destruktive Loeschfunktionen mit Sicherheitsfreigaben
- druckbare Berichte im Browser
- NixOS-Live-Image mit automatischem App-Start und Chromium-Kiosk

## Lokale Ausfuehrung unter Ubuntu/Debian

Voraussetzungen:
- Python 3.11+
- `smartmontools`
- `util-linux`
- `udisks2`
- optional `hdparm` fuer ATA Secure Erase

Installation:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
sudo apt update
sudo apt install -y smartmontools udisks2 util-linux hdparm
```

Start:

```bash
sudo ./.venv/bin/python app.py
```

Dann im Browser:

```text
http://127.0.0.1:5055
```

## Typischer Ablauf

1. Laufwerke per USB-Dock oder direkt im Server anschliessen.
2. App mit Root-Rechten starten.
3. Laufwerk waehlen.
4. SMART-Daten und Uebersicht pruefen.
5. Passenden Test auswaehlen.
6. Laufende Jobs im rechten Bereich beobachten.
7. Bericht oeffnen und fuer den Verkauf ablegen oder drucken.

## Nix Build

Das Projekt enthaelt ein Nix-Setup fuer ein Live-ISO, das die App automatisch startet.

Voraussetzungen:
- funktionierendes `nix` mit Flakes

Build:

```bash
nix build .#iso
```

Ergebnis:

```text
./result/iso/driveproof-live.iso
```

## NixOS Live-USB

Das Live-Image ist fuer den direkten Einsatz auf Testsystemen gedacht:
- Boot vom USB-Stick
- automatischer Start der Flask-App
- automatischer Chromium-Start im Kiosk-Modus
- direkte Nutzung ohne lokale Installation

Das ist besonders sinnvoll, wenn in einem Server oder Testsystem mehrere interne Platten geprueft werden sollen.

## GitHub Release und fertiger Build

Ein fertiger ISO-Build kann grundsaetzlich als GitHub-Release bereitgestellt werden. Der aktuelle rohe ISO-Build ist hier aber groesser als `2 GiB` und damit fuer einen direkten GitHub-Release-Upload unpraktisch beziehungsweise ueber der typischen Asset-Grenze.

Praktische Optionen:
- komprimiertes Release-Artefakt pruefen, zum Beispiel `.iso.xz`
- externes Hosting fuer das rohe ISO nutzen
- nur den Source-Stand auf GitHub halten und den Build lokal oder per CI erzeugen

## Projektpositionierung

Das Projekt ist keine 1:1-Kopie von CrystalDiskInfo oder GSmartControl. Es ist eher eine auf Linux und NixOS zugeschnittene Resale- und Batch-Test-Oberflaeche fuer:
- SMART
- Laufwerkstests
- Verkaufsberichte
- Live-USB-Betrieb
- Mehrplatten-Szenarien

## Support

Wenn dir das Projekt nuetzt, kannst du es hier unterstuetzen:

- https://buymeacoffee.com/joreichhardt
