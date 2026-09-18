import os
import sys
import time
import argparse
import unicodedata
import requests
from datetime import datetime, timedelta, timezone
from playwright.sync_api import sync_playwright

# --- Definice sledovaných zdrojů (účet + klíčová slova + vlastní DB soubor) ---
SOURCES = [
    {
        "name": "FIFA",
        "profile_url": "https://x.com/FIFAcom",
        "keywords": ["match officials", "referee", "rozhodčí", "referees", "appointment"],
        "db_file": "last_tweet_id_fifa.txt",
        "notification_title": "🚨 *FIFA zveřejnila rozhodčí!*",
    },
    {
        "name": "RFEF",
        "profile_url": "https://x.com/rfef",
        "keywords": ["designaciones"],
        "db_file": "last_tweet_id_rfef.txt",
        "notification_title": "🚨 *RFEF zveřejnila designaciones!*",
    },
]

X_AUTH_TOKEN = os.environ.get("X_AUTH_TOKEN")
GOOGLE_CHAT_WEBHOOK = os.environ.get("GOOGLE_CHAT_WEBHOOK")


def resolve_sources():
    """
    Vybere, které zdroje se mají v tomto běhu zpracovat.

    Priorita:
    1. Argument příkazové řádky --source (jméno nebo seznam oddělený čárkou,
       např. `--source rfef` nebo `--source fifa,rfef`).
    2. Proměnná prostředí SOURCE (stejný formát, hodí se pro workflow_dispatch input).
    3. Pokud nic není zadáno -> zpracují se VŠECHNY zdroje.

    Jméno zdroje se porovnává bez ohledu na velikost písmen podle klíče "name".
    """
    parser = argparse.ArgumentParser(description="FIFA/RFEF tweet tracker")
    parser.add_argument(
        "--source",
        help="Jméno zdroje (nebo více oddělených čárkou) k zpracování, např. 'fifa' nebo 'fifa,rfef'. "
             "Pokud není zadáno, zpracují se všechny zdroje.",
        default=None,
    )
    args, _ = parser.parse_known_args()

    raw_selection = args.source or os.environ.get("SOURCE")

    if not raw_selection or raw_selection.strip().lower() in ("", "all"):
        return SOURCES

    requested_names = {name.strip().lower() for name in raw_selection.split(",") if name.strip()}
    selected = [s for s in SOURCES if s["name"].lower() in requested_names]

    known_names = {s["name"].lower() for s in SOURCES}
    unknown = requested_names - known_names
    if unknown:
        print(f"Varování: neznámé zdroje v --source/SOURCE, ignoruji: {', '.join(sorted(unknown))}")

    if not selected:
        print(f"Chyba: žádný ze zadaných zdrojů ({raw_selection}) nebyl nalezen. Dostupné zdroje: {', '.join(s['name'] for s in SOURCES)}")
        sys.exit(1)

    return selected


def normalize_text(text: str) -> str:
    """
    Převede stylizované Unicode varianty písmen (např. matematické
    tučné/kurzívní znaky používané v tweetech typu 𝗗𝗘𝗦𝗜𝗚𝗡𝗔𝗖𝗜𝗢𝗡𝗘𝗦)
    na běžná ASCII písmena, aby fungovalo porovnávání klíčových slov.
    """
    return unicodedata.normalize("NFKC", text)


def send_notification(title, text, url):
    if not GOOGLE_CHAT_WEBHOOK:
        print("Chyba: GOOGLE_CHAT_WEBHOOK chybí!")
        return
    try:
        payload = {"text": f"{title}\n\n{text}\n\n🔗 *Odkaz:* {url}"}
        response = requests.post(GOOGLE_CHAT_WEBHOOK, json=payload)
        if response.status_code == 200:
            print("Notifikace úspěšně odeslána do Google Chatu.")
        else:
            print(f"Google Chat error: {response.status_code} - {response.text}")
    except Exception as e:
        print(f"Chyba při odesílání: {e}")


