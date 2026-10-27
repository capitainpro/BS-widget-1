#!/usr/bin/env python3
"""Haalt de nieuwste wetten en besluiten uit het Belgisch Staatsblad
en maakt er een RSS-bestand (feed.xml) van voor je telefoon.

Gebruik:  python staatsblad_feed.py          (normaal)
          python staatsblad_feed.py --debug  (bewaart de ruwe pagina)

Alleen standaardbibliotheken, niets te installeren.
"""
import json
import re
import sys
import time
from datetime import date, datetime, timedelta, timezone
from html.parser import HTMLParser
from urllib.parse import urljoin
from urllib.request import Request, urlopen
from xml.etree import ElementTree as ET

# ---------------------------------------------------------------
# INSTELLINGEN (hier pas je dingen aan)
# ---------------------------------------------------------------
# LET OP: deze URL is NIET getest. Open het Staatsblad in je browser,
# kies een datum, en kijk of de link overeenkomt. Pas anders aan.
# {datum} wordt vervangen door JJJJ-MM-DD.
LIJST_URL = ("https://www.ejustice.just.fgov.be/cgi/list.pl"
             "?language=nl&sum_date={datum}")

DAGEN_TERUGKIJKEN = 7      # geen publicaties (weekend)? kijk tot X dagen terug
MAX_ITEMS = 60             # zoveel items blijven in de feed
ITEMS_BESTAND = "items.json"
FEED_BESTAND = "feed.xml"
USER_AGENT = "staatsblad-widget (persoonlijk gebruik, 1 aanvraag per dag)"

# Alleen titels die met een van deze woorden beginnen komen erdoor.
# Dat houdt benoemingen en bedrijfspublicaties buiten de feed.
WET_PATROON = re.compile(
    r"^\s*(wet|decreet|ordonnantie|koninklijk besluit|ministerieel besluit|"
    r"besluit van de (vlaamse|waalse|brusselse)|besluit van de regering|"
    r"gecoördineerde|programmawet|grondwet|samenwerkingsakkoord|"
    r"koninklijk besluit)",
    re.IGNORECASE,
)
# Alleen links die op een publicatie lijken.
LINK_PATROON = re.compile(r"(numac=|article\.pl)", re.IGNORECASE)


class LinkVerzamelaar(HTMLParser):
    def __init__(self):
        super().__init__()
        self.links = []          # lijst van (href, tekst)
        self._href = None
        self._tekst = []

    def handle_starttag(self, tag, attrs):
        if tag == "a":
            self._href = dict(attrs).get("href")
            self._tekst = []

    def handle_data(self, data):
        if self._href is not None:
            self._tekst.append(data)

    def handle_endtag(self, tag):
        if tag == "a" and self._href is not None:
            tekst = " ".join("".join(self._tekst).split())
            self.links.append((self._href, tekst))
            self._href = None


def haal_pagina(url):
    req = Request(url, headers={"User-Agent": USER_AGENT})
    with urlopen(req, timeout=30) as r:
        ruw = r.read()
        charset = r.headers.get_content_charset() or "utf-8"
    for enc in (charset, "utf-8", "latin-1"):
        try:
            return ruw.decode(enc)
        except (UnicodeDecodeError, LookupError):
            continue
    return ruw.decode("latin-1", errors="replace")


def parse_wetten(html, basis_url):
    verzamelaar = LinkVerzamelaar()
    verzamelaar.feed(html)
    gevonden, gezien = [], set()
    for href, tekst in verzamelaar.links:
        if not href or not LINK_PATROON.search(href):
            continue
        if len(tekst) < 15 or not WET_PATROON.search(tekst):
            continue
        volledig = urljoin(basis_url, href)
        if volledig in gezien:
            continue
        gezien.add(volledig)
        gevonden.append({"titel": tekst, "link": volledig})
    return gevonden


def laad_items():
    try:
        with open(ITEMS_BESTAND, encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return []


def bewaar_items(items):
    with open(ITEMS_BESTAND, "w", encoding="utf-8") as f:
        json.dump(items, f, ensure_ascii=False, indent=1)


def schrijf_feed(items):
    rss = ET.Element("rss", version="2.0")
    kanaal = ET.SubElement(rss, "channel")
    ET.SubElement(kanaal, "title").text = "Staatsblad: wetten en besluiten"
    ET.SubElement(kanaal, "link").text = "https://www.ejustice.just.fgov.be/"
    ET.SubElement(kanaal, "description").text = (
        "Nieuwe wetten en besluiten uit het Belgisch Staatsblad")
    for it in items:
        e = ET.SubElement(kanaal, "item")
        ET.SubElement(e, "title").text = it["titel"]
        ET.SubElement(e, "link").text = it["link"]
        ET.SubElement(e, "guid").text = it["link"]
        ET.SubElement(e, "pubDate").text = datetime.strptime(
            it["datum"], "%Y-%m-%d").replace(tzinfo=timezone.utc
        ).strftime("%a, %d %b %Y 00:00:00 +0000")
    ET.indent(rss)
    ET.ElementTree(rss).write(FEED_BESTAND, encoding="utf-8",
                              xml_declaration=True)


def main():
    debug = "--debug" in sys.argv
    items = laad_items()
    bekend = {i["link"] for i in items}
    nieuw = []

    vandaag = date.today()
    for terug in range(DAGEN_TERUGKIJKEN + 1):
        dag = (vandaag - timedelta(days=terug)).isoformat()
        url = LIJST_URL.format(datum=dag)
        try:
            html = haal_pagina(url)
        except Exception as fout:
            print(f"[{dag}] ophalen mislukt: {fout}")
            continue
        if debug:
            with open(f"debug_{dag}.html", "w", encoding="utf-8") as f:
                f.write(html)
        wetten = parse_wetten(html, url)
        print(f"[{dag}] {len(wetten)} wetten/besluiten gevonden")
        for w in wetten:
            if w["link"] not in bekend:
                w["datum"] = dag
                nieuw.append(w)
                bekend.add(w["link"])
        if wetten and terug > 0 and items:
            break          # voldoende teruggekeken
        time.sleep(2)      # vriendelijk blijven voor de server

    items = (nieuw + items)[:MAX_ITEMS]
    bewaar_items(items)
    schrijf_feed(items)
    print(f"Klaar: {len(nieuw)} nieuw, {len(items)} in feed.")
    if not items:
        print("Niets gevonden: draai opnieuw met --debug en stuur "
              "een debug_*.html door om het script bij te stellen.")


if __name__ == "__main__":
    main()
