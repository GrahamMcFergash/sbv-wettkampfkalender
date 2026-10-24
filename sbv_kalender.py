#!/usr/bin/env python3
"""Liest den SBV-Wettkampfkalender (PDF) und erzeugt daraus einen abonnierbaren iCalendar-Feed (.ics).

Aufruf:  python sbv_kalender.py [--pdf datei.pdf] [--out docs/sbv_wettkampfkalender.ics]
Ohne --pdf wird das PDF von sachsenbogen.de geladen.

Das PDF ist eine optische Tabelle ohne Zellrahmen. Die Spalten werden deshalb über die
x-Position der Textstücke bestimmt (PyMuPDF); eine neue Zeile beginnt mit jedem Datum.
Die Ausgabe ist deterministisch (kein Erzeugungszeitpunkt), damit sich die Datei nur ändert,
wenn sich das PDF inhaltlich ändert.
"""
import argparse
import hashlib
import re
import sys
import urllib.request
from datetime import date, datetime, timedelta

import pymupdf

PDF_URL = "https://www.sachsenbogen.de/dbdata/wettkampf/sbv_wettkampfkalender.pdf"
KALENDER_NAME = "SBV Wettkampfkalender"
MIN_TERMINE = 20  # darunter ist das Layout vermutlich ein anderes -> nichts veröffentlichen

# Beginn der Spalten (x in PDF-Punkten): Ort | Wettkampf | AK | Ausrichter | Ranglistenturnier | Sterne berechtigt
SPALTEN = [("ort", 0), ("wettkampf", 226), ("ak", 468), ("ausrichter", 610), ("rang", 735), ("sterne", 785)]

# 14.02.2026 | 27.-29.03.2026 | 28./29.03.2026 | 31.10./01.11.2026
DATUM_RE = re.compile(r"^\d{1,2}\.(?:\d{2}\.)?\s*[-/]\s*\d{1,2}\.\d{2}\.\d{3,4}$|^\d{1,2}\.\d{2}\.\d{3,4}$")
BEREICH_RE = re.compile(r"^(\d{1,2})\.(?:(\d{2})\.)?\s*([-/])\s*(\d{1,2})\.(\d{2})\.(\d{3,4})$")
EINZEL_RE = re.compile(r"^(\d{1,2})\.(\d{2})\.(\d{3,4})$")
STAND_RE = re.compile(r"^\d{2}\.\d{2}\.\d{4}$")


def spalte(x):
    name = SPALTEN[0][0]
    for n, x0 in SPALTEN:
        if x >= x0 - 1:
            name = n
    return name


def jahr_korrigieren(jahr, kontext_jahr):
    """Tippfehler im PDF (z. B. '207' statt '2027') über das Jahr der Abschnittsüberschrift ausgleichen."""
    if 2000 <= jahr <= 2099:
        return jahr
    return kontext_jahr


def parse_datum(text, kontext_jahr, warnungen):
    """Gibt (von, bis) als date zurück oder None."""
    t = text.strip()
    m = BEREICH_RE.match(t)
    if m:
        t1, m1, trenner, t2, m2, jahr = m.groups()
        jahr = jahr_korrigieren(int(jahr), kontext_jahr)
        ende = date(jahr, int(m2), int(t2))
        if trenner == "/":
            # "28./29.03." = zwei aufeinanderfolgende Tage; offensichtliche Tippfehler (z. B. "1.10./01.11.") glätten
            beginn = ende - timedelta(days=1)
            if beginn.day != int(t1):
                warnungen.append(f"Datum '{t}' als {beginn:%d.%m.}-{ende:%d.%m.%Y} gelesen")
        else:
            beginn = date(jahr, int(m1 or m2), int(t1))
            if beginn > ende:  # Jahreswechsel
                beginn = date(jahr - 1, beginn.month, beginn.day)
        return beginn, ende
    m = EINZEL_RE.match(t)
    if m:
        tag, mon, jahr = m.groups()
        korrigiert = jahr_korrigieren(int(jahr), kontext_jahr)
        if korrigiert != int(jahr):
            warnungen.append(f"Jahr in '{t}' als {korrigiert} gelesen")
        d = date(korrigiert, int(mon), int(tag))
        return d, d
    return None


def spans_aus_pdf(pdf_bytes):
    """Liefert je Seite eine nach y/x sortierte Liste von (y, x, text)."""
    doc = pymupdf.open(stream=pdf_bytes, filetype="pdf")
    seiten = []
    for seite in doc:
        teile = []
        for block in seite.get_text("dict")["blocks"]:
            for zeile in block.get("lines", []):
                for span in zeile["spans"]:
                    text = span["text"].strip()
                    if text:
                        x0, y0, _, y1 = span["bbox"]
                        teile.append(((y0 + y1) / 2, x0, text))
        seiten.append(sorted(teile))
    return seiten


