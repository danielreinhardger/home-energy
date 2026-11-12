# PV-Strategie: Sonnenstrom nach Wert verwenden

Stand 2026-11-08. Ersetzt die frühere reine Warmwasser-Strategie (Abschnitte weiter unten
bleiben als Hintergrund und Recherche).

## Die Rechnung

| | |
|---|---|
| Strom aus dem Netz | **35 ct/kWh** |
| Einspeisevergütung | **9,2 ct/kWh** |
| Batterie | 10,2 kWh, ~90 % Wirkungsgrad, nachts von Haushaltsgeräten weitgehend leer gezogen |
| Wärmepumpe | **Stiebel Eltron LWZ 5 S Plus** (Integralgerät mit Lüftung/WRG), 4,4 kW Heizleistung |
| COP Heizen (W35, Fußbodenheizung) | Datenblatt: **2,61 bei −7 °C, 3,74 bei 2 °C, 4,74 bei 7 °C** – skaliert mit den eigenen Zählern (Lebensdauer 25 153 kWh Wärme / 5 488 kWh Strom = 4,58 → Faktor 1,11) |
| COP Warmwasser | **2,6** – aus den Zählern (10 978 / 4 184 kWh); sobald genug Tage erfasst sind, aus den letzten 14 Tagen |

Eine kWh PV, die eine spätere kWh aus Netz oder Batterie ersetzt, ist bis zu 35 ct wert, eingespeist nur
9,2 ct. **Entscheidend ist, ob die gespeicherte Energie vor der nächsten Sonne gebraucht wird.** Wärme, die
erst nach dem nächsten Sonnenfenster gebraucht würde, verdrängt nur morgigen Gratis-Strom (also 9,2 ct)
und kostet dabei Wirkungsgrad.

