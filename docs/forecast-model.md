# PV-Leistungsmodell und Intervallprognosen

## Ziel und aktueller Stand

Bei dieser Anlage ist PV direkt an den Marstek angeschlossen; der Wechselrichter
liegt dahinter. Zielgröße ist die PV-Leistung am Batterieeingang, gemessen als
`w1 + w2`. Batterieausgang, SOC-Änderung und die 130-W-Verbrauchsannahme sind keine
Eingaben dieses Erzeugungsmodells. Eine spätere Speicherbilanz bleibt getrennt.

Der neue Rechenkern liegt in `src/energy_manager/forecast/power_forecast.py`.
Er ist unabhängig von Home Assistant, MQTT, Wetteranbieter und Dateizugriffen.
Die laufende Prognose verwendet vorerst weiter ihre bisherigen Formeln. Der
neue Kern wird erst nach Prüfung der Offline-Ergebnisse in die Runtime eingebunden.

## Eine Leistungsreihe für alle Zeiträume

`WeatherInterval` beschreibt ein Zeitfenster mit Anfang, Ende und vorhergesagter
Solarenergie pro Fläche in J/m². Fehlende Einstrahlung ist `None`, nicht null.
`predict_power_series()` berechnet für jedes Wetterintervall die mittlere
PV-Leistung. Die Schrittweite ist nicht fest auf eine Stunde begrenzt.

Das Modell lautet:

```text
G = Solarenergie pro Fläche / Intervalllänge in Sekunden       [W/m²]
c = max(0, cos(Einfallswinkel auf die Module))
P = G × (a + b × c)                                         [W]
```

Standort, Zeitpunkt, Modulazimut und Neigung bestimmen den Einfallswinkel. Uhrzeit
und Jahreszeit gehen damit über die Sonnengeometrie ein. Die Geometrie wird am
Intervallmittelpunkt ausgewertet, die Leistung innerhalb des Intervalls als
konstant angenommen. Das liefert keine sekundengenaue Momentanprognose.

`a` und `b` werden offline aus passenden Wetter-/Messpaaren bestimmt und müssen
nichtnegativ sein. Es gibt keinen konstanten Leistungsaufschlag bei null
Einstrahlung. Die Formel ist ein empirischer Kandidat; sie ist keine vollständige
physikalische Umrechnung von horizontaler zu geneigter Einstrahlung.

Eine bekannte PV-Eingangsgrenze kann über `max_power_w` angegeben werden. Ohne
diese Angabe wird keine Obergrenze erfunden. Die maximale Batterieausgangsleistung
ist kein Ersatz für eine PV-Eingangsgrenze. Die Anpassung minimiert zunächst den
unkappierten quadratischen Fehler; die optionale Grenze kappt anschließend die
Vorhersage, sie ist kein eigenes Modell für Abregelung.

## Zeitfenster und Einheiten

| Funktion | Ergebnis |
| --- | --- |
| `predict_power_series(weather, model, latitude, longitude, array)` | Intervalle mit mittlerer Leistung in W |
| `energy_between(series, start, end)` | Energie in Wh nur innerhalb `[start, end)` |
| `energy_for_day(series, day, zone)` | Energie des angegebenen lokalen Kalendertags |
| `forecast_horizon_end(now, zone)` | Ende von übermorgen, als UTC-Zeitpunkt |

Energie ergibt sich aus Leistung × überlappender Zeitdauer. Angebrochene Intervalle
werden anteilig berücksichtigt. Für kWh wird das Wh-Ergebnis durch 1000 geteilt.
Das Ergebnis bietet außerdem `mean_power_w` für die mittlere Leistung im
abgefragten Zeitraum. Damit lassen sich jetzt, +1 h und +2 h aus derselben Reihe
abfragen, beispielsweise als aufeinanderfolgende einstündige Zeitfenster.

Morgen ist von morgen 00:00 bis übermorgen 00:00 in `Europe/Berlin`. Energie von
heute gehört nicht dazu. Eine Reihe bis zum Ende von übermorgen kann mehr als
48 Stunden umfassen. Zeitstempel müssen Zeitzoneninformationen enthalten;
Integration und Vergleiche verwenden UTC. Lokale Tage können 23 oder 25 Stunden haben.

Fehlt ein Teil des angefragten Zeitfensters oder eine benötigte Leistung, ist
`energy_wh` nicht verfügbar. `covered_seconds` und `expected_seconds` zeigen die
Abdeckung. Bekannte 0 W bleiben ein gültiger Wert. Überlappende Intervalle werden
abgewiesen, damit Energie nicht doppelt gezählt wird. Bei vollständiger Abdeckung
bleibt die Datenqualität `heuristic`; das ist keine statistische Konfidenz und
keine Ampelbewertung. Aktualität der Wetterabrufe muss die spätere Adapter- und
Orchestrierungsebene prüfen.

