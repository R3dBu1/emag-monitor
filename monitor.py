import sqlite3
import requests
import smtplib
import json
import re
import time
import logging
import schedule
import os
from bs4 import BeautifulSoup
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from datetime import datetime
from config import CONFIG

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler("monitor.log", encoding="utf-8"),
        logging.StreamHandler()
    ]
)
log = logging.getLogger(__name__)

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/122.0.0.0 Safari/537.36",
    "Accept-Language": "ro-RO,ro;q=0.9",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}


def init_db():
    conn = sqlite3.connect("emag_monitor.db")
    c = conn.cursor()
    c.execute("""
        CREATE TABLE IF NOT EXISTS produse (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            url TEXT UNIQUE,
            nume_produs TEXT,
            seller_curent TEXT,
            ultima_verificare TEXT
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS istoric (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            url TEXT,
            seller_vechi TEXT,
            seller_nou TEXT,
            tip_eveniment TEXT,
            data TEXT
        )
    """)
    conn.commit()
    conn.close()
    log.info("Baza de date initializata.")


def get_toate_produsele():
    conn = sqlite3.connect("emag_monitor.db")
    c = conn.cursor()
    c.execute("SELECT url, nume_produs, seller_curent FROM produse")
    rows = c.fetchall()
    conn.close()
    return rows


def update_produs(url, nume, seller):
    conn = sqlite3.connect("emag_monitor.db")
    c = conn.cursor()
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    c.execute("""
        INSERT INTO produse (url, nume_produs, seller_curent, ultima_verificare)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(url) DO UPDATE SET
            nume_produs=excluded.nume_produs,
            seller_curent=excluded.seller_curent,
            ultima_verificare=excluded.ultima_verificare
    """, (url, nume, seller, now))
    conn.commit()
    conn.close()


def salveaza_in_istoric(url, seller_vechi, seller_nou, tip):
    conn = sqlite3.connect("emag_monitor.db")
    c = conn.cursor()
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    c.execute("""
        INSERT INTO istoric (url, seller_vechi, seller_nou, tip_eveniment, data)
        VALUES (?, ?, ?, ?, ?)
    """, (url, seller_vechi, seller_nou, tip, now))
    conn.commit()
    conn.close()


