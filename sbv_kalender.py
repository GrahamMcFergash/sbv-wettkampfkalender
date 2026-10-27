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
import html
import os
import re
import sys
import urllib.request
from datetime import date, datetime, timedelta

import pymupdf

PDF_URL = "https://www.sachsenbogen.de/dbdata/wettkampf/sbv_wettkampfkalender.pdf"
BASIS_URL = "https://www.sachsenbogen.de/"
# Fallback, falls der Menüpunkt "Ausschreibungen" auf der Startseite nicht gefunden wird
AUSSCHREIBUNGEN_URL = BASIS_URL + "index.php?page=0bd50be6ad79c15b3c08daaac3c24329"
SYMBOL_AUSSCHREIBUNG = "🏆"  # Pokal vor dem Titel, wenn eine Ausschreibung vorliegt
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


# ---------------------------------------------------------------- Ausschreibungen

STOPPWOERTER = {"und", "in", "der", "die", "das", "bei", "am", "im", "mit", "zur", "zum", "von", "dem", "des", "bei", "e", "v"}


def woerter(text):
    t = text.lower().replace("ä", "ae").replace("ö", "oe").replace("ü", "ue").replace("ß", "ss")
    # "2. Landesliga" (Kalender) und "Liga 2" (Ausschreibung) sollen dasselbe Wort ergeben
    t = re.sub(r"(\d+)\.\s*landesliga", r"landesliga\1", t)
    t = re.sub(r"\bliga\s*(\d+)", r"landesliga\1", t)
    return {w for w in re.findall(r"[a-z0-9]+", t) if w not in STOPPWOERTER}


def text_von_html(fragment):
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", fragment))).strip()


VEREIN_KUERZEL = {"Bogensportclub": "BSC", "Sportverein": "SV", "Sportclub": "SC", "Sportgemeinschaft": "SG"}


def verein_kurz(name):
    """'Bogensportclub Chemnitz-Rabenstein e.V.' -> 'BSC Chemnitz-Rabenstein' (Schreibweise wie im PDF-Kalender)."""
    name = re.sub(r"\s+e\.\s*V\.$", "", name.strip())
    for lang, kurz in VEREIN_KUERZEL.items():
        if name.startswith(lang + " "):
            return kurz + name[len(lang):]
    return name


def termine_aus_ausschreibungen(offen):
    """Ausschreibungen, die im PDF-Kalender fehlen, als eigene Termine aufnehmen."""
    termine = []
    for a in offen:
        termine.append({
            "von": a["von"], "bis": a["bis"], "ort": "",
            "titel": [a["titel"]], "ak": [],
            "ausrichter": verein_kurz(a["verein"]),
            "rang": False, "sterne": False,
            "ausschreibungen": [a] if a["pdf"] else [],
            "beginn": a["beginn"],
            "quelle": "Quelle: Seite Ausschreibungen des SBV (nicht im PDF-Wettkampfkalender)",
        })
    return termine


def seite_laden(url):
    req = urllib.request.Request(url, headers={"User-Agent": "turniertools-sbv-kalender"})
    return urllib.request.urlopen(req, timeout=60).read().decode("utf-8", errors="replace")


def ausschreibungen_url(warnungen):
    """Folgt dem Menüpunkt 'Ausschreibungen' der Startseite (die Seiten-ID kann sich ändern)."""
    try:
        m = re.search(r'href="(index\.php\?page=[0-9a-f]+)"[^>]*>\s*Ausschreibungen\s*<', seite_laden(BASIS_URL))
    except OSError as e:
        warnungen.append(f"Startseite nicht lesbar ({e}), nehme die bekannte Adresse der Ausschreibungen")
        return AUSSCHREIBUNGEN_URL
    if not m:
        warnungen.append("Menüpunkt 'Ausschreibungen' auf der Startseite nicht gefunden, nehme die bekannte Adresse")
        return AUSSCHREIBUNGEN_URL
    return BASIS_URL + m.group(1)


