# SBV-Wettkampfkalender als abonnierbarer Kalender

Liest täglich das PDF <https://www.sachsenbogen.de/dbdata/wettkampf/sbv_wettkampfkalender.pdf>,
extrahiert die Termine und stellt sie als iCalendar-Feed (`docs/sbv_wettkampfkalender.ics`) bereit,
den Google Kalender (und jeder andere Kalender) abonnieren kann.

- `sbv_kalender.py` – PDF laden, Tabelle über Textkoordinaten lesen (PyMuPDF), `.ics` schreiben.
  Die Ausgabe ist deterministisch (kein Erzeugungszeitpunkt, DTSTAMP = Stand des PDFs), die Datei ändert sich also nur,
  wenn sich das PDF inhaltlich ändert. Stabile UIDs je Datum+Titel+Ort.
- `.github/workflows/update.yml` – GitHub Action, täglich 04:17 UTC; committet die `.ics` nur bei Änderung.
  Findet das Skript weniger als 20 Termine (Layout geändert), bricht der Lauf ab, die alte Datei bleibt online und
  GitHub schickt eine Fehler-Mail.
- `docs/` – wird per GitHub Pages ausgeliefert.

Lokal testen: `pip install -r requirements.txt`, dann `python sbv_kalender.py --liste` (zeigt alle Termine als Tabelle).

## Einrichten (einmalig)

Das Repo muss **öffentlich** sein, damit GitHub Pages kostenlos ausliefert und Google die URL abrufen kann
(der Inhalt ist ohnehin öffentlich). Ordner `SBV-Kalender/` als eigenes Repo:

```bash
cd SBV-Kalender
git init -b main
git add .
git commit -m "SBV-Wettkampfkalender als ICS-Feed"
gh repo create sbv-wettkampfkalender --public --source . --push
gh api -X POST repos/:owner/sbv-wettkampfkalender/pages -f "source[branch]=main" -f "source[path]=/docs"
```

Feed-URL danach: `https://<github-nutzer>.github.io/sbv-wettkampfkalender/sbv_wettkampfkalender.ics`

## Ausschreibungen

Zusätzlich liest das Skript die Seite „Ausschreibungen“ (Menü Aktuelles → Wettkämpfe → Ausschreibungen; die Adresse wird über
die Startseite gefunden, die Seiten-ID ist nur Fallback). Gibt es zu einem Termin eine Ausschreibung als PDF, steht vor dem
Titel ein Pokal (🏆), die Beschreibung enthält den Link zum PDF und den Beginn; zusätzlich setzt das Skript `URL` und `ATTACH`
(wird von Google Kalender bei abonnierten Feeds nicht als Anhang gezeigt, der Link in der Beschreibung genügt).
Zuordnung: Datum überlappt und Titel/Verein stimmen in Worten überein (bei mehreren Terminen am selben Tag entscheidet die
größte Übereinstimmung). Ausschreibungen, die zu keinem Termin im PDF-Kalender passen, werden beim Lauf als „Hinweis“ ausgegeben.

## In Google Kalender abonnieren

calendar.google.com (am PC) → links „Weitere Kalender“ → **+** → **Per URL** → Feed-URL einfügen.
Google aktualisiert abonnierte Kalender selbst, in der Regel alle 12–24 Stunden (nicht einstellbar); neu abonnierte Kalender
erscheinen in der Android-/iOS-App nach kurzer Zeit automatisch.

## Bekannte Eigenheiten des PDFs

- Mehrtägige Termine (`27.-29.03.2026`, `28./29.03.2026`) werden als ganztägige Termine über den Zeitraum angelegt.
- Tippfehler im PDF werden geglättet und beim Lauf als „Hinweis“ ausgegeben (z. B. `1.10./01.11.2026` → 31.10.–01.11.,
  `10.01.207` → 2027).
- Abschnitt „Vorschau“ (ohne Daten) und Zeilen ohne Datum werden übersprungen.
- Meldeschlüsse und Sitzungen stehen als Termine im PDF und kommen deshalb auch im Kalender vor.
- Wird das PDF-Layout grundlegend geändert, muss `SPALTEN` in `sbv_kalender.py` angepasst werden.
