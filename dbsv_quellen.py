"""Termine des DBSV (Deutscher Bogensportverband, dbsv1959.de) lesen.

Zwei Quellen, beide nur die DBSV-eigenen Termine:
- Seite "Sport -> Wettbewerbe": Deutsche Meisterschaften, Verbandspokale, Bundesliga mit Datum, Ort und den Links zu den
  Ausschreibungen (die maßgebliche Quelle für Datum, Titel und Ausschreibungs-PDFs)
- Turnierkalender-PDF (verlinkt auf "Termine -> Wettbewerbe 2026"): zusätzlich Meldeschlüsse, Sitzungen, Schulungen und
  den Ausrichter vor Ort. Darin sind die DBSV-eigenen Zeilen farbig (rot/blau), Veranstaltungen von Vereinen und anderen
  Landesverbänden schwarz.

Die Ergebnisse haben dieselbe Form wie die SBV-Termine in sbv_kalender.py.
"""
import html
import re
import urllib.parse
import urllib.request

import pymupdf

BASIS_URL = "https://www.dbsv1959.de"
WETTBEWERBE_URL = BASIS_URL + "/index.php/sport-3/wettbewerbe.html"
KALENDER_SEITE_URL = BASIS_URL + "/index.php/termine-3/wettbewerbe-2021.html"  # verlinkt das aktuelle Turnierkalender-PDF
VERANSTALTER = "DBSV"
QUELLE = "Quelle: DBSV (dbsv1959.de)"
MIN_SEITE = 8   # darunter ist der Seitenaufbau vermutlich ein anderer
MIN_KALENDER = 15

DISZIPLINEN = {"logofitaxs": "Scheibe", "logobulixs": "Bundesliga", "logo3dxs": "3D", "logofeldxs": "Feld/Wald", "logobolaxs": "Bogenlauf"}
LAENDER = {"thüringen": "TH", "baden-württemberg": "BW", "sachsen-anhalt": "ST", "hessen": "HE", "nordrhein": "NW", "niedersachsen": "NI",
           "brandenburg": "BB", "bayern": "BY", "mecklenburg-vorpommern": "MV", "rheinland-pfalz": "RP", "hamburg": "HH", "berlin": "BE",
           "schleswig-holstein": "SH", "bremen": "HB", "saarland": "SL"}
MONATE = {"januar": 1, "februar": 2, "märz": 3, "april": 4, "mai": 5, "juni": 6, "juli": 7, "august": 8,
          "september": 9, "oktober": 10, "november": 11, "dezember": 12}


def laden(url):
    req = urllib.request.Request(url, headers={"User-Agent": "turniertools-sbv-kalender"})
    return urllib.request.urlopen(req, timeout=60).read()


def text_von_html(fragment):
    ersetzt = re.sub(r"</div>|<br\s*/?>|</p>", "\n", fragment)
    sauber = html.unescape(re.sub(r"<[^>]+>", " ", ersetzt)).replace("\xa0", " ")
    zeilen = [re.sub(r"\s+", " ", z).strip() for z in sauber.split("\n")]
    return [z for z in zeilen if z]


STOPPWOERTER = {"und", "der", "die", "das", "fuer", "bis", "dt", "ue", "in", "im", "mit", "zur", "zum"}


def woerter(text):
    t = text.lower().replace("ä", "ae").replace("ö", "oe").replace("ü", "ue").replace("ß", "ss")
    return {w for w in re.findall(r"[a-z0-9]+", t) if len(w) > 1 and w not in STOPPWOERTER}


def ueberlappt(a, b):
    return a["von"] <= b["bis"] and b["von"] <= a["bis"]


ORT_KORREKTUREN = {"Oberaua": "Oberaula"}  # Tippfehler in der Quelle


def ort_mit_land(stadt, land):
    """'Jena', 'Thüringen' -> 'Jena (TH)'; Sachsen bleibt ohne Zusatz (wie im SBV-Kalender)."""
    stadt = ORT_KORREKTUREN.get(stadt, stadt)
    land = land.strip()
    if not land or land.lower() == "sachsen":
        return stadt
    schluessel = next((k for k in LAENDER if land.lower().startswith(k)), None)
    return f"{stadt} ({LAENDER[schluessel]})" if schluessel else f"{stadt} ({land})"


def neuer_termin(von, bis, titel, ort, ausschreibungen, disziplin="", vor_ort=""):
    return {
        "von": von, "bis": bis, "ort": ort, "titel": [titel], "ak": [],
        "ausrichter": VERANSTALTER, "rang": False, "sterne": False,
        "ausschreibungen": ausschreibungen, "quelle": QUELLE,
        "disziplin": disziplin, "vor_ort": vor_ort,
    }


# ---------------------------------------------------------------- Seite "Wettbewerbe"