def ausschreibungen_lesen(seite, kontext_jahr, warnungen):
    """Liest die SBV-Seite 'Ausschreibungen': Liste von dict(von, bis, datum, beginn, titel, verein, pdf)."""
    if "Contest_Table" not in seite:
        raise ValueError("Tabelle der Ausschreibungen nicht gefunden – Seitenaufbau geändert?")
    ergebnis = []
    for zeile in re.findall(r"<tr>\s*(<td.*?)</tr>", seite, re.S):
        def feld(klasse):
            m = re.search(rf'class="{klasse}">(.*?)</p>', zeile, re.S)
            return text_von_html(m.group(1)) if m else ""
        datum_text = feld("Contest_VonBis")
        if not datum_text:
            continue  # Kopfzeile o. Ä.
        datum = parse_datum(datum_text, kontext_jahr, warnungen) if datum_text else None
        if datum is None:
            warnungen.append(f"Ausschreibung ohne lesbares Datum: '{datum_text}'")
            continue
        pdf = re.search(r'href="(dbdata/[^"]+\.pdf)"', zeile)
        ergebnis.append({
            "von": datum[0], "bis": datum[1], "datum": datum_text,
            "beginn": feld("Contest_Zeit").replace(" ", " "),
            "titel": feld("Contest_Headline"), "verein": feld("Contest_Autor"),
            "pdf": BASIS_URL + pdf.group(1) if pdf else None,
        })
    return ergebnis


def ausschreibungen_zuordnen(termine, ausschreibungen, warnungen):
    """Hängt Ausschreibungen mit PDF an den passenden Termin (Datum überlappt, bei mehreren Terminen entscheidet
    die Wortübereinstimmung von Titel/Verein). Gibt die nicht zuordenbaren Ausschreibungen zurück."""
    for t in termine:
        t["ausschreibungen"] = []
    offen = []
    for a in ausschreibungen:
        kandidaten = [t for t in termine if t["von"] <= a["bis"] and a["von"] <= t["bis"]]
        a_woerter = woerter(a["titel"] + " " + a["verein"])
        bewertet = sorted(
            ((len(a_woerter & woerter(" ".join(t["titel"]) + " " + t["ausrichter"] + " " + t["ort"])), i)
             for i, t in enumerate(kandidaten)), reverse=True)
        if not bewertet or bewertet[0][0] == 0 or (len(bewertet) > 1 and bewertet[0][0] == bewertet[1][0]):
            offen.append(a)
            continue
        if a["pdf"]:
            kandidaten[bewertet[0][1]]["ausschreibungen"].append(a)
    for t in termine:
        t["ausschreibungen"].sort(key=lambda a: a["von"])
    return offen


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


def ics_vorher_lesen(pfad):
    """Liest die bisherige .ics: UID -> (Hash des Inhalts, DTSTAMP, SEQUENCE), um geänderte Termine zu erkennen."""
    try:
        text = open(pfad, encoding="utf-8", newline="").read()
    except OSError:
        return {}
    text = re.sub(r"\r?\n ", "", text).replace("\r\n", "\n")
    vorher = {}
    for block in re.findall(r"BEGIN:VEVENT\n(.*?)\nEND:VEVENT", text, re.S):
        zeilen = block.split("\n")
        feld = {z.split(":", 1)[0]: z.split(":", 1)[1] for z in zeilen if ":" in z}
        inhalt = [z for z in zeilen if z.split(":", 1)[0] not in ("UID", "DTSTAMP", "LAST-MODIFIED", "SEQUENCE")]
        if "UID" in feld:
            # SEQUENCE -1 = Datei ohne Versionsangaben
            vorher[feld["UID"]] = (inhalt_hash(inhalt), feld.get("DTSTAMP", ""), int(feld.get("SEQUENCE", "-1")))
    return vorher


def inhalt_hash(zeilen):
    return hashlib.sha1("\n".join(zeilen).encode("utf-8")).hexdigest()


