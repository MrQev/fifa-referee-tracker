import os
import time
import requests
from playwright.sync_api import sync_playwright

X_PROFILE_URL = "https://x.com/FIFAcom"
KEYWORDS = ["match officials", "referee", "rozhodčí", "referees", "appointment"]
DB_FILE = "last_tweet_id.txt"

X_AUTH_TOKEN = os.environ.get("X_AUTH_TOKEN")
GOOGLE_CHAT_WEBHOOK = os.environ.get("GOOGLE_CHAT_WEBHOOK")

def send_notification(text, url):
    if not GOOGLE_CHAT_WEBHOOK:
        print("Chyba: GOOGLE_CHAT_WEBHOOK nenalezen!")
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

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context()
        context.add_cookies([{
            "name": "auth_token",
            "value": X_AUTH_TOKEN,
            "domain": ".x.com",
            "path": "/"
        }])
        
        page = context.new_page()
        print(f"Otevírám {X_PROFILE_URL}...")
        page.goto(X_PROFILE_URL)
        
        # Počkáme na vykreslení prvního tweetu
        page.wait_for_selector('article[data-testid="tweet"]', timeout=15000)
        
        # --- NOVINKA: Odrolování dolů pro načtení starších tweetů ---
        print("Skroluji dolů pro načtení starší historie...")
        for _ in range(3):
            page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            time.sleep(2) # Čas na načtení další dávky tweetů
            
        tweets = page.locator('article[data-testid="tweet"]').all()
        print(f"Celkem nalezeno tweetů k analýze: {len(tweets)}")
        
        last_processed_id = ""
        if os.path.exists(DB_FILE):
            with open(DB_FILE, "r") as f:
                last_processed_id = f.read().strip()
        print(f"ID posledního zpracovaného tweetu z minula: '{last_processed_id}'")

        found_match = False
        for i, tweet in enumerate(tweets, 1):
            try:
                text_element = tweet.locator('div[data-testid="tweetText"]')
                if text_element.count() == 0:
                    continue
                tweet_text = text_element.inner_text()
                
                link_element = tweet.locator('a[href*="/status/"]').first
                href = link_element.get_attribute("href")
                tweet_id = href.split("/status/")[1].split("?")[0]
                tweet_url = f"https://x.com{href}"
                
                # --- NOVINKA: Debug výpis pro každý kontrolovaný tweet ---
                short_text = tweet_text.replace('\n', ' ')[:50]
                print(f" [{i}] Kontrola ID {tweet_id} | Text: {short_text}...")
                
                if tweet_id == last_processed_id:
                    print(f"-> Stop: Narazili jsme na ID z minula ({tweet_id}).")
                    break
                
                text_lower = tweet_text.lower()
                if any(kw in text_lower for kw in KEYWORDS):
                    print(f"-> Žhavá stopa! Nalezen odpovídající tweet: {tweet_id}")
                    send_notification(tweet_text, tweet_url)
                    
                    with open(DB_FILE, "w") as f:
                        f.write(tweet_id)
                    found_match = True
                    break # Zpracovali jsme nejnovější shodu a končíme
                    
            except Exception as e:
                print(f"Chyba parsování u tweetu č. {i}: {e}")
                continue
        
        if not found_match:
            print("Analýza dokončena. Žádný nový tweet nevyhovoval klíčovým slovům.")
            
        browser.close()

if __name__ == "__main__":
    check_tweets()
