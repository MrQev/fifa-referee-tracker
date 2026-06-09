import os
import time
import requests
from playwright.sync_api import sync_playwright

X_PROFILE_URL = "https://x.com/FIFAcom"
KEYWORDS = ["match officials", "referee", "referees", "appointment"]
DB_FILE = "last_tweet_id.txt"

# Načtení tajných proměnných z GitHubu
X_AUTH_TOKEN = os.environ.get("X_AUTH_TOKEN")
GOOGLE_CHAT_WEBHOOK = os.environ.get("GOOGLE_CHAT_WEBHOOK")

def send_notification(text, url):
    if not GOOGLE_CHAT_WEBHOOK:
        print("Chyba: GOOGLE_CHAT_WEBHOOK nenalezen v prostředí!")
        return
        
    try:
        # Google Chat podporuje základní markdown formátování (* pro tučné)
        payload = {
            "text": f"🚨 *FIFA zveřejnila rozhodčí!*\n\n{text}\n\n🔗 *Odkaz na tweet:* {url}"
        }
        
        # Odeslání požadavku jako JSON do Google Chatu
        response = requests.post(GOOGLE_CHAT_WEBHOOK, json=payload)
        
        if response.status_code == 200:
            print("Zpráva úspěšně odeslána do Google Chatu.")
        else:
            print(f"Google Chat vrátil chybu: {response.status_code} - {response.text}")
            
    except Exception as e:
        print(f"Chyba notifikace: {e}")

def check_tweets():
    if not X_AUTH_TOKEN:
        print("Chyba: X_AUTH_TOKEN nenalezen v prostředí!")
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
        
        page.wait_for_selector('article[data-testid="tweet"]', timeout=15000)
        tweets = page.locator('article[data-testid="tweet"]').all()
        
        last_processed_id = ""
        if os.path.exists(DB_FILE):
            with open(DB_FILE, "r") as f:
                last_processed_id = f.read().strip()

        for tweet in tweets:
            try:
                text_element = tweet.locator('div[data-testid="tweetText"]')
                if text_element.count() == 0:
                    continue
                tweet_text = text_element.inner_text()
                
                link_element = tweet.locator('a[href*="/status/"]').first
                href = link_element.get_attribute("href")
                tweet_id = href.split("/status/")[1].split("?")[0]
                tweet_url = f"https://x.com{href}"
                
                if tweet_id == last_processed_id:
                    print("Narazili jsme na již zpracovaný tweet. Končím.")
                    break
                
                text_lower = tweet_text.lower()
                if any(kw in text_lower for kw in KEYWORDS):
                    print(f"Nalezen nový odpovídající tweet: {tweet_id}")
                    send_notification(tweet_text, tweet_url)
                    
                    with open(DB_FILE, "w") as f:
                        f.write(tweet_id)
                    break
                    
            except Exception as e:
                print(f"Chyba parsování tweetu: {e}")
                continue
                
        browser.close()

if __name__ == "__main__":
    check_tweets()