Wert je kWh PV (live berechnet in `economics.py`, angezeigt auf „Übersicht“ und „So funktioniert's“):

| Verwendung | Wert | Warum |
|---|---|---|
| Hausverbrauch direkt | 35 ct | passiert automatisch |
| Warmwasser-Tagesbedarf mit PV | ~36 ct | Nachheizen mittags statt abends; COP mittags etwas besser |
| Haus vorheizen (Winter) | ~35 ct | Heizen am Tag statt nachts; ~8 % Mehrverlust durch +1 K |
| Batterie laden | 31,5 ct | 35 ct × 90 % Wirkungsgrad |
| Warmwasser über Bedarf (50 °C) | ~28 ct | COP sinkt ~3 %/K, Speicher verliert mehr – **nur vor trübem Tag** |
| Einspeisen | 9,2 ct | |

Der COP hängt stark von der Außentemperatur ab (`cop.py`). Deshalb zählt nicht nur *ob*, sondern *wann*
geheizt wird: mittags ist die Luft wärmer als nachts. Der „COP-Vorteil am Tag“ wird aus der Temperatur-
prognose berechnet (COP bei Tageshöchst- vs. Tiefsttemperatur) statt als fester Wert. An einem Tag mit 0 °C
nachts und 8 °C mittags ist die Wärmepumpe mittags gut 30 % effizienter.
Sobald genug eigene Messungen da sind (≥ 2 h Heizbetrieb mit COP jetzt, Außentemperaturen über ≥ 4 K,
letzte 30 Tage), kommt dieses Verhältnis aus einer Geraden durch die gemessenen Werte statt aus dem
Datenblatt – nur innerhalb des gemessenen Temperaturbereichs, sonst weiter Datenblatt. Genutzt wird nur
das Verhältnis Tag/Nacht: Lüfter und Pumpen fehlen in der Motorleistung, verschieben aber kaum das Verhältnis.

**Kein Raumfühler:** Die LWZ meldet als „Raum-Ist HK1“ nur ihr eigenes Raum-Soll zurück (Feuchte 0 %).
Eine echte Innentemperatur gibt es nicht; „Puffer voll“ und das Hausmodell (Speicherfähigkeit) lassen
sich damit nicht messen. Als Ersatz wird die Ablufttemperatur aufgezeichnet (`extract_temp`, nur bei
laufender Lüftung aussagekräftig).

Die Plätze 2–4 sind praktisch gleichwertig. Im Winter geht das Haus trotzdem vor: Es kann pro Tag mehrere
kWh Strom aufnehmen (Warmwasser nur ~1 kWh), und die Batterie wird nachts ohnehin von den Geräten geleert –
jede kWh Heizwärme, die in den Tag verlegt wird, spart nachts 35 ct Netzstrom.

**Warum kein 55-°C-Boost mehr:** Vor einem sonnigen Tag verdrängt er nur den morgigen PV-Strom (Wert
9,2 ct) bei schlechterem COP (~1,9 statt 2,6) und höheren Speicherverlusten. Ein Vorrat (50 °C) lohnt nur
vor einem trüben Tag, wenn die Batterie trotzdem voll wird und – im Winter – das Haus schon vorgeheizt ist.

## Das Haus als Speicher

FingerHaus VIO (124–184 m², Holzrahmenbau mit THERMO+-Hülle: Wand U = 0,12, Dach 0,17, Fenster Ug 0,5,
20 cm Stahlbeton-Bodenplatte, Fußbodenheizung, Lüftung mit Wärmerückgewinnung), KfW 40+.
Grobe Abschätzung (keine Messung):

- Wärmeverlust H ≈ 80–100 W/K (Hülle + Lüftung mit WRG).
- Speicherfähigkeit C ≈ 15–25 kWh/K (Estrich ~20 t, Bodenplatte, Innenausbau).
- Zeitkonstante τ = C/H ≈ 7–11 Tage → in einer 14-h-Nacht bei 0 °C außen fällt der Raum nur ~1–1,5 K.
- +1 K Vorheizen speichert grob 10–20 kWh Wärme = 2–4 kWh Strom bei COP 4,6.

Damit funktioniert „nur am Tag heizen“. Komfortband (auf Wunsch der Bewohner): **normal 22 °C, ohne PV
bis 20 °C** (kühle Abende sind kein Komfortverlust), **als Wärmepuffer bis 24 °C**. Tagsüber hebt PV die
Raumtemperatur an, abends und nachts darf sie bis 20 °C sinken – die gespeicherte Wärme trägt das Haus bis
zur nächsten Sonne.

## Die Regeln (jede Minute, in dieser Reihenfolge)

**1. Heizen** (`heating.py`, schreibt „Raumtemp. Tag“ HK1 = Fußbodenheizung, in 0,1-K-Schritten):
- Heizperiode: außen unter 15 °C.
- **Batterie zuerst** (seit 2026-11-09): Läuft PV und wird die Batterie heute nicht voll (unter 95 % und
  der freie PV-Rest deckt den Batteriebedarf nicht 1,2-fach), wird nicht vorgeheizt: das Haus bleibt bei der
  normalen Raumtemperatur (22 °C, nicht am Minimum – Komfort geht vor dem letzten Rest Batterie). Anlass: Am 9.11. lief die Wärmepumpe tagsüber mit
  3,3 von 7,5 kWh PV, die Batterie wurde nicht voll – abends kam die Wärme dann teuer aus dem Netz.
- **Vorheizen, sanft und fein**: ≥ 800 W nutzbarer Überschuss für 10 min (solange die Batterie nicht sicher
  voll wird, zählt nur Einspeisung), kein Bad, Raum unter Pufferziel → erstes Ziel nur **Raum + 0,3 K**.
  Danach alle 10 min ±0,2 K: höher bei > 300 W Einspeisung, tiefer bei > 200 W Bezug aus Netz/Batterie.
  Obergrenze ist das **Pufferziel**: ein voller 2-K-Puffer braucht 2 K × 20 kWh/K ÷ COP ≈ 8,7 kWh Strom;
  was nach dem Batteriebedarf vom PV-Rest übrig ist, bestimmt die Höhe (22,3 … 24 °C). Vor einem trüben
  Tag wird der volle Puffer genutzt. Ende bei Puffer voll, Bad, oder > 300 W Defizit für 15 min.
- **Warum Schritte statt Leistungsvorgabe:** Die LWZ 5 S Plus hat **keinen Leistungs-Sollwert** (weder per
  Modbus noch auf der ISG-Seite; der SG-Ready-Eingang kennt nur Stufen). Der Inverter-Verdichter moduliert
  selbst nach dem Abstand Raum-Soll − Raum-Ist: Ziel knapp über dem Raum → kleine Leistung, großer Abstand →
  volle Leistung. Das kleine Ziel in 0,1-K-Schritten ist deshalb der Hebel für die Leistung. Die tatsächliche
  Verdichterleistung (FACHMANN-Seite: MOTORLEISTUNG, Drehzahl) wird mitgelesen und im Trace angezeigt.
  Dazu die gemessene Heizleistung (HEIZ/KÜHL-LEISTUNG GEMESSEN) und daraus der **COP jetzt** = Wärme ÷
  Motorleistung (nur bei laufendem Verdichter, 1…10). Die Motorleistung enthält keine Lüfter und Pumpen,
  daher liegt dieser Wert über dem Tages-COP aus den Zählern. Beides läuft minütlich in die Historie
  (`hp_heat_w`, `cop_live`). Die Warmwasser-Leistung ist dagegen begrenzbar (ISG „WW-LEISTUNG“, 30–100 %).
- **Absenkung**: ohne PV, wenn heute noch ≥ 2 Sonnenstunden kommen oder abends ein guter PV-Tag
  folgt (≥ 8 kWh) → bis 20 °C. Sonst normal (22 °C).
- **Kein Hin und Her** (seit 2026-11-11; vorher wechselte das Ziel an einem Tag sechsmal zwischen 20 und
  22 °C): Normal, Absenkung und „Batterie zuerst“ wechseln frühestens nach 45 min (einstellbar). Dazu
  Abstände an den Schwellen, sobald eine Betriebsart läuft: Heizperiode endet erst 1 K über der Grenze,
  eine laufende Absenkung wartet auf morgen bis 85 % von „Trüber Tag unter“ (Prognose schwankte live
  zwischen 7,9 und 8,8 kWh), „PV läuft“ endet erst unter 100 W (beginnt bei 300 W). Vorheizen und Bad
  warten nie.

**2. Warmwasser** (`hotwater.py`, schreibt „WW Soll Tag“):
- Bad angemeldet → Badplanung (unverändert, hat Vorrang vor allem).
- **Tagesbedarf mit PV**: Speicher unter Komfort − 2 K und Überschuss ≥ 90 % der WW-Leistung für 5 min →
  Komfort (46 °C). **Batterie zuerst:** solange die Batterie heute nicht sicher voll wird, zählt nur
  Einspeisung als Überschuss (abschaltbar).
- **Vorrat** (50 °C) nur, wenn morgen trüb (< 8 kWh), die Batterie trotzdem voll wird und das Haus nicht
  gerade vorgeheizt wird.
- **Legionellenschutz** (seit 2026-11-11): alle 7 Tage einmal 60 °C, bevorzugt mit PV-Überschuss (wie
  der Vorrat: nur wenn die Batterie trotzdem voll wird und das Haus nicht gerade vorgeheizt wird). Kam
  14 Tage kein PV-Tag, wird mittags 11–15 Uhr (wärmste Luft) trotzdem geheizt. Erledigt, sobald der
  Speicher 59 °C erreicht (auch durch ein Bad) oder nach 4 h. Das eigene Antilegionellen-Programm der
  ISG bleibt aus (dort 10 °C).
- **WW-Leistung** (`dhwpower.py`, seit 2026-11-11): während PV den Speicher heizt, wird die
  Leistungsbegrenzung der ISG („WW-LEISTUNG“ Sommer/Winter, sonst 80 %) nachgeführt: bei ≥ 500 W
  Einspeisung 100 %, bei ≥ 300 W Bezug 50 %, dazwischen bleibt sie. Bad: 100 %. Sonst wieder 80 %.
  Höchstens alle 30 min, max. 8 Änderungen am Tag. Nicht im Modbus-Plan – geschrieben über die
  ISG-Webseite (POST `save.php` wie der Speichern-Knopf) mit Rücklesen; getestet 80 → 85 → 80 %.
- **Grundzustand**: Sonne kommt → 42 °C halten und warten.
  **Nacht vor einem trüben Tag** (seit 2026-11-09, abschaltbar): abends bzw. nach Mitternacht vor
  Sonnenaufgang bis 42 °C absinken lassen – es kommt kein PV zum Nachladen, also wird erst tagsüber
  (wärmere Luft, besserer COP) statt nachts aus Batterie oder Netz nachgeheizt.
  **Nur mit PV über das Minimum** (seit 2026-11-09, Standard): auch tagsüber ohne genug Sonne bleibt das
  Ziel 42 °C. Über das Minimum heizt nur PV-Überschuss (Tagesbedarf bis 46 °C, Vorrat bis 50 °C); unter
  42 °C hält die Wärmepumpe das Minimum selbst. Ein angemeldetes Bad wird immer geheizt. Abschaltbar –
  dann gilt ohne Sonne wieder die Komforttemperatur.

**Was automatisch passiert:** Die Batterie lädt mit allem, was übrig bleibt; das Auto lädt über openWB
im Modus PV-Laden ab 1300 W Überschuss; der Rest wird eingespeist.

## Was bringt das? Simulation

`homeenergy/simulate.py` rechnet die echten Regeln (`decide_heating()`, `decide()`) minütlich gegen ein einfaches
Modell: Haus mit H = 0,06 kW/K und C = 20 kWh/K, Wärmepumpe 3,5 kW Heizleistung (COP 4,6) bzw. 3,4 kW
Warmwasser (COP 2,6, −3 %/K), 235-l-Speicher mit Zapfungen 07:00 (3 kWh) und 19:00 (2 kWh), Batterie
10,24 kWh, Grundlast 350 W + 800 W abends. Vergleich mit den früheren festen Werten (23,5 °C / 46 °C),
Kosten pro Tag (Stand 2026-11-08, Standardeinstellungen):

Mit der temperaturabhängigen COP-Kurve der LWZ 5 S Plus (kalibriert, Heizleistung 4,4 kW) und dem
Komfortband 20 / 22 / 24 °C:

| Modelltag | COP Heizen | Feste Werte | Strategie | Ersparnis | WP-Strom nachts |
|---|---|---|---|---|---|
| Kalter Sonnentag (−5 °C) | 3,2 | 2,93 € | 0,69 € | **2,25 €** | 10,3 → 1,9 kWh |
| Sonniger Wintertag (5 °C) | 4,8 | 1,06 € | 0,19 € | **0,87 €** | 6,6 → 1,9 kWh |
| Trüber Wintertag, morgen Sonne | 4,4 | 5,02 € | 4,93 € | 0,09 € | 6,1 → 1,7 kWh |
| Sommertag | – | −5,22 € | −5,23 € | ±0 | 0 → 0 |

Je kälter der Sonnentag, desto mehr bringt das Heizen mit PV: mehr Wärme wird in den Tag verlegt, und
nachts wäre der COP schlechter. An trüben Tagen gibt es kaum PV zu verschieben. Der Raum bleibt im Band
20–24 °C. Im Sommer ändert sich wenig, weil die Batterie die Nacht
ohnehin trägt. Die Tests in `tests/test_simulation.py` halten diese Eigenschaften fest; die Tabelle wird auf
„So funktioniert's“ mit den aktuellen Einstellungen neu berechnet.

## Grenzen und offene Punkte

- **SG Ready ist aus** (2026-11-11, Register 4001 = 0): die ISG warnt, dass mit aktivem Energie-
  management Sollwerte nur über die SERVICEWELT geändert werden sollten. Da diese Regelung die Sollwerte
  schreibt, ist das Energiemanagement aus. Beim Ausschalten setzte die ISG „Raumtemp. Tag“ einmal auf
  23,5 °C zurück (gespeicherter Wert); die Regelung hat es eine Minute später wieder auf 20 °C gestellt.

- Das Vorheizen läuft auf Wunsch im **Testbetrieb** (zeigt nur an). Einschalten unter „PV-Regelung“.
- Die Gebäudewerte oben sind Schätzungen. Der Verlauf (SQLite) speichert seit 2026-11-08 die
  Fußbodenheizungstemperaturen und, sobald der Raumfühler da ist, die echte Raumtemperatur (`room_sensor`); nach einigen Winternächten lassen sich H und C aus
  den eigenen Daten bestimmen und Anhebung/Absenkung genauer einstellen.
- Die Wärmepumpe hat keinen eigenen Stromzähler. Seit 2026-11-08 wird ihre Stromaufnahme aus den Wh-genauen
  Tageszählern der ISG-Webseite abgeleitet (Ø 5 min, `devices/isg_web.py`) und im Energiefluss angezeigt; die
  Tageszähler landen im Finanz-Journal (COP pro Tag/Monat).
- Die Simulation (Tab Erklärung) rechnet die Inverter-Modulation vereinfacht linear: 25 % bei kleinem
  Abstand Ziel–Raum, volle Leistung ab 1 K. Mit Batterie zuerst wird die Batterie an Sonnentagen voll.
- Auto und Wärmepumpe teilen sich den Überschuss; eine echte Priorität bräuchte Schreibzugriff auf openWB.

Quellen: [Bosch/tga-praxis: Potenzial der thermischen Speicherung](https://www.tga-praxis.de/sites/default/files/public/data-fachartikel/MGT_2022_12_Das-Potenzial-der-thermischen-Speicherung_21-23_0.pdf) ·
[EnergieSchweiz/BFE: Gebäude als Speicher](https://pubdb.bfe.admin.ch/de/publication/download/10157) ·
[HTW Berlin: Solarstromoptimiertes Energiemanagement von Wärmepumpen](https://solar.htw-berlin.de/wp-content/uploads/WIDERA-2023-Solarstromoptimiertes-Energiemanagement-von-Waermepumpen.pdf) ·
[photovoltaik.info: Thermische Speicherung](https://www.photovoltaik.info/thermische-speicherung-pv-eigenverbrauch/) ·
[FingerHaus VIO](https://www.fingerhaus.de/haeuser/einfamilienhaeuser/vio/) ·
[FingerHaus Wandaufbau](https://www.fingerhaus.de/bauweise/wandaufbau/) ·
[LWZ 5 S Plus Ausschreibungstext (heinze.de)](https://www.heinze.de/ausschreibungstext/stiebel-eltron-integralsystem-lwz-5-s-plus/41995815/) ·
[LWZ 5 S Plus Produktflyer](https://www.stiebel-eltron.de/content/dam/ste/de/downloads/STE_Produktflyer_LWZ_5_S_Plus.pdf)

---

# Hintergrund: frühere Warmwasser-Strategie und Recherche

Ziel: Den Warmwasserspeicher der Wärmepumpe als **Wärmebatterie** nutzen, damit
PV-Strom zu Warmwasser wird und die Hausbatterie (KOSTAL, 10,2 kWh) **nicht**
für die Warmwasserbereitung entladen wird.

## 1. Stand der Technik (Recherche, November 2026)

| Ansatz | Prinzip | Beispiele | Eignung hier |
|---|---|---|---|
| **Überschuss-Schwelle** | Wärmepumpe/Heizstab startet, sobald Einspeisung > Schwelle (oft über SG-Ready-Kontakte). Batterie meist „zuerst voll“. | evcc + SG-Ready-Relais, viele HEMS | Einfach, robust, aber blind für morgen. |
| **Regelbasiert + Prognose** | Wie oben, plus PV-Prognose: Boost auch bei nicht voller Batterie, wenn die Prognose zeigt, dass sie trotzdem voll wird. Vor sonnigen Stunden nicht nachheizen. | Sunny Home Manager, clever-PV | **Gewählt**: gut erklärbar, wenige Parameter, kein Solver. |
| **MPC / lineare Optimierung** | Fahrplan über 24–48 h mit Prognosen für PV, Last und Warmwasserbedarf; thermisches Speichermodell. | EMHASS (LP mit CVXPY/HiGHS, „deferrable load thermal model“), Akkudoktor-EOS (genetischer Algorithmus) | Bester Ertrag in Studien, aber braucht gute Lastprognosen und ist schwer nachzuvollziehen. Für **einen** Verbraucher mit Ein/Aus-Charakter ist der Vorteil klein. |
| **Lernende Verfahren** | ML-Prognosen für Verbrauch/Erzeugung (z. B. CNN-LSTM) + Vortagesplanung | Lopes et al. 2025 | Forschungsstand; lohnt erst mit viel eigenem Verlauf. |

Erkenntnisse aus der Literatur, die in die Regeln eingeflossen sind:

- Prognose- und Modellfehler können MPC-Entscheidungen unbrauchbar machen (TU Dresden,
  Gelleschus & Bocklisch) → **Sicherheitsfaktor** auf den Batteriebedarf und
  **Kalibrierung** der Prognose mit dem echten Ertrag.
- Warmwasserspeicher als thermischer Speicher ist der Standardhebel für
  PV-Eigenverbrauch (EMHASS „thermal inertia“, MDPI 2025 zu Warmwasserspeichern).
- Praxis-HEMS priorisieren Haus → Wärmepumpe/Heizstab → Batterie → Einspeisung,
  oder starten die Wärmepumpe erst bei voller Batterie (evcc-Setups). Wir entscheiden
  das **dynamisch** über die Prognose.
- Luft-Wärmepumpen arbeiten mittags (wärmere Luft) effizienter → Boost in der
  Sonne bringt zusätzlich besseren COP.

Quellen:
[Heat Pumping Technologies: Smart integration of heat pumps by predictive controls](https://heatpumpingtechnologies.org/articles/heat-pumping-technologies-magazine-vol-40-no-3-2022/topical-article-smart-of-heat-pumps-by-predictive-controls/) ·
[MDPI Solar 2025: MPC für Warmwasserspeicher mit PV](https://www.mdpi.com/2673-9941/5/2/15) ·
[Lopes et al. 2025](https://run.unl.pt/bitstream/10362/185229/1/Lopes_R._A._et_al._2025..pdf) ·
[Optimal HEMS für modulierende Wärmepumpen und PV (arXiv)](https://ar5iv.labs.arxiv.org/html/2009.02349) ·
[EMHASS Dokumentation](https://emhass.readthedocs.io/en/latest/) ·
[EMHASS LP-Modell](https://emhass.readthedocs.io/en/stable/lpems.html) ·
[Akkudoktor-EOS](https://akkudoktor-eos.readthedocs.io/en/latest/akkudoktoreos/introduction.html) ·
[evcc-Diskussion Wärmepumpe](https://github.com/evcc-io/evcc/discussions/17963) ·
[simon42: Energiemanagement mit evcc](https://community.simon42.com/t/energie-management-mit-evcc/63085) ·
[KNX-User-Forum: Kostal Plenticore, SG-Ready, Prognose](https://knx-user-forum.de/forum/öffentlicher-bereich/knx-eib-forum/2051654-externe-ladesteuerung-mit-kostal-plenticore-und-byd-sg-ready-prognose) ·
[photovoltaik.info: SG-Ready vs. Heizstab](https://www.photovoltaik.info/pv-ueberschuss-warmwasser-vergleich-heizstaebe/)

## 2. Die Regeln

Die Regelung verändert nur **WW Soll Tag** (Register 1012). Nachts gilt am Gerät
ohnehin „WW Soll Nacht“ (derzeit 10 °C).

Jede Minute:

1. **Überschuss** = Einspeisung + Batterieladeleistung (Strom, der sonst ins Netz oder
   in die Batterie ginge). **Defizit** = Netzbezug + Batterieentladung.
2. **Darf die Batterie Strom abgeben?** Boost ist erlaubt, wenn
   - SOC ≥ „Boost ohne Prognose ab SOC“ (Standard 95 %), **oder**
   - laut Prognose: freie PV-Energie heute (Rest-PV − Grundlast × Sonnenstunden)
     ≥ Batteriebedarf × Sicherheitsfaktor + Warmwasserbedarf.
3. **Boost starten**, wenn Überschuss ≥ Startschwelle × WW-Leistung, Batterie-Regel erfüllt
   und Speicher > 2 K unter Boost-Ziel, **ununterbrochen** für die Startverzögerung (5 min).
   Ziel: Boost-Temperatur (55 °C), bei schwachem nächsten Tag Speicher-Boost (58 °C).
4. **Boost beenden**, wenn der Speicher das Ziel erreicht hat, oder wenn das Defizit
   für die Stoppverzögerung (10 min) über der Grenze liegt **und** die Mindestlaufzeit (20 min)
   vorbei ist. Kurze Wolken beenden den Boost also nicht.
5. **Ohne Boost:**
   - Sonne kommt heute noch und reicht deutlich für den Speicher → **Warten auf Sonne**:
     Mindesttemperatur (42 °C). Die Wärmepumpe heizt morgens nicht aus der Batterie nach.
   - Abends, wenn morgen ein guter Tag ist → ebenfalls Warten (Mindesttemperatur).
   - Sonst **Komfort** (46 °C).

Die Mindesttemperatur ist die garantierte Untergrenze: darunter heizt die Wärmepumpe immer.

### Bad vorbereiten

Wer baden will, meldet Uhrzeit und Temperatur an (Standard 17:30, 55 °C). Bis dahin gilt:

- Aufheizdauer = Strombedarf ÷ WW-Leistung; **spätester Start** = Badezeit − Aufheizdauer × 1,3
  − Sicherheitspuffer (20 min, deckt Schreibabstand und Reaktionszeit der Wärmepumpe).
- Vor dem spätesten Start wird nur mit **PV-Überschuss** geheizt, auch wenn die Batterie noch
  nicht voll ist: Die Energie wird bis zum Bad ohnehin gebraucht, und PV jetzt ist besser als
  Batterie oder Netz später. Kurze Wolken pausieren nicht sofort (wie beim Boost).
- Ab dem spätesten Start wird geheizt, notfalls aus Batterie/Netz, damit das Bad pünktlich warm ist.
- Ist der Speicher warm genug, wird die Badtemperatur bis zur Uhrzeit gehalten; danach läuft
  wieder die normale Regelung.

### Warmwasser-Energie

`E_el = Liter × 1,163 Wh/(L·K) × ΔT / COP`. Beispiel: 235 L von 45 auf 55 °C ≈ 2,7 kWh Wärme
≈ 1,05 kWh Strom bei COP 2,6. Der COP ist aus den Zählern der Anlage geschätzt:
Wärme WW gesamt 10 978 kWh / Strom WW gesamt 4 184 kWh = 2,6.

### Schutz der Wärmepumpe

- **Testbetrieb** ist Standard: Entscheidungen werden angezeigt, aber nicht geschrieben.
- Höchstens ein Schreibvorgang alle 10 min und 24 pro Tag (einstellbar). Einstellungen liegen
  im nichtflüchtigen Speicher des Reglers, darum wenig schreiben.
- Beim Ausschalten wird die Komforttemperatur einmal zurückgeschrieben.
- Ohne frische Daten von beiden Geräten wird nichts entschieden.

## 2b. PV-Heizen im Winter

Ein zweiter, unabhängiger Regler (`heating.py`) nutzt Überschuss in der Heizperiode für das Haus:

- **Heizperiode:** Außentemperatur unter 15 °C.
- **Start:** mindestens 1500 W Überschuss, Batterie ≥ 90 %, Warmwasser heizt gerade nicht mit PV
  (kein Boost, kein Bad), Raum noch unter Ziel − 0,3 K, das Ganze 10 min am Stück.
- **Wirkung:** „Raumtemp. Tag“ (HK1, Register 1002) wird um 1 K angehoben. Die Fußbodenheizung und
  die Gebäudemasse speichern die Wärme bis in den Abend, die Wärmepumpe braucht dann weniger Batteriestrom.
- **Ende:** Raum warm genug, Warmwasser braucht den Überschuss, oder mehr als 300 W Defizit für 10 min
  (nach mindestens 30 min Anhebung).
- **Schutz:** eigene Schreibgrenzen (1 Schreibvorgang je 15 min, 12 pro Tag); beim Ausschalten wird die
  normale Raumtemperatur zurückgeschrieben. Startet im Testbetrieb.
- **Offen:** Ob die Fußbodenheizung an HK1 oder HK2 hängt, ist nicht bestätigt. HK2 (Register 1005)
  ist nicht auf der ISG-Seite und deshalb nicht beschreibbar.

## 3. Wetterprognose: Machbarkeit

**Machbar und eingebaut.** Getestet am 2026-11-08 von diesem Rechner aus:

| Dienst | Kosten / Schlüssel | Auflösung | Ergebnis |
|---|---|---|---|
| **Open-Meteo** `global_tilted_irradiance` | frei (nicht-kommerziell), kein Schlüssel | stündlich, je Modulfläche (Neigung/Azimut) | ✔ funktioniert, gewählt |
| Forecast.Solar | frei mit Limit (12 Abrufe/h), kein Schlüssel | stündlich/täglich | ✔ funktioniert, Alternative |

Modell: `Wh = kWp × GTI × Performance-Ratio` je Stunde und Fläche, Summe über alle Flächen.
Open-Meteo-Strahlung ist der Mittelwert der Stunde *vor* dem Zeitstempel, das wird berücksichtigt.

**Selbstkalibrierung:** Für jeden Tag (Ortszeit) wird festgehalten, was am Vortag für ihn
vorhergesagt wurde (letzter Abruf vor Mitternacht), was am Tag selbst vorhergesagt wurde und was
der Kostal wirklich erzeugt hat (`forecast_log` im Zustand, 90 Tage). Der Faktor ist
**Σ Ertrag ÷ Σ Vortagesprognose** über die letzten 21 Tage mit Ertrag (begrenzt auf 0,3–1,6);
Tage mit < 1 kWh Prognose zählen nicht mit. Summen statt eines Mittels der Tagesquotienten,
damit ein sonniger 20-kWh-Tag mehr zählt als ein grauer 2-kWh-Tag, dessen Quotient fast nur
Rauschen ist. Kalibriert wird gegen die Vortagesprognose, weil die Regeln („morgen trüb?“) sie
nutzen. Bis drei Tage erfasst sind, bleibt der bisherige Faktor (die Prognosekarte zeigt
„vorläufig“). Der Faktor gleicht falsche kWp, Verschattung und Verschmutzung aus.

Stand 11.11.2026: Die Form der Stundenwerte passt zur eingetragenen Ausrichtung (Azimut 75°,
Korrelation 0,77; Süd 0,42, Ost 0,34). Der Faktor 0,73 stammt aus drei bedeckten Novembertagen
(Quotienten 0,71 / 0,70 / 1,15) – zu wenig, um eine Jahreszeit- oder Wetterabhängigkeit zu
erkennen. Mit dem Protokoll lässt sich das nach einigen Wochen prüfen.

Grenzen: Wettermodelle unterschätzen Nebel/Hochnebel im Winter (Forenberichte). Darum der
Sicherheitsfaktor, und darum entscheidet die Prognose nur **ob** geboostet werden darf. **Wann**
gestartet wird, entscheidet immer der gemessene Überschuss.

**Zum Aktivieren** unter „PV-Warmwasser → Wetterprognose“: Breiten-/Längengrad des Hauses und die
Modulflächen (kWp, Neigung, Azimut) eintragen. Die beiden Strings des Kostal (DC1 ~430 V,
DC2 ~590 V) liegen vermutlich auf verschiedenen Dachseiten: dann zwei Flächen anlegen.

## 4. Mögliche Erweiterungen

- **Wallbox und Warmwasser teilen sich den Überschuss.** openWB lädt im Modus PV-Laden ab
  1300 W Überschuss (Einschaltverzögerung 20 s). Wer zuerst startet, nimmt den Überschuss;
  die Warmwasser-Regelung sieht danach weniger Einspeisung und startet nicht. Eine echte
  Priorität (z. B. erst Warmwasser bis 50 °C, dann Auto) bräuchte Schreibzugriff auf openWB
  (`openWB/set/...`) und ist bewusst noch nicht eingebaut.

- Warmwasserbedarf lernen (Temperaturabfall je Tageszeit) → Ziel-Temperatur passend zum Bedarf.
- Elektrische WW-Leistung messen (Hausverbrauch-Sprung beim WW-Start), sobald die Statusbits
  zugeordnet sind (Register 2001).
- Echte Optimierung (EMHASS-artig) für mehrere Verbraucher, etwa wenn eine Wallbox dazukommt.
