import os
import json
import urllib.request

token = os.environ["TELEGRAM_BOT_TOKEN"]
url = f"https://api.telegram.org/bot{token}/getUpdates"

try:
    with urllib.request.urlopen(url, timeout=20) as response:
        data = json.loads(response.read().decode())

    if not data.get("ok"):
        print("Telegram sorgusu başarısız.")
    else:
        found = set()
        for update in data.get("result", []):
            message = update.get("message") or update.get("channel_post")
            if message:
                chat = message.get("chat", {})
                if chat.get("type") in ("group", "supergroup"):
                    key = (chat.get("id"), chat.get("title"))
                    if key not in found:
                        found.add(key)
                        print(f"Grup: {chat.get('title')} | Chat ID: {chat.get('id')}")

        if not found:
            print("Grup mesajı bulunamadı. Telegram grubuna yeniden komut gönderip tekrar deneyin.")
except Exception:
    print("Sorgu sırasında hata oluştu. Tokeni veya ham yanıtı paylaşmayın.")
