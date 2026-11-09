import os
import json
import time
import urllib.request
import urllib.parse
import urllib.error
from datetime import datetime, timezone

BASE_URL = "https://data-api.binance.vision"
QUOTE_ASSETS = {"USDT", "TRY"}
VOLUME_THRESHOLD = 2.0
REQUEST_DELAY = 0.12


def get_json(endpoint, params=None):
    query = urllib.parse.urlencode(params or {})
    url = f"{BASE_URL}{endpoint}"
    if query:
        url += "?" + query

    request = urllib.request.Request(
        url,
        headers={"User-Agent": "CryptoScanner/1.0"}
    )

    for attempt in range(3):
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                return json.loads(response.read().decode("utf-8"))

        except urllib.error.HTTPError as error:
            if error.code in (418, 429) and attempt < 2:
                time.sleep(5 * (attempt + 1))
                continue
            raise

    raise RuntimeError("Binance API bağlantısı başarısız.")


def send_telegram(message):
    token = os.environ["TELEGRAM_BOT_TOKEN"]
    chat_id = os.environ["TELEGRAM_CHAT_ID"]

    url = f"https://api.telegram.org/bot{token}/sendMessage"

    data = urllib.parse.urlencode({
        "chat_id": chat_id,
        "text": message
    }).encode("utf-8")

    request = urllib.request.Request(url, data=data)

    with urllib.request.urlopen(request, timeout=30) as response:
        result = json.loads(response.read().decode("utf-8"))

    if not result.get("ok"):
        raise RuntimeError("Telegram mesajı gönderilemedi.")


def discover_pairs():
    info = get_json("/api/v3/exchangeInfo")
    pairs = []

    for item in info["symbols"]:
        if (
            item["status"] == "TRADING"
            and item["quoteAsset"] in QUOTE_ASSETS
            and item.get("isSpotTradingAllowed", True)
        ):
            pairs.append({
                "symbol": item["symbol"],
                "quote": item["quoteAsset"]
            })

    return pairs


def scan_pair(pair, ticker):
    symbol = pair["symbol"]

    candles = get_json(
        "/api/v3/klines",
        {
            "symbol": symbol,
            "interval": "1d",
            "limit": 31
        }
    )

    now_ms = int(time.time() * 1000)

    completed = [
        candle for candle in candles
        if int(candle[6]) < now_ms
    ][-30:]

    if len(completed) < 30:
        return None

    volumes = [float(candle[7]) for candle in completed]
    average_volume = sum(volumes) / len(volumes)
    current_volume = float(ticker["quoteVolume"])

    if average_volume <= 0:
        return None

    ratio = current_volume / average_volume

    if ratio < VOLUME_THRESHOLD:
        return None

    return {
        "symbol": symbol,
        "quote": pair["quote"],
        "ratio": ratio,
        "volume": current_volume,
        "change": float(ticker["priceChangePercent"])
    }


def main():
    print("KRIPTO MASASI - TUM SPOT PARITELER")
    print("Tarama başladı...")

    pairs = discover_pairs()
    print(f"Bulunan USDT ve TRY paritesi: {len(pairs)}")

    tickers = get_json("/api/v3/ticker/24hr")
    ticker_map = {item["symbol"]: item for item in tickers}

    alerts = []
    scanned = 0
    failed = 0

    for pair in pairs:
        symbol = pair["symbol"]
        ticker = ticker_map.get(symbol)

        if ticker is None:
            continue

        try:
            result = scan_pair(pair, ticker)
            scanned += 1

            if result:
                alerts.append(result)
                print(
                    f"ALARM: {symbol} | "
                    f"{result['ratio']:.2f} kat hacim"
                )

        except Exception as error:
            failed += 1
            print(f"{symbol}: Tarama hatası: {error}")

        time.sleep(REQUEST_DELAY)

    alerts.sort(key=lambda item: item["ratio"], reverse=True)

    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    lines = [
        "📊 KRİPTO MASASI - SPOT TARAMA",
        f"Zaman: {now}",
        f"Tarama yapılan parite: {scanned}",
        f"Hata alınan parite: {failed}",
        f"Hacim alarmı: {len(alerts)}",
        ""
    ]

    for item in alerts:
        lines.extend([
            f"🚨 {item['symbol']}",
            f"Hacim artışı: {item['ratio']:.2f} kat",
            f"24 saatlik hacim: {item['volume']:,.2f} {item['quote']}",
            f"Fiyat değişimi: %{item['change']:.2f}",
            ""
        ])

    if not alerts:
        lines.append("Şimdilik 2 kat hacim eşiğini aşan parite yok.")

    message = "\n".join(lines)

    chunks = []
    current = ""

    for line in message.split("\n"):
        if len(current) + len(line) + 1 > 3500:
            chunks.append(current)
            current = ""

        current += line + "\n"

    if current:
        chunks.append(current)

    for chunk in chunks:
        send_telegram(chunk)

    print("Telegram mesajı gönderildi.")
    print("Tarama tamamlandı.")


if __name__ == "__main__":
    main()
