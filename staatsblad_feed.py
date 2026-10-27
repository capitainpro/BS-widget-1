#!/usr/bin/env python3
"""Haalt de nieuwste wetten en besluiten uit het Belgisch Staatsblad
en maakt er een RSS-bestand (feed.xml) van voor je telefoon.

Gebruik:  python staatsblad_feed.py          (normaal)
          python staatsblad_feed.py --debug  (bewaart de ruwe pagina)

Gebruikt een echte (onzichtbare) Chrome via Playwright, omdat het
Staatsblad een JavaScript-controle heeft. Draait in GitHub Actions.
"""
import json
import re
import sys
import time
from datetime import date, datetime, timedelta, timezone
from html.parser import HTMLParser
from urllib.parse import urljoin
from xml.etree import ElementTree as ET

# ---------------------------------------------------------------
# INSTELLINGEN (hier pas je dingen aan)
# ---------------------------------------------------------------
# LET OP: deze URL is NIET getest. Open het Staatsblad in je browser,
# kies een datum, en kijk of de link overeenkomt. Pas anders aan.
# {datum} wordt vervangen door JJJJ-MM-DD.
# Het script probeert deze adressen in volgorde en neemt het eerste
# dat een niet-lege pagina teruggeeft.
LIJST_URLS = [
    "https://www.ejustice.just.fgov.be/cgi/summary.pl?language=nl&sum_date={datum}",
    "https://www.ejustice.just.fgov.be/cgi/list.pl?language=nl&sum_date={datum}",
]

DAGEN_TERUGKIJKEN = 7      # geen publicaties (weekend)? kijk tot X dagen terug
MAX_ITEMS = 60             # zoveel items blijven in de feed
ITEMS_BESTAND = "items.json"
FEED_BESTAND = "feed.xml"
HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                   "AppleWebKit/537.36 (KHTML, like Gecko) "
                   "Chrome/124.0 Safari/537.36"),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "nl-BE,nl;q=0.9,en;q=0.8",
    "Referer": "https://www.ejustice.just.fgov.be/",
}

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


def haal_pagina(page, url):
    """Opent de pagina in de browser en wacht tot de beveiligings-
    controle (TSPD) voorbij is. Geeft de uiteindelijke HTML terug."""
    page.goto(url, wait_until="domcontentloaded", timeout=60000)
    for _ in range(8):
        page.wait_for_timeout(4000)
        try:
            html = page.content()
        except Exception:
            continue       # de controle laadt de pagina net opnieuw
        if "TSPD" not in html and len(html) > 500:
            return html
    try:
        return page.content()
    except Exception:
        return ""


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

    from playwright.sync_api import sync_playwright
    pw = sync_playwright().start()
    browser = pw.chromium.launch(
        headless=True,
        args=["--disable-blink-features=AutomationControlled"])
    context = browser.new_context(
        user_agent=HEADERS["User-Agent"], locale="nl-BE",
        timezone_id="Europe/Brussels",
        viewport={"width": 1366, "height": 850})
    page = context.new_page()

    vandaag = date.today()
    for terug in range(DAGEN_TERUGKIJKEN + 1):
        dag = (vandaag - timedelta(days=terug)).isoformat()
        html, url = "", None
        for sjabloon in LIJST_URLS:
            url = sjabloon.format(datum=dag)
            print(f"[{dag}] {url}")
            try:
                html = haal_pagina(page, url)
            except Exception as fout:
                print(f"[{dag}] ophalen mislukt: {fout}")
                continue
            if len(html) > 500 and "TSPD" not in html:
                break
            print(f"[{dag}] lege pagina of beveiligingscontrole, "
                  "volgend adres proberen")
        if debug:
            with open(f"debug_{dag}.html", "w", encoding="utf-8") as f:
                f.write(html)
        if not html:
            continue
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

    browser.close()
    pw.stop()

    items = (nieuw + items)[:MAX_ITEMS]
    bewaar_items(items)
    schrijf_feed(items)
    print(f"Klaar: {len(nieuw)} nieuw, {len(items)} in feed.")
    if not items:
        print("Niets gevonden: draai opnieuw met --debug en stuur "
              "een debug_*.html door om het script bij te stellen.")


if __name__ == "__main__":
    main()
