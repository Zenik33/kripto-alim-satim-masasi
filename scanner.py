import json
import time
import os
import urllib.request
import urllib.parse
from datetime import datetime, timezone

BASE_URL = "https://api.binance.com"
SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT"]
THRESHOLD = 2.0


def send_telegram(message):
    token = os.environ["TELEGRAM_BOT_TOKEN"]
    chat_id = os.environ["TELEGRAM_CHAT_ID"]

    url = f"https://api.telegram.org/bot{token}/sendMessage"
    data = urllib.parse.urlencode({
        "chat_id": chat_id,
        "text": message
    }).encode("utf-8")

    request = urllib.request.Request(url, data=data)
    with urllib.request.urlopen(request, timeout=15) as response:
        result = json.loads(response.read().decode("utf-8"))

    if not result.get("ok"):
        raise RuntimeError("Telegram mesajı gönderilemedi")

    print("Telegram mesajı başarıyla gönderildi.")


def binance_get(endpoint, params):
    query = urllib.parse.urlencode(params)
    url = f"{BASE_URL}{endpoint}?{query}"

    request = urllib.request.Request(
        url,
        headers={"User-Agent": "CryptoDeskScanner/1.0"}
    )
    with urllib.request.urlopen(request, timeout=15) as response:
        return json.loads(response.read().decode("utf-8"))


def scan_symbol(symbol):
    ticker = binance_get(
        "/api/v3/ticker/24hr",
        {"symbol": symbol}
    )

    candles = binance_get(
        "/api/v3/klines",
        {"symbol": symbol, "interval": "1d", "limit": 31}
    )

    now_ms = int(time.time() * 1000)
    completed = [
        candle for candle in candles
        if int(candle[6]) < now_ms
    ][-30:]

    if len(completed) < 30:
        raise ValueError("30 tamamlanmış günlük veri bulunamadı")

    daily_volumes = [float(candle[7]) for candle in completed]
    average_volume = sum(daily_volumes) / len(daily_volumes)

    current_volume = float(ticker["quoteVolume"])
    price_change = float(ticker["priceChangePercent"])
    multiple = current_volume / average_volume if average_volume else 0

    if multiple >= THRESHOLD:
        return {
            "varlik": symbol,
            "hacim_kati": round(multiple, 2),
            "fiyat_degisim_yuzdesi": round(price_change, 2),
            "son_24_saat_hacmi_usdt": round(current_volume, 2),
            "30_gunluk_ortalama_usdt": round(average_volume, 2),
            "zaman_utc": datetime.now(timezone.utc).isoformat()
        }

    return None


def main():
    print("KRIPTO MASASI - TARAYICI")
    print("Tarama başladı...\n")

    found = []

    for symbol in SYMBOLS:
        try:
            result = scan_symbol(symbol)

            if result:
                found.append(result)
                print("HACİM ALARMI:")
                print(json.dumps(result, indent=2, ensure_ascii=False))
            else:
                print(f"{symbol}: Olağan dışı hacim yok.")

        except Exception as error:
            print(f"{symbol}: Veri alınamadı: {error}")

        time.sleep(0.3)

    if found:
        message = "🚨 KRİPTO HACİM ALARMI 🚨\n\n"
        for item in found:
            message += (
                f"Varlık: {item['varlik']}\n"
                f"Hacim: {item['hacim_kati']} kat\n"
                f"Fiyat değişimi: %{item['fiyat_degisim_yuzdesi']}\n"
                f"24 saatlik hacim: {item['son_24_saat_hacmi_usdt']:,.2f} USDT\n\n"
            )

        try:
            send_telegram(message)
        except Exception as error:
            print(f"Telegram gönderim hatası: {error}")
    else:
        print("\nŞu anda eşik değerini aşan varlık bulunamadı.")

    print("\nTarama tamamlandı.")


if __name__ == "__main__":
    main()