def datum_mit_jahr(text):
    """'20.- 21.02.2027', '04.-05.09.2027', '06.02.2027' -> (von, bis) oder None."""
    from datetime import date
    t = re.sub(r"\s+", "", text)
    m = re.match(r"^(\d{1,2})\.(?:(\d{2})\.)?[-/](\d{1,2})\.(\d{2})\.(\d{4})$", t)
    if m:
        t1, m1, t2, m2, jahr = m.groups()
        return date(int(jahr), int(m1 or m2), int(t1)), date(int(jahr), int(m2), int(t2))
    m = re.match(r"^(\d{1,2})\.(\d{2})\.(\d{4})$", t)
    if m:
        d = date(int(m.group(3)), int(m.group(2)), int(m.group(1)))
        return d, d
    return None


def seite_lesen(seite, warnungen):
    """Wettbewerbe-Seite -> Liste von Terminen (mit Ausschreibungs-Links)."""
    anfang = seite.find('itemprop="articleBody"')
    if anfang < 0:
        raise ValueError("DBSV-Wettbewerbe-Seite: Inhaltsbereich nicht gefunden – Seitenaufbau geändert?")
    termine = []
    for zeile in re.findall(r"<tr.*?</tr>", seite[anfang:], re.S):
        zellen = re.findall(r"<td.*?</td>", zeile, re.S)
        if len(zellen) < 2:
            continue
        zeilen = text_von_html(zellen[1])
        if not zeilen:
            continue
        m = re.match(r"^(.+?\d{4})\s*,\s*(.+)$", zeilen[-1])
        datum = datum_mit_jahr(m.group(1)) if m else None
        if datum is None:
            continue  # Kopfzeile ('Wettbewerbe') oder Leerzeile
        titel = " ".join(zeilen[:-1])
        stadt, _, land = m.group(2).partition("/")
        icon = re.search(r"/grafiken/(\w+)\.", zellen[0])
        ausschreibungen = []
        for zelle in zellen[2:]:
            for href, bezeichnung in re.findall(r'<a [^>]*href="([^"]+)"[^>]*>(.*?)</a>', zelle, re.S):
                name = " ".join(text_von_html(bezeichnung))
                if "ausschreib" not in re.sub(r"\s+", "", name).lower():
                    continue
                zusatz = re.sub(r"(?i)ausschreibu\s*ng|aktualisierte|[!]", "", name).strip()
                url = urllib.parse.urljoin(BASIS_URL + "/", href)
                url = urllib.parse.quote(url, safe=":/%?=&")
                ausschreibungen.append({"datum": zusatz, "pdf": url, "beginn": ""})
        termine.append(neuer_termin(datum[0], datum[1], titel, ort_mit_land(stadt.strip(), land), ausschreibungen,
                                    disziplin=DISZIPLINEN.get(icon.group(1), "") if icon else ""))
    if len(termine) < MIN_SEITE:
        raise ValueError(f"DBSV-Wettbewerbe-Seite: nur {len(termine)} Termine gefunden (erwartet mindestens {MIN_SEITE})")
    return termine


# ---------------------------------------------------------------- Turnierkalender (PDF)

PDF_BEREICH = re.compile(r"^(\d{1,2})\.(?:/(\d{1,2})\.)?(\d{2})\.$")
PDF_MONATSWECHSEL = re.compile(r"^(\d{1,2})\.(\d{2})\./(\d{1,2})\.(\d{2})\.$")
PDF_MONATSNAME = re.compile(r"^(\d{1,2})\.([A-Za-zäÄ]+)$")
KOPF_RE = re.compile(r"^(Turnierkalender|- Turnierkalender|Stand\b|\d+$)")


def pdf_datum(text):
    """Gibt (tag_von, monat_von, tag_bis, monat_bis) zurück; das Jahr ergibt sich aus der Reihenfolge der Monate."""
    t = re.sub(r"\s+", "", text)
    m = PDF_MONATSWECHSEL.match(t)
    if m:
        return int(m.group(1)), int(m.group(2)), int(m.group(3)), int(m.group(4))
    m = PDF_BEREICH.match(t)
    if m:
        tag2 = int(m.group(2) or m.group(1))
        return int(m.group(1)), int(m.group(3)), tag2, int(m.group(3))
    m = PDF_MONATSNAME.match(t)
    if m and m.group(2).lower() in MONATE:
        monat = MONATE[m.group(2).lower()]
        return int(m.group(1)), monat, int(m.group(1)), monat
    return None