def adauga_produs(url):
    conn = sqlite3.connect("emag_monitor.db")
    c = conn.cursor()
    try:
        c.execute(
            "INSERT OR IGNORE INTO produse (url, ultima_verificare) VALUES (?, ?)",
            (url, datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
        )
        conn.commit()
        log.info("Produs adaugat: " + url)
    except Exception as e:
        log.error("Eroare la adaugare: " + str(e))
    finally:
        conn.close()


def import_urls_din_fisier(path="produse.txt"):
    if not os.path.exists(path):
        log.warning("Fisierul " + path + " nu exista.")
        return
    with open(path, "r", encoding="utf-8") as f:
        urls = [line.strip() for line in f if line.strip() and line.startswith("http")]
    for url in urls:
        adauga_produs(url)
    log.info("Importate " + str(len(urls)) + " URL-uri din " + path)


def curat(text):
    if not text:
        return ""
    text = str(text)
    text = text.replace("\xa0", " ")
    text = text.replace("\u200b", "")
    text = text.strip()
    return text


def get_seller_emag(url):
    try:
        resp = requests.get(url, headers=HEADERS, timeout=15)
        if resp.status_code != 200:
            log.warning("Status " + str(resp.status_code) + " pentru " + url)
            return None, None

        soup = BeautifulSoup(resp.text, "html.parser")
        page_text = resp.text

        title_tag = soup.find("h1")
        nume = curat(title_tag.get_text(strip=True)) if title_tag else "Produs necunoscut"

        seller = None

        for script in soup.find_all("script", {"type": "application/ld+json"}):
            try:
                data = json.loads(script.string or "")
                items = data if isinstance(data, list) else [data]
                for item in items:
                    offers = item.get("offers", {})
                    if isinstance(offers, list):
                        offers = offers[0] if offers else {}
                    s = offers.get("seller", {})
                    name = s.get("name", "") if isinstance(s, dict) else str(s)
                    if name and name not in ("None", "null", ""):
                        seller = curat(name)
                        break
            except Exception:
                continue
            if seller:
                break

        if not seller:
            for pattern in [
                r'"seller_name"\s*:\s*"([^"]{2,60})"',
                r'"sellerName"\s*:\s*"([^"]{2,60})"',
                r'"vendor"\s*:\s*"([^"]{2,60})"',
                r'"sold_by"\s*:\s*"([^"]{2,60})"',
                r'"merchantName"\s*:\s*"([^"]{2,60})"',
            ]:
                match = re.search(pattern, page_text)
                if match:
                    candidate = curat(match.group(1))
                    if candidate and candidate.lower() not in ("null", "undefined", ""):
                        seller = candidate
                        break

        if not seller:
            seller_link = soup.find("a", href=lambda h: h and "/seller/" in str(h))
            if seller_link:
                text = curat(seller_link.get_text(strip=True))
                if text:
                    seller = text

        if not seller:
            plain_text = soup.get_text(" ", strip=True)
            for pattern in [
                r'[Vv][ai]ndut de\s*:?\s*([A-Za-z0-9 &.\-_]{2,50})',
                r'[Ss]old by\s*:?\s*([A-Za-z0-9 &.\-_]{2,50})',
            ]:
                match = re.search(pattern, plain_text)
                if match:
                    candidate = curat(match.group(1))
                    if len(candidate) > 1:
                        seller = candidate
                        break

        if not seller:
            seller = "Necunoscut"
            log.warning("Seller negasit pentru: " + url[:60])

        return curat(nume), curat(seller)

    except Exception as e:
        log.error("Eroare scraping " + url + ": " + str(e))
        return None, None


EMAG_NAMES = ["emag", "emag.ro"]


def este_emag(seller):
    return seller and seller.strip().lower() in EMAG_NAMES


def detecteaza_evenimente(url, seller_vechi, seller_nou):
    events = []
    if seller_vechi is None:
        return events
    if seller_vechi != seller_nou:
        events.append({
            "tip": "SCHIMBARE_SELLER",
            "mesaj": "Seller schimbat: " + str(seller_vechi) + " -> " + str(seller_nou)
        })
        if este_emag(seller_nou) and not este_emag(seller_vechi):
            events.append({
                "tip": "EMAG_CASTIGAT",
                "mesaj": "eMAG a castigat Buy Button! (de la " + str(seller_vechi) + ")"
            })
        if este_emag(seller_vechi) and not este_emag(seller_nou):
            events.append({
                "tip": "EMAG_PIERDUT",
                "mesaj": "eMAG a pierdut Buy Button! (preluat de " + str(seller_nou) + ")"
            })
    return events


def trimite_email(alerte):
    if not alerte:
        return

    cfg = CONFIG["email"]
    now = datetime.now().strftime("%d.%m.%Y %H:%M")

    rows_html = ""
    for a in alerte:
        culoare = "#d4edda" if "CASTIGAT" in a["tip"] else ("#f8d7da" if "PIERDUT" in a["tip"] else "#fff3cd")
        emoji = "OK" if "CASTIGAT" in a["tip"] else ("X" if "PIERDUT" in a["tip"] else "~")
        tip = a["tip"].replace("_", " ")
        nume = curat(a["nume"])[:60]
        sv = curat(a["seller_vechi"])
        sn = curat(a["seller_nou"])
        url = a["url"]
        rows_html += (
            "<tr style='background:" + culoare + ";'>"
            "<td style='padding:8px;border-bottom:1px solid #ddd;'>" + emoji + " " + tip + "</td>"
            "<td style='padding:8px;border-bottom:1px solid #ddd;'>" + nume + "</td>"
            "<td style='padding:8px;border-bottom:1px solid #ddd;'>" + sv + "</td>"
            "<td style='padding:8px;border-bottom:1px solid #ddd;font-weight:bold;'>" + sn + "</td>"
            "<td style='padding:8px;border-bottom:1px solid #ddd;'><a href='" + url + "'>Vezi</a></td>"
            "</tr>"
        )

    html = (
        "<html><body style='font-family:Arial,sans-serif;max-width:900px;margin:auto;'>"
        "<h2 style='color:#e30613;'>eMAG Monitor - Raport " + now + "</h2>"
        "<p>S-au detectat <strong>" + str(len(alerte)) + " schimbari</strong>:</p>"
        "<table style='width:100%;border-collapse:collapse;font-size:14px;'>"
        "<thead><tr style='background:#e30613;color:white;'>"
        "<th style='padding:10px;'>Eveniment</th>"
        "<th style='padding:10px;'>Produs</th>"
        "<th style='padding:10px;'>Seller Vechi</th>"
        "<th style='padding:10px;'>Seller Nou</th>"
        "<th style='padding:10px;'>Link</th>"
        "</tr></thead>"
        "<tbody>" + rows_html + "</tbody>"
        "</table>"
        "<p style='color:#888;font-size:12px;margin-top:20px;'>Trimis automat de eMAG Monitor</p>"
        "</body></html>"
    )

    msg = MIMEMultipart("alternative")
    msg["Subject"] = "eMAG Monitor: " + str(len(alerte)) + " schimbari - " + now
    msg["From"] = cfg["from"]
    msg["To"] = cfg["to"]
    msg.attach(MIMEText(html, "html", "utf-8"))

    try:
        with smtplib.SMTP_SSL(cfg["smtp_server"], cfg["smtp_port"]) as server:
            server.login(cfg["username"], cfg["password"])
            server.sendmail(cfg["from"], cfg["to"], msg.as_bytes())
        log.info("Email trimis cu " + str(len(alerte)) + " alerte.")
    except Exception as e:
        log.error("Eroare trimitere email: " + str(e))


def ruleaza_verificare():
    log.info("=" * 50)
    log.info("Incep verificarea produselor...")
    produse = get_toate_produsele()

    if not produse:
        log.warning("Niciun produs in baza de date. Adauga URL-uri in produse.txt")
        return

    alerte_sesiune = []

    for i, (url, nume_salvat, seller_salvat) in enumerate(produse):
        log.info("[" + str(i + 1) + "/" + str(len(produse)) + "] Verific: " + url[:60] + "...")

        nume_nou, seller_nou = get_seller_emag(url)

        if seller_nou is None:
            log.warning("Nu am putut extrage seller pentru " + url)
            time.sleep(CONFIG["pauza_secunde"])
            continue

        log.info("  Seller gasit: " + seller_nou)
        events = detecteaza_evenimente(url, seller_salvat, seller_nou)

        for ev in events:
            salveaza_in_istoric(url, seller_salvat, seller_nou, ev["tip"])
            alerte_sesiune.append({
                "tip": ev["tip"],
                "mesaj": ev["mesaj"],
                "url": url,
                "nume": nume_nou or nume_salvat or "Necunoscut",
                "seller_vechi": seller_salvat or "N/A",
                "seller_nou": seller_nou,
            })
            log.info("  ALERT: " + ev["mesaj"])

        update_produs(url, nume_nou or nume_salvat, seller_nou)
        time.sleep(CONFIG["pauza_secunde"])

    log.info("Verificare finalizata. " + str(len(alerte_sesiune)) + " alerte detectate.")

    if alerte_sesiune:
        trimite_email(alerte_sesiune)
    else:
        log.info("Nicio schimbare detectata. Niciun email trimis.")


def main():
    log.info("eMAG Monitor pornit!")
    init_db()
    import_urls_din_fisier("produse.txt")
    ruleaza_verificare()
    log.info("Verificare completa. GitHub Actions va rula din nou in 2 ore.")


if __name__ == "__main__":
    main()