def zeilen_aus_spans(teile):
    """Fasst Textstücke mit gleicher y-Position zu Tabellenzeilen zusammen: {'termin', 'ort', ...}."""
    zeilen = []
    for y, x, text in teile:
        if zeilen and abs(zeilen[-1][0] - y) <= 3:
            zeilen[-1][1].append((x, text))
        else:
            zeilen.append([y, [(x, text)]])
    ergebnis = []
    for _, felder in zeilen:
        zellen = {}
        for x, text in sorted(felder):
            name = "termin" if DATUM_RE.match(text) and x < 130 else spalte(x)
            zellen.setdefault(name, []).append(text)
        ergebnis.append({k: " ".join(v) for k, v in zellen.items()})
    return ergebnis


def termine_lesen(pdf_bytes):
    """Gibt (termine, stand, warnungen) zurück."""
    termine, warnungen = [], []
    stand = None
    aktuell = None
    kontext_jahr = date.today().year
    ende = False
    for teile in spans_aus_pdf(pdf_bytes):
        if ende:
            break
        if stand is None:
            for _, _, text in teile:
                if STAND_RE.match(text):
                    stand = datetime.strptime(text, "%d.%m.%Y").date()
                    break
        for z in zeilen_aus_pdf_zeilen(teile):
            ort_kopf = z.get("ort", "")
            ueberschrift = re.match(r"^(Freilufttermine|Hallentermine|Vorschau)\s+(\d{4})$", ort_kopf.replace("\xa0", " ").strip())
            if ueberschrift:
                kontext_jahr = int(ueberschrift.group(2))
                aktuell = None
                if ueberschrift.group(1) == "Vorschau":
                    ende = True  # Vorschau-Termine haben keine konkreten Daten
                    break
                continue
            if z.get("ort") == "Ort" and z.get("wettkampf") == "Wettkampf":
                continue  # Tabellenkopf
            if "termin" in z:
                datum = parse_datum(z["termin"], kontext_jahr, warnungen)
                if datum is None:
                    warnungen.append(f"Datum nicht lesbar: '{z['termin']}'")
                    aktuell = None
                    continue
                aktuell = neuer_termin(datum, z)
                termine.append(aktuell)
            elif aktuell is not None and "ort" not in z and "wettkampf" in z and "ausrichter" in z:
                # Zeile ohne Datum, aber mit eigenem Ausrichter: weiterer Wettkampf am selben Tag
                neu = neuer_termin((aktuell["von"], aktuell["bis"]), z)
                neu["ort"] = aktuell["ort"]
                termine.append(neu)
                aktuell = neu
            elif aktuell is not None and "ort" not in z:
                # Folgezeile derselben Tabellenzeile (zweizeilige Zellen)
                if "wettkampf" in z:
                    aktuell["titel"].append(z["wettkampf"])
                if "ak" in z:
                    aktuell["ak"].append(z["ak"])
            elif "termin" not in z and "ak" in z and DATUM_RE.match(z["ak"].split()[0]) and "wettkampf" in z:
                # Info-Zeile ohne Terminspalte, Datum steht in der AK-Spalte (z. B. 'LM 3D des SSB')
                teile_ak = z["ak"].split(maxsplit=1)
                datum = parse_datum(teile_ak[0], kontext_jahr, warnungen)
                if datum:
                    z = dict(z, ort=teile_ak[1] if len(teile_ak) > 1 else "")
                    z.pop("ak")
                    aktuell = neuer_termin(datum, z)
                    termine.append(aktuell)
    for t in termine:
        t["ak"] = ak_bereinigen(t["ak"])
    ausrichter_ergaenzen(termine)
    return termine, stand, warnungen


def zeilen_aus_pdf_zeilen(teile):
    return zeilen_aus_spans(teile)


def ak_bereinigen(teile):
    """Einbuchstabige Reste (Überlagerungen im PDF) und verwaiste Kommas entfernen."""
    ergebnis = []
    for t in teile:
        t = re.sub(r"\s*,\s*$", "", t.strip())
        if len(t) > 1:
            ergebnis.append(t)
    return ergebnis


def ausrichter_ergaenzen(termine):
    """Im PDF abgeschnittene Ausrichter (z. B. 'BSC Chemnitz-Rabenst') anhand vollständiger Schreibweisen ergänzen."""
    bekannt = {t["ausrichter"] for t in termine}
    for t in termine:
        a = t["ausrichter"]
        laenger = [b for b in bekannt if len(b) > len(a) and b.startswith(a)]
        if a and len(a) >= 8 and len(laenger) == 1:
            t["ausrichter"] = laenger[0]


def neuer_termin(datum, z):
    von, bis = datum
    return {
        "von": von, "bis": bis,
        "ort": z.get("ort", ""),
        "titel": [z["wettkampf"]] if "wettkampf" in z else [],
        "ak": [z["ak"]] if "ak" in z else [],
        "ausrichter": z.get("ausrichter", ""),
        "rang": "rang" in z,
        "sterne": "sterne" in z,
    }


# ---------------------------------------------------------------- iCalendar