def ics_erzeugen(termine, stand, vorher=None):
    vorher = vorher or {}
    heute = date.today().strftime("%Y%m%dT000000Z")
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
        ausrichter = t["ausrichter"] if not t["ausrichter"].startswith("Meldung") else ""
        praefix = ausrichter or t["ort"]  # Schema: <Ausrichter>: <Turniername>, ohne Ausrichter der Ort
        anzeige = f"{praefix}: {titel}" if praefix else titel
        ausschreibungen = t.get("ausschreibungen", [])
        if ausschreibungen:
            anzeige = f"{SYMBOL_AUSSCHREIBUNG} {anzeige}"
        schluessel = f"{t['von']}|{titel}|{t['ort']}"
        n = benutzt.get(schluessel, 0)
        benutzt[schluessel] = n + 1
        uid = hashlib.sha1((schluessel + f"|{n}").encode("utf-8")).hexdigest()[:20] + "@turniertools.sbv"
        beschreibung = []
        mehrere = len(ausschreibungen) > 1
        for a in ausschreibungen:
            beschreibung.append(f"Ausschreibung{' ' + a['datum'] if mehrere else ''}: {a['pdf']}")
        beginn = t.get("beginn") or (ausschreibungen[0]["beginn"] if ausschreibungen else "")
        if beginn:
            beschreibung.append(f"Beginn: {beginn}")
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
        beschreibung.append(t.get("quelle") or "Quelle: Wettkampfkalender des SBV (PDF auf sachsenbogen.de)")
        ev = [
            f"DTSTART;VALUE=DATE:{t['von']:%Y%m%d}",
            f"DTEND;VALUE=DATE:{t['bis'] + timedelta(days=1):%Y%m%d}",
            f"SUMMARY:{ics_text(anzeige)}",
        ]
        if t["ort"]:
            ev.append(f"LOCATION:{ics_text(t['ort'])}")
        if ausschreibungen:
            ev.append(f"URL:{ausschreibungen[0]['pdf']}")
            ev += [f"ATTACH;FMTTYPE=application/pdf:{a['pdf']}" for a in ausschreibungen]
        ev += [
            f"DESCRIPTION:{ics_text(chr(10).join(beschreibung))}",
            "TRANSP:TRANSPARENT",
        ]
        # Version: unveränderte Termine behalten Stempel und Nummer, geänderte bekommen heute und SEQUENCE+1,
        # damit Kalenderprogramme die Änderung übernehmen
        hash_neu = inhalt_hash(ev)
        alt = vorher.get(uid)
        if alt and alt[2] >= 0 and alt[0] == hash_neu:
            stempel, folge = alt[1], alt[2]
        elif alt:
            stempel, folge = heute, max(alt[2], 0) + 1
        else:
            stempel, folge = heute, 0
        zeilen += ["BEGIN:VEVENT", f"UID:{uid}", f"DTSTAMP:{stempel}", f"LAST-MODIFIED:{stempel}", f"SEQUENCE:{folge}"] + ev + ["END:VEVENT"]
    zeilen.append("END:VCALENDAR")
    return "\r\n".join(falten(z) for z in zeilen) + "\r\n"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pdf", help="lokale PDF-Datei statt Download")
    ap.add_argument("--out", default="docs/sbv_wettkampfkalender.ics")
    ap.add_argument("--ausschreibungen", help="lokale HTML-Datei der Ausschreibungsseite statt Download")
    ap.add_argument("--liste", action="store_true", help="Termine als Tabelle ausgeben")
    args = ap.parse_args()

    if args.pdf:
        daten = open(args.pdf, "rb").read()
    else:
        req = urllib.request.Request(PDF_URL, headers={"User-Agent": "turniertools-sbv-kalender"})
        daten = urllib.request.urlopen(req, timeout=60).read()

    termine, stand, warnungen = termine_lesen(daten)

    if args.ausschreibungen:
        seite = open(args.ausschreibungen, "rb").read().decode("utf-8", errors="replace")
    else:
        seite = seite_laden(ausschreibungen_url(warnungen))
    ausschreibungen = ausschreibungen_lesen(seite, date.today().year, warnungen)
    offen = ausschreibungen_zuordnen(termine, ausschreibungen, warnungen)
    for a in offen:
        print(f"Hinweis: Ausschreibung ohne Termin im PDF-Kalender, als eigener Termin aufgenommen: "
              f"{a['datum']} {a['titel']} ({a['verein']}){'' if a['pdf'] else ' [ohne PDF]'}", file=sys.stderr)
    if len(termine) < MIN_TERMINE:
        sys.exit(f"Nur {len(termine)} Termine gefunden (erwartet mindestens {MIN_TERMINE}) – Layout geändert? Nichts geschrieben.")
    termine += termine_aus_ausschreibungen(offen)
    for w in warnungen:
        print("Hinweis:", w, file=sys.stderr)
    if args.liste:
        for t in termine:
            print(t["von"], t["bis"], "|", t["ort"], "|", " / ".join(t["titel"]), "|", ", ".join(t["ak"]), "|", t["ausrichter"],
                  "|", "R" if t["rang"] else "-", "S" if t["sterne"] else "-", "|", len(t["ausschreibungen"]), "Ausschreibung(en)")

    inhalt = ics_erzeugen(termine, stand, ics_vorher_lesen(args.out))
    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    with open(args.out, "w", encoding="utf-8", newline="") as f:
        f.write(inhalt)
    print(f"{len(termine)} Termine (Stand laut PDF {stand}, nur zur Info), davon {sum(1 for t in termine if t['ausschreibungen'])} mit Ausschreibung, nach {args.out} geschrieben")


if __name__ == "__main__":
    main()
