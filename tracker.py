import os
import time
import requests
from datetime import datetime, timedelta, timezone
from playwright.sync_api import sync_playwright

X_PROFILE_URL = "https://x.com/FIFAcom"
KEYWORDS = ["match officials", "referee", "rozhodčí", "referees", "appointment"]
DB_FILE = "last_tweet_id.txt"

X_AUTH_TOKEN = os.environ.get("X_AUTH_TOKEN")
GOOGLE_CHAT_WEBHOOK = os.environ.get("GOOGLE_CHAT_WEBHOOK")

def send_notification(text, url):
    if not GOOGLE_CHAT_WEBHOOK:
        print("Chyba: GOOGLE_CHAT_WEBHOOK chybí!")
        return
    try:
        payload = {"text": f"🚨 *FIFA zveřejnila rozhodčí!*\n\n{text}\n\n🔗 *Odkaz:* {url}"}
        response = requests.post(GOOGLE_CHAT_WEBHOOK, json=payload)
        if response.status_code == 200:
            print("Notifikace úspěšně odeslána do Google Chatu.")
        else:
            print(f"Google Chat error: {response.status_code} - {response.text}")
    except Exception as e:
        print(f"Chyba při odesílání: {e}")

def check_tweets():
    if not X_AUTH_TOKEN:
        print("Chyba: X_AUTH_TOKEN chybí!")
        return

    # Definice časové hranice (začátek včerejšího dne v UTC)
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
        print(f"Otevírám {X_PROFILE_URL}...")
        page.goto(X_PROFILE_URL)
        
        page.wait_for_selector('article[data-testid="tweet"]', timeout=15000)
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
                            "date": tweet_date
                        }
                        
                except Exception:
                    continue
            
            if reached_end_of_timeframe:
                break
                
            page.evaluate("window.scrollBy(0, 600)")
            time.sleep(2)
            scroll_attempts += 1
            
        print(f"Sběr dokončen. Unikátních tweetů za sledované období: {len(scraped_tweets)}")
        
        # --- ZMĚNA: Načtení VŠECH dříve zpracovaných ID do množiny (set) pro bleskové vyhledávání ---
        processed_ids = set()
        if os.path.exists(DB_FILE):
            with open(DB_FILE, "r") as f:
                # Načte soubor řádek po řádku, ořízne mezery/odřádkování a vynechá prázdné řádky
                processed_ids = {line.strip() for line in f if line.strip()}
        print(f"Celkem v databázi uložených ID z minulosti: {len(processed_ids)}")

        # Seřadíme od nejstaršího po nejnovější
        sorted_tweets = sorted(scraped_tweets.items(), key=lambda x: x[1]["date"])
        
        new_matches_found = False
        counter = 0
        
        for tweet_id, data in sorted_tweets:
            counter += 1
            short_text = data["text"].replace('\n', ' ')[:40]
            print(f" [{counter}] ID: {tweet_id} | Datum: {data['date'].strftime('%Y-%m-%d %H:%M')} | Text: {short_text}...")
            
            # --- ZMĚNA: Kontrola, zda ID existuje KDEKOLIV v historii souboru ---
            if tweet_id in processed_ids:
                print(f"-> Ignorováno: ID {tweet_id} už v historii existuje.")
                continue
            
            text_lower = data["text"].lower()
            if any(kw in text_lower for kw in KEYWORDS):
                print(f"-> 🎯 NOVÁ SHODA! Nalezen odpovídající tweet: {tweet_id}")
                send_notification(data["text"], data["url"])
                
                # --- ZMĚNA: Zápis nového ID na konec souboru (režim 'a' jako append) ---
                with open(DB_FILE, "a") as f:
                    f.write(f"{tweet_id}\n")
                
                # Přidáme ho i do aktuální sady, abychom ho v rámci jednoho běhu znova nezpracovali
                processed_ids.add(tweet_id)
                new_matches_found = True

        if not new_matches_found and len(scraped_tweets) > 0:
            print("Analýza dokončena. Žádný nový tweet nevyhovoval podmínkám.")
            
        browser.close()

if __name__ == "__main__":
    check_tweets()