## Offline-Anpassung und Vergleich

`fit_power_model()` und `evaluate_power_models()` liegen in `offline_analysis.py`.
Die Koeffizienten werden mit einer nichtnegativen Ausgleichsrechnung für zwei
Merkmale bestimmt. Es sind keine zusätzlichen numerischen Bibliotheken nötig.
Bei konstantem Einfallswinkel gibt es eine stabile Lösung ohne singuläres
Gleichungssystem; reine Nachtmessungen erzeugen keinen erfundenen Parametersatz.

Der Vergleich verwendet drei Kandidaten auf denselben Testintervallen:

1. Die bisherige feste **Stundenleistungsformel**, nicht die Tagesenergieformel.
2. Eine angepasste Einstrahlungsgerade `P = a × G`.
3. Das neue Modell `P = G × (a + b × c)`.

Die späteren lokalen Kalendertage werden vollständig zurückgehalten. Die
Koeffizienten entstehen ausschließlich aus vorherigen Intervallen; auch ein
Trainingsintervall darf nicht in den Testtag hineinreichen. Es werden mindestens
30 gültige Trainingspaare benötigt. Nullwerte bleiben enthalten. Ungültige Paare
oder Prognoseabrufe nach Beginn des Zielintervalls werden ausgeschlossen.

Ausgaben sind MAE und RMSE in W sowie Bias als **Prognose minus Messung**.
`active_mae_w` berücksichtigt nur Intervalle mit positiver vorhergesagter
Einstrahlung oder positiver gemessener Leistung. So sind reine Nullintervalle
in der Gesamtbewertung enthalten, verdecken aber nicht den Fehler bei Ertrag.
Fehlende Stunden werden nicht zu vermeintlich vollständigen Tagesfehlern summiert.

```bash
.venv/bin/python scripts/analyze_brightsky_input.py \
  --max-gap-seconds 60 \
  --decision-time 18:00 \
  --max-snapshot-age-hours 2 \
  --output data/analysis/forecast/hourly_pairs.csv \
  --model-report data/analysis/forecast/power_model_comparison.json
```

Die 60-Sekunden-Grenze betrifft nur die Integration der Offline-Messwerte. Die
laufende Tagesenergiezählung und die Batterieregelung werden nicht verändert.
Das Werkzeug speichert bei Bedarf einen Vergleichsbericht, übernimmt aber keine
Koeffizienten in den Betrieb. Die bisherige breite Ridge-Auswertung bleibt als
separater Legacy-Helfer erhalten; das CLI verwendet den neuen Drei-Modell-Vergleich.

Die CSV-Trainingsausgabe und der ursprüngliche Vergleich verwenden weiterhin
die jüngste Prognose vor der Zielstunde. Dieser Vergleich allein belegt keine
Güte für einen Tag oder zwei Tage Vorlauf. Wenige lokale Tage reichen außerdem
nicht für eine Aussage über andere Jahreszeiten.

## Auswertung nach Vorlauf und Planungszeitpunkt

`horizon_analysis.py` ergänzt den JSON-Bericht um `horizon_evaluation`:

- **Stunden:** Die Vorlaufgruppen sind `[0, 1)`, `[1, 3)`, `[3, 12)`, `[12, 24)`,
  `[24, 48)` und mindestens 48 Stunden. Pro Zielstunde und Gruppe wird nur die
  jüngste passende Prognoseversion ausgewertet. Mehrere archivierte Abrufe
  erhöhen nicht das Gewicht einer Zielstunde. Verschiedene Gruppen können
  unterschiedliche Zielstunden enthalten; die Anzahl wird separat ausgewiesen.
- **Tagesplanung:** `--decision-time` legt eine lokale Uhrzeit fest, standardmäßig
  18:00 in der konfigurierten BrightSky-Zeitzone. Für jeden Testtag werden morgen
  und übermorgen aus Sicht des jeweiligen früheren Planungsabends bewertet.
  Es zählt ausschließlich der letzte bis dahin vorhandene gemeinsame Wetterabruf.
  Ein späterer Abruf um 18:05 darf eine Planung um 18:00 nicht verbessern.
- **Aktualität:** Der Abruf darf bei der Entscheidung standardmäßig höchstens
  zwei Stunden alt sein; `--max-snapshot-age-hours` macht diese Grenze anpassbar.
  Fehlende Stunden werden nicht aus anderen Abrufen ergänzt. Auch bekannte
  Nachtstunden benötigen Daten; fehlende Einstrahlung wird nicht als null erfunden.
