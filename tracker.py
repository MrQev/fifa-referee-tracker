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
        
        # Počkáme na načtení tweetů a dáme tomu pevnou pauzu na stabilizaci
        page.wait_for_selector('article[data-testid="tweet"]', timeout=15000)
        time.sleep(5)
        
        # ŽÁDNÉ SCROLLOVÁNÍ - bereme rovnou to, co je nahoře
        tweets = page.locator('article[data-testid="tweet"]').all()
        print(f"Načteno nejnovějších tweetů z vrchu stránky: {len(tweets)}")
        
        last_processed_id = ""
        if os.path.exists(DB_FILE):
            with open(DB_FILE, "r") as f:
                last_processed_id = f.read().strip()

        found_match = False
        valid_tweet_counter = 0
        
        for tweet in tweets:
            try:
                text_element = tweet.locator('div[data-testid="tweetText"]')
                if text_element.count() == 0:
                    continue
                tweet_text = text_element.inner_text()
                
                # Zkusíme vytáhnout čas tweetu pro lepší přehled v logu
                time_element = tweet.locator('time')
                tweet_time = time_element.get_attribute("datetime") if time_element.count() > 0 else "Neznámý čas"
                
                link_element = tweet.locator('a[href*="/status/"]').first
                href = link_element.get_attribute("href")
                tweet_id = href.split("/status/")[1].split("?")[0]
                tweet_url = f"https://x.com{href}"
                
                valid_tweet_counter += 1
                short_text = tweet_text.replace('\n', ' ')[:40]
                print(f" [{valid_tweet_counter}] ID: {tweet_id} | Čas: {tweet_time} | Text: {short_text}...")
                
                if tweet_id == last_processed_id:
                    print(f"-> Stop: Narazili jsme na ID z minula ({tweet_id}).")
                    break
                
                text_lower = tweet_text.lower()
                if any(kw in text_lower for kw in KEYWORDS):
                    print(f"-> Nalezen odpovídající tweet: {tweet_id}")
                    send_notification(tweet_text, tweet_url)
                    
                    with open(DB_FILE, "w") as f:
                        f.write(tweet_id)
                    found_match = True
                    break
                    
            except Exception as e:
                continue
        
        if not found_match and valid_tweet_counter > 0:
            print("Analýza dokončena. Žádný z vrchních tweetů nevyhovoval klíčovým slovům.")
            
        browser.close()

if __name__ == "__main__":
    check_tweets()