def check_source(source, page, yesterday_start):
    profile_url = source["profile_url"]
    keywords = source["keywords"]
    db_file = source["db_file"]

    print(f"\n=== Zdroj: {source['name']} ({profile_url}) ===")
    print(f"Otevírám {profile_url}...")
    page.goto(profile_url)

    try:
        page.wait_for_selector('article[data-testid="tweet"]', timeout=20000)
    except Exception as e:
        # Uložíme screenshot a HTML pro diagnostiku, ať víme, co X místo timeline zobrazil
        debug_dir = "debug"
        os.makedirs(debug_dir, exist_ok=True)
        safe_name = source["name"].lower()
        screenshot_path = os.path.join(debug_dir, f"{safe_name}_timeout.png")
        html_path = os.path.join(debug_dir, f"{safe_name}_timeout.html")
        try:
            page.screenshot(path=screenshot_path, full_page=True)
            with open(html_path, "w", encoding="utf-8") as f:
                f.write(page.content())
            print(f"Diagnostika uložena do {screenshot_path} a {html_path}")
        except Exception as inner_e:
            print(f"Nepodařilo se uložit diagnostiku: {inner_e}")
        raise
    time.sleep(3)

    scraped_tweets = {}
    reached_end_of_timeframe = False
    scroll_attempts = 0
    max_scroll_attempts = 15

    print("Spouštím postupné scrollování a sběr dnešních/včerejších tweetů...")

    while not reached_end_of_timeframe and scroll_attempts < max_scroll_attempts:
        visible_tweets = page.locator('article[data-testid="tweet"]').all()

        for tweet in visible_tweets:
            try:
                if tweet.locator('text="Pinned"').count() > 0 or tweet.locator('text="Přišpendlený"').count() > 0:
                    continue

                link_element = tweet.locator('a[href*="/status/"]').first
                href = link_element.get_attribute("href")
                tweet_id = href.split("/status/")[1].split("?")[0]

                if tweet_id in scraped_tweets:
                    continue

                text_element = tweet.locator('div[data-testid="tweetText"]')
                tweet_text = text_element.inner_text() if text_element.count() > 0 else ""

                time_element = tweet.locator('time')
                tweet_time_str = time_element.get_attribute("datetime") if time_element.count() > 0 else None

                if tweet_time_str:
                    tweet_date = datetime.fromisoformat(tweet_time_str.replace("Z", "+00:00"))

                    if tweet_date < yesterday_start:
                        print(f"-> Dosažen tweet z předvčerejška ({tweet_date.strftime('%Y-%m-%d')}). Zastavuji sběr.")
                        reached_end_of_timeframe = True
                        break

                    scraped_tweets[tweet_id] = {
                        "text": tweet_text,
                        "url": f"https://x.com{href}",
                        "date": tweet_date,
                    }

            except Exception:
                continue

        if reached_end_of_timeframe:
            break

        page.evaluate("window.scrollBy(0, 600)")
        time.sleep(2)
        scroll_attempts += 1

    print(f"Sběr dokončen. Unikátních tweetů za sledované období: {len(scraped_tweets)}")

    processed_ids = set()
    if os.path.exists(db_file):
        with open(db_file, "r") as f:
            processed_ids = {line.strip() for line in f if line.strip()}
    print(f"Celkem v databázi uložených ID z minulosti ({db_file}): {len(processed_ids)}")

    sorted_tweets = sorted(scraped_tweets.items(), key=lambda x: x[1]["date"])

    new_matches_found = False
    counter = 0

    for tweet_id, data in sorted_tweets:
        counter += 1
        short_text = data["text"].replace('\n', ' ')[:40]
        print(f" [{counter}] ID: {tweet_id} | Datum: {data['date'].strftime('%Y-%m-%d %H:%M')} | Text: {short_text}...")

        if tweet_id in processed_ids:
            print(f"-> Ignorováno: ID {tweet_id} už v historii existuje.")
            continue

        # Normalizace řeší i stylizované Unicode znaky (např. 𝗗𝗘𝗦𝗜𝗚𝗡𝗔𝗖𝗜𝗢𝗡𝗘𝗦)
        text_normalized = normalize_text(data["text"]).lower()
        if any(kw.lower() in text_normalized for kw in keywords):
            print(f"-> 🎯 NOVÁ SHODA! Nalezen odpovídající tweet: {tweet_id}")
            send_notification(source["notification_title"], data["text"], data["url"])

            with open(db_file, "a") as f:
                f.write(f"{tweet_id}\n")

            processed_ids.add(tweet_id)
            new_matches_found = True

    if not new_matches_found and len(scraped_tweets) > 0:
        print("Analýza dokončena. Žádný nový tweet nevyhovoval podmínkám.")


def check_tweets():
    if not X_AUTH_TOKEN:
        print("Chyba: X_AUTH_TOKEN chybí!")
        return

    sources_to_run = resolve_sources()
    print(f"Zdroje k zpracování v tomto běhu: {', '.join(s['name'] for s in sources_to_run)}")

    now_utc = datetime.now(timezone.utc)
    yesterday_start = (now_utc - timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    print(f"Hledáme tweety publikované od: {yesterday_start.strftime('%Y-%m-%d %H:%M:%S')} UTC")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        )

        context.add_cookies([{
            "name": "auth_token",
            "value": X_AUTH_TOKEN,
            "domain": ".x.com",
            "path": "/"
        }])

        page = context.new_page()

        for source in sources_to_run:
            try:
                check_source(source, page, yesterday_start)
            except Exception as e:
                print(f"Chyba při zpracování zdroje {source['name']}: {e}")

        browser.close()


if __name__ == "__main__":
    check_tweets()