- **Tagesgrenzen:** Jeder Zieltag läuft von lokal 00:00 bis zum nächsten lokalen
  Mitternachtspunkt. Integration und Vorlauf verwenden UTC. Tage mit 23 oder
  25 Stunden bleiben entsprechend lang. Eine mehrdeutige oder nicht existente
  Planungsuhrzeit bei der Zeitumstellung wird ausdrücklich ausgeschlossen.

Alle Gruppen verwenden dieselben späteren lokalen Testtage. Die Trainingspaare
stammen aus der bisherigen Auswahl der jeweils jüngsten Prognose vor der Stunde.
Pro Gruppe wird einmal trainiert und der Parametersatz danach eingefroren.
Das Trainingsende liegt spätestens an der Testtagsgrenze und vor oder genau am
frühesten ausgewerteten Abrufzeitpunkt; bei der Tagesplanung zählt stattdessen
der Entscheidungszeitpunkt. Kein Trainingsintervall darf über diese Grenze
reichen. Längere Vorläufe können daher deutlich weniger Trainingsdaten zulassen.
`trained_through`, `train_rows`, `model_status` und die Parameter dokumentieren
diese Auswahl. Bei weniger als 30 Trainingspaaren oder fehlender Einstrahlung
bleibt die feste Baseline auswertbar; Ergebnisse der angepassten Modelle werden
erst ausgewiesen, wenn sie tatsächlich trainiert werden konnten.

Die Tagesberichte enthalten je Fall Zieltag, Entscheidungs- und Abrufzeitpunkt,
Abrufalter, erwartete Stunden, Abdeckung sowie Ist- und Prognoseenergie in kWh.
Die Messabdeckung wird aus vollständig integrierten archivierten Stundenpaaren
bestimmt. Unvollständige Prognosen oder Messungen, veraltete Abrufe und fehlende
Daten liefern keinen Tagesfehler. MAE, RMSE und Bias in kWh beruhen ausschließlich
auf vollständigen gemeinsamen Tagen. Der Bias ist Prognose minus Messung.
Die Tages-Baseline integriert die bisherige Stundenleistungsformel; sie ist nicht
die separate Tagesregression aus der Runtime.

Die Offline-Integration verlangt jetzt eine exakt abgedeckte Intervallgrenze;
auch eine fehlende halbe Sekunde am Ende wird nicht mehr auf vollständig gerundet.
Diese Präzisierung betrifft ausschließlich die Offline-Auswertung.

## Langfristige Datensammlung

Unter `data/logs/forecast/` werden Bright-Sky-Prognoseabrufe und die kompakte
`pv_input_history_YYYY-MM-DD.csv` gesammelt. Letztere speichert bei jedem
empfangenen Marstek-Status nur UTC-Zeitstempel und gemessene Eingangsleistung.
Dadurch können Stundenpaare später neu gebildet werden; auch kurze Schattenübergänge
bleiben zeitlich erkennbar. Ausführliche Diagnosen liegen separat unter
`data/logs/marstek/`. Sie sind in der HA-App standardmäßig ausgeschaltet und
behalten bei Aktivierung ihre zusätzliche Altersgrenze von zehn Tagen.

Ab App-Version 0.2.0 steuern `forecast_data_logging` und `debug_logging` die
Aufzeichnungen unabhängig voneinander. Die kompakten PV- und Wetterdaten sind
standardmäßig aktiv und teilen sich 200 MiB Speicherbudget. Älteste abgeschlossene
UTC-Tagesdateien werden bei Bedarf gelöscht; heutige Dateien bleiben geschützt.
Ist für sie kein Platz mehr, pausiert die Aufzeichnung. Für langfristige oder
jahreszeitliche Modellvergleiche müssen Daten vor ihrer Löschung extern archiviert
werden. Es gibt keinen automatischen Export. Der bisherige Quellcode-Hauptschalter
`CSV_LOGGING_ENABLED` muss für beide CSV-Kategorien eingeschaltet bleiben.
Die Messauflösung, Forecast-Berechnung und JSON-Zustände werden nicht verändert.
Die Analyse findet
sowohl die neue Ordnerstruktur als auch alte Dateien direkt unter `data/`.
Überlappende identische Messungen werden nicht doppelt integriert.

`scripts/organize_energy_logs.py` kann bestehende V5-Messungen vor einer späteren
Löschung der Diagnosen in die kompakte Historie übernehmen und alte CSV-Dateien
einsortieren. Das Werkzeug wird bei gestopptem Manager oder auf einer Offline-Kopie
ausgeführt. Recovery-, Tagesenergie- und Prognosevergleichszustände bleiben an
ihren bisherigen JSON-Pfaden. Das Analysewerkzeug schreibt standardmäßig nach
`data/analysis/forecast/hourly_training_pairs.csv`.