def ics_text(s):
    return s.replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\n", "\\n")


def falten(zeile):
    """Zeilen auf höchstens 75 Oktetts umbrechen (RFC 5545, 3.1)."""
    b = zeile.encode("utf-8")
    if len(b) <= 75:
        return zeile
    teile, aktuell = [], b""
    for ch in zeile:
        cb = ch.encode("utf-8")
        grenze = 75 if not teile else 74
        if len(aktuell) + len(cb) > grenze:
            teile.append(aktuell)
            aktuell = b""
        aktuell += cb
    teile.append(aktuell)
    return "\r\n ".join(t.decode("utf-8") for t in teile)


def ics_erzeugen(termine, stand):
    stempel = (stand or date.today()).strftime("%Y%m%dT000000Z")
    zeilen = [
        "BEGIN:VCALENDAR", "VERSION:2.0",
        "PRODID:-//turniertools//SBV Wettkampfkalender//DE",
        "CALSCALE:GREGORIAN", "METHOD:PUBLISH",
        f"X-WR-CALNAME:{ics_text(KALENDER_NAME)}",
        "X-WR-CALDESC:" + ics_text("Termine aus dem Wettkampfkalender des Sächsischen Bogensportverbands (PDF auf sachsenbogen.de). Ob ein Wettkampf stattfindet, steht in der Ausschreibung."),
        "X-WR-TIMEZONE:Europe/Berlin",
        "REFRESH-INTERVAL;VALUE=DURATION:PT12H", "X-PUBLISHED-TTL:PT12H",
    ]
    benutzt = {}
    for t in sorted(termine, key=lambda t: (t["von"], t["bis"], " ".join(t["titel"]))):
        titel = " – ".join(t["titel"]) or "Termin"
        schluessel = f"{t['von']}|{titel}|{t['ort']}"
        n = benutzt.get(schluessel, 0)
        benutzt[schluessel] = n + 1
        uid = hashlib.sha1((schluessel + f"|{n}").encode("utf-8")).hexdigest()[:20] + "@turniertools.sbv"
        beschreibung = []
        if t["ort"]:
            beschreibung.append(f"Ort: {t['ort']}")
        if t["ak"]:
            beschreibung.append("Klassen/Hinweis: " + ", ".join(t["ak"]))
        if t["ausrichter"]:
            beschreibung.append(f"Ausrichter: {t['ausrichter']}")
        if t["rang"]:
            beschreibung.append("Ranglistenturnier")
        if t["sterne"]:
            beschreibung.append("Sterne berechtigt")
        beschreibung.append("Quelle: Wettkampfkalender des SBV (Stand " + (stand.strftime("%d.%m.%Y") if stand else "unbekannt") + ")")
        zeilen += [
            "BEGIN:VEVENT",
            f"UID:{uid}",
            f"DTSTAMP:{stempel}",
            f"DTSTART;VALUE=DATE:{t['von']:%Y%m%d}",
            f"DTEND;VALUE=DATE:{t['bis'] + timedelta(days=1):%Y%m%d}",
            f"SUMMARY:{ics_text(titel)}",
        ]
        if t["ort"]:
            zeilen.append(f"LOCATION:{ics_text(t['ort'])}")
        zeilen += [
            f"DESCRIPTION:{ics_text(chr(10).join(beschreibung))}",
            "TRANSP:TRANSPARENT",
            "END:VEVENT",
        ]
    zeilen.append("END:VCALENDAR")
    return "\r\n".join(falten(z) for z in zeilen) + "\r\n"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pdf", help="lokale PDF-Datei statt Download")
    ap.add_argument("--out", default="docs/sbv_wettkampfkalender.ics")
    ap.add_argument("--liste", action="store_true", help="Termine als Tabelle ausgeben")
    args = ap.parse_args()

    if args.pdf:
        daten = open(args.pdf, "rb").read()
    else:
        req = urllib.request.Request(PDF_URL, headers={"User-Agent": "turniertools-sbv-kalender"})
        daten = urllib.request.urlopen(req, timeout=60).read()

    termine, stand, warnungen = termine_lesen(daten)
    for w in warnungen:
        print("Hinweis:", w, file=sys.stderr)
    if args.liste:
        for t in termine:
            print(t["von"], t["bis"], "|", t["ort"], "|", " / ".join(t["titel"]), "|", ", ".join(t["ak"]), "|", t["ausrichter"],
                  "|", "R" if t["rang"] else "-", "S" if t["sterne"] else "-")
    if len(termine) < MIN_TERMINE:
        sys.exit(f"Nur {len(termine)} Termine gefunden (erwartet mindestens {MIN_TERMINE}) – Layout geändert? Nichts geschrieben.")

    inhalt = ics_erzeugen(termine, stand)
    import os
    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    with open(args.out, "w", encoding="utf-8", newline="") as f:
        f.write(inhalt)
    print(f"{len(termine)} Termine (Stand {stand}) nach {args.out} geschrieben")


if __name__ == "__main__":
    main()