def kalender_lesen(pdf_bytes, warnungen):
    """Turnierkalender-PDF -> nur die DBSV-eigenen (farbigen) Zeilen als Termine."""
    from datetime import date
    doc = pymupdf.open(stream=pdf_bytes, filetype="pdf")
    kopf = doc[0].get_text()
    saison = re.search(r"(20\d{2})\s*/\s*(20\d{2})", kopf)
    if not saison:
        raise ValueError("DBSV-Turnierkalender: Saison ('2026/2027') nicht gefunden – Layout geändert?")
    jahr = int(saison.group(1))
    letzter_monat = 0
    eigene, bekannt = [], set()
    letzte = None
    for seite in doc:
        spans = []
        for block in seite.get_text("dict")["blocks"]:
            for zeile in block.get("lines", []):
                for s in zeile["spans"]:
                    if s["text"].strip():
                        spans.append(((s["bbox"][1] + s["bbox"][3]) / 2, s["bbox"][0], s["text"].strip(), s["color"]))
        spans.sort()
        zeilen = []
        for y, x, t, c in spans:
            if zeilen and abs(zeilen[-1][0] - y) <= 3:
                zeilen[-1][1].append((x, t, c))
            else:
                zeilen.append([y, [(x, t, c)]])
        for _, felder in zeilen:
            felder.sort()
            spalten = {"datum": [], "titel": [], "club": [], "ort": [], "land": []}
            for x, t, c in felder:
                name = "datum" if x < 130 else "titel" if x < 300 else "club" if x < 445 else "ort" if x < 525 else "land"
                spalten[name].append((t, c))
            texte = {k: " ".join(t for t, _ in v) for k, v in spalten.items()}
            if len(felder) == 1 and felder[0][1].lower() in MONATE:
                letzte = None
                continue  # Monatsüberschrift
            datum = pdf_datum(texte["datum"]) if texte["datum"] else None
            if datum is None:
                # Folgezeile eines umbrochenen Titels
                if letzte is not None and texte["titel"] and not texte["datum"] and not KOPF_RE.match(texte["titel"]):
                    letzte["titel"][0] += " " + texte["titel"]
                continue
            t1, m1, t2, m2 = datum
            if m1 < letzter_monat:
                jahr += 1
            letzter_monat = m1
            try:
                von = date(jahr, m1, t1)
                bis = date(jahr + (1 if m2 < m1 else 0), m2, t2)
            except ValueError:
                warnungen.append(f"DBSV-Turnierkalender: Datum '{texte['datum']}' nicht lesbar")
                letzte = None
                continue
            farbe = spalten["titel"][0][1] if spalten["titel"] else 0
            ist_dbsv = farbe != 0 or texte["club"] == VERANSTALTER
            stadt = texte["ort"]
            termin = neuer_termin(von, bis, texte["titel"], ort_mit_land(stadt, "" if texte["land"] in ("", "SN") else texte["land"]) if stadt else "",
                                  [], vor_ort="" if texte["club"] in ("", VERANSTALTER) else texte["club"])
            termin["_land"] = texte["land"]
            schluessel = (von, bis, texte["titel"])
            if not ist_dbsv or schluessel in bekannt:  # Veranstaltung von Verein/Landesverband oder Doppeleintrag über den Monatswechsel
                letzte = None
                continue
            bekannt.add(schluessel)
            eigene.append(termin)
            letzte = termin
    if len(eigene) < MIN_KALENDER:
        raise ValueError(f"DBSV-Turnierkalender: nur {len(eigene)} DBSV-Termine erkannt (erwartet mindestens {MIN_KALENDER})")
    return eigene


def kalender_pdf_url(seite):
    m = re.search(r'href="([^"]*Turnierkalender[^"]*\.pdf)"', seite)
    if not m:
        raise ValueError("DBSV: Link zum Turnierkalender-PDF nicht gefunden – Seitenaufbau geändert?")
    return urllib.parse.quote(urllib.parse.urljoin(BASIS_URL + "/", html.unescape(m.group(1))), safe=":/%?=&")


# ---------------------------------------------------------------- Zusammenführen beider Quellen

def dbsv_termine(seite, pdf_bytes, warnungen):
    """Wettbewerbe-Seite (maßgeblich für Datum, Titel, Ausschreibungen) und Turnierkalender zusammenführen."""
    seitentermine = seite_lesen(seite, warnungen)
    kalendertermine = kalender_lesen(pdf_bytes, warnungen)
    frei = list(seitentermine)
    ergebnis = []
    for k in kalendertermine:
        # Datum überlappt UND Titel/Ort haben ein Wort gemeinsam (sonst würde z. B. ein Meldeschluss am selben Tag zugeordnet)
        wk = woerter(" ".join(k["titel"]) + " " + k["ort"])
        bewertet = sorted(((len(wk & woerter(" ".join(s["titel"]) + " " + s["ort"])), i) for i, s in enumerate(frei) if ueberlappt(k, s)), reverse=True)
        bewertet = [b for b in bewertet if b[0] > 0]
        kandidaten = [frei[bewertet[0][1]]] if bewertet and (len(bewertet) == 1 or bewertet[0][0] > bewertet[1][0]) else []
        if kandidaten:
            s = kandidaten[0]
            frei.remove(s)
            s["vor_ort"] = k["vor_ort"] or s["vor_ort"]
            s["ort"] = s["ort"] or k["ort"]
            ergebnis.append(s)
        else:
            ergebnis.append(k)
    ergebnis += frei
    for t in ergebnis:
        t.pop("_land", None)
    return ergebnis
