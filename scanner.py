import concurrent.futures
import json
import os
import time
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

BASE_URL = "https://api.binance.com"
STATE_FILE = "paper_state.json"
STARTING_BALANCE = 100.0
MAX_TRADE_VALUE = 20.0
MAX_POSITIONS = 5
RISK_PER_TRADE = 0.01
MAX_DAILY_LOSS = 0.03
FEE_RATE = 0.001

ATR_STOP_MULTIPLIER = 1.5
ATR_TARGET_MULTIPLIER = 2.5
TRAILING_ACTIVATION = 0.05
TRAILING_DISTANCE = 0.025
BREAK_EVEN_ACTIVATION = 0.03
REENTRY_WAIT_HOURS = 6

USDT_PAIR_COUNT = 40
TRY_PAIR_COUNT = 20
BUY_SCORE_MIN = 75
SELL_SCORE_MIN = 70
MIN_VOLUME_RATIO = 1.10
MIN_RSI = 48
MAX_RSI = 68
MAX_WORKERS = 8
REPORT_INTERVAL_SECONDS = 7200
TEST_HOURS = 24

TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

STABLECOINS = {
    "USDC", "USDT", "BUSD", "TUSD", "FDUSD", "USDP",
    "DAI", "USDE", "USDS", "PYUSD", "EURC", "USTC"
}


def now():
    return datetime.now(timezone.utc)


def iso_now():
    return now().isoformat()


def telegram(message):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        print(message)
        return False
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = urllib.parse.urlencode({
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message[:4000]
    }).encode()
    request = urllib.request.Request(url, data=payload, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            return bool(json.loads(response.read().decode()).get("ok"))
    except Exception as exc:
        print("Telegram gonderim hatasi:", exc)
        return False


def http_get(path, params=None):
    url = BASE_URL + path
    if params:
        url += "?" + urllib.parse.urlencode(params)
    error = None
    for attempt in range(3):
        try:
            req = urllib.request.Request(
                url, headers={"User-Agent": "MasaTarayici/5.0"}
            )
            with urllib.request.urlopen(req, timeout=25) as response:
                return json.loads(response.read().decode())
        except Exception as exc:
            error = exc
            time.sleep(1.5 * (attempt + 1))
    raise RuntimeError(f"Binance API hatasi: {error}")


def get_usdt_try_rate():
    data = http_get("/api/v3/ticker/price", {"symbol": "USDTTRY"})
    return float(data["price"])


def get_symbols():
    info = http_get("/api/v3/exchangeInfo")
    tickers = http_get("/api/v3/ticker/24hr")
    allowed = {}
    for item in info.get("symbols", []):
        if (
            item.get("status") == "TRADING"
            and item.get("quoteAsset") in ("USDT", "TRY")
            and item.get("isSpotTradingAllowed", False)
            and item.get("baseAsset") not in STABLECOINS
        ):
            allowed[item["symbol"]] = item

    usdt_pairs, try_pairs = [], []
    prices = {}
    for ticker in tickers:
        symbol = ticker.get("symbol")
        if symbol not in allowed:
            continue
        try:
            volume = float(ticker.get("quoteVolume", 0))
            price = float(ticker.get("lastPrice", 0))
        except (TypeError, ValueError):
            continue
        if volume <= 0 or price <= 0:
            continue
        prices[symbol] = price
        row = (symbol, volume)
        if allowed[symbol]["quoteAsset"] == "USDT":
            usdt_pairs.append(row)
        else:
            try_pairs.append(row)

    usdt_pairs.sort(key=lambda x: x[1], reverse=True)
    try_pairs.sort(key=lambda x: x[1], reverse=True)
    selected = (
        [s for s, _ in usdt_pairs[:USDT_PAIR_COUNT]]
        + [s for s, _ in try_pairs[:TRY_PAIR_COUNT]]
    )
    return selected, prices


def get_klines(symbol, interval, limit=120):
    rows = http_get("/api/v3/klines", {
        "symbol": symbol, "interval": interval, "limit": limit
    })
    current_ms = int(time.time() * 1000)
    result = []
    for row in rows:
        candle = {
            "open_time": int(row[0]),
            "open": float(row[1]),
            "high": float(row[2]),
            "low": float(row[3]),
            "close": float(row[4]),
            "volume": float(row[5]),
            "close_time": int(row[6])
        }
        # Sadece kapanmis mumlar kullanilir.
        if candle["close_time"] < current_ms:
            result.append(candle)
    return result


def ema(values, period):
    if len(values) < period:
        return []
    alpha = 2.0 / (period + 1)
    out = [float(values[0])]
    for value in values[1:]:
        out.append(alpha * float(value) + (1 - alpha) * out[-1])
    return out


def rsi(values, period=14):
    if len(values) <= period:
        return 50.0
    changes = [values[i] - values[i - 1] for i in range(1, len(values))]
    gains = [max(x, 0.0) for x in changes]
    losses = [max(-x, 0.0) for x in changes]
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    for i in range(period, len(gains)):
        avg_gain = ((period - 1) * avg_gain + gains[i]) / period
        avg_loss = ((period - 1) * avg_loss + losses[i]) / period
    if avg_loss == 0:
        return 100.0 if avg_gain > 0 else 50.0
    rs = avg_gain / avg_loss
    return 100.0 - 100.0 / (1.0 + rs)


def atr(candles, period=14):
    if len(candles) <= period:
        return 0.0
    ranges = []
    for i in range(1, len(candles)):
        cur = candles[i]
        prev_close = candles[i - 1]["close"]
        ranges.append(max(
            cur["high"] - cur["low"],
            abs(cur["high"] - prev_close),
            abs(cur["low"] - prev_close)
        ))
    return sum(ranges[-period:]) / period


def analyze(candles):
    if len(candles) < 60:
        return None
    closes = [c["close"] for c in candles]
    volumes = [c["volume"] for c in candles]
    e20, e50 = ema(closes, 20), ema(closes, 50)
    e12, e26 = ema(closes, 12), ema(closes, 26)
    if not all((e20, e50, e12, e26)):
        return None

    macd = [a - b for a, b in zip(e12, e26)]
    signal = ema(macd, 9)
    if not signal:
        return None

    current_rsi = rsi(closes)
    buy = 0
    buy += 20 if closes[-1] > e20[-1] else 0
    buy += 20 if e20[-1] > e50[-1] else 0
    buy += 15 if 48 <= current_rsi <= 68 else 0
    buy += 15 if macd[-1] > signal[-1] else 0
    buy += 15 if closes[-1] > closes[-6] else 0
    buy += 10 if e20[-1] > e20[-4] else 0

    recent_vol = sum(volumes[-5:]) / 5
    base_vol = sum(volumes[-20:-5]) / 15
    vol_ratio = recent_vol / base_vol if base_vol > 0 else 0
    if vol_ratio >= MIN_VOLUME_RATIO and closes[-1] > closes[-2]:
        buy += 5

    sell = 0
    sell += 25 if closes[-1] < e20[-1] else 0
    sell += 25 if e20[-1] < e50[-1] else 0
    sell += 25 if macd[-1] < signal[-1] else 0
    sell += 25 if closes[-1] < closes[-6] else 0

    return {
        "buy_score": min(buy, 100),
        "sell_score": min(sell, 100),
        "rsi": current_rsi,
        "volume_ratio": vol_ratio,
        "atr": atr(candles),
        "close": closes[-1]
    }


def evaluate_symbol(symbol, raw_price, usdt_try):
    try:
        # Haftalik analiz artik 120 adet GERCEK 1w mumu kullanir.
        daily = get_klines(symbol, "1d", 120)
        weekly = get_klines(symbol, "1w", 120)
        day = analyze(daily)
        week = analyze(weekly)
        if day is None or week is None:
            return None

        conversion = usdt_try if symbol.endswith("USDT") else 1.0
        price_try = raw_price * conversion
        atr_try = day["atr"] * conversion
        buy_signal = (
            day["buy_score"] >= BUY_SCORE_MIN
            and week["buy_score"] >= BUY_SCORE_MIN
            and MIN_RSI <= day["rsi"] <= MAX_RSI
            and day["volume_ratio"] >= MIN_VOLUME_RATIO
            and atr_try > 0
            and price_try > 0
        )
        sell_signal = (
            day["sell_score"] >= SELL_SCORE_MIN
            or week["sell_score"] >= SELL_SCORE_MIN
        )
        return {
            "price": price_try,
            "atr": atr_try,
            "daily": day,
            "weekly": week,
            "buy_signal": buy_signal,
            "sell_signal": sell_signal
        }
    except Exception as exc:
        print(f"{symbol} analiz hatasi: {exc}")
        return None


def new_state():
    start = now()
    return {
        "version": 5,
        "status": "running",
        "started": start.isoformat(),
        "deadline": (start + timedelta(hours=TEST_HOURS)).isoformat(),
        "cash": STARTING_BALANCE,
        "positions": {},
        "trades": [],
        "cooldowns": {},
        "daily_date": start.date().isoformat(),
        "daily_start_equity": STARTING_BALANCE,
        "last_report": start.isoformat(),
        "scan_count": 0
    }


def save_state(state):
    temp = STATE_FILE + ".tmp"
    with open(temp, "w", encoding="utf-8") as file:
        json.dump(state, file, ensure_ascii=False, indent=2)
    os.replace(temp, STATE_FILE)


def load_state():
    if not os.path.exists(STATE_FILE):
        state = new_state()
        save_state(state)
        return state
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as file:
            state = json.load(file)
        required = {
            "status", "started", "deadline", "cash", "positions",
            "trades", "cooldowns", "daily_date", "daily_start_equity",
            "last_report", "scan_count"
        }
        if not required.issubset(state):
            raise ValueError("Durum dosyasinda gerekli alanlar eksik")
        if state["status"] == "completed":
            # Tamamlanan testi kendiliginden sifirlamaz.
            return state
        return state
    except Exception as exc:
        telegram(f"UYARI: Portfoy dosyasi okunamadi: {exc}. Yeni sanal test baslatiliyor.")
        state = new_state()
        save_state(state)
        return state


def equity(state, market):
    total = float(state["cash"])
    for symbol, pos in state["positions"].items():
        price = market.get(symbol, {}).get("price", pos["last_price"])
        total += pos["quantity"] * price * (1 - FEE_RATE)
    return total


def daily_loss_limit_reached(state, market):
    today = now().date().isoformat()
    if state["daily_date"] != today:
        state["daily_date"] = today
        state["daily_start_equity"] = equity(state, market)
    start = float(state["daily_start_equity"])
    if start <= 0:
        return True
    return (start - equity(state, market)) / start >= MAX_DAILY_LOSS


def close_position(state, symbol, price, reason):
    pos = state["positions"].get(symbol)
    if not pos:
        return None
    proceeds = pos["quantity"] * price * (1 - FEE_RATE)
    pnl = proceeds - pos["cost"]
    state["cash"] += proceeds
    del state["positions"][symbol]
    state["cooldowns"][symbol] = (
        now() + timedelta(hours=REENTRY_WAIT_HOURS)
    ).isoformat()
    trade = {
        "symbol": symbol,
        "entry": pos["entry"],
        "exit": price,
        "quantity": pos["quantity"],
        "cost": pos["cost"],
        "pnl": pnl,
        "pnl_percent": 100 * pnl / pos["cost"] if pos["cost"] else 0,
        "reason": reason,
        "closed": iso_now()
    }
    state["trades"].append(trade)
    return trade


def manage_positions(state, market):
    messages = []
    for symbol in list(state["positions"]):
        item = market.get(symbol)
        if not item:
            continue
        pos = state["positions"][symbol]
        price = item["price"]
        pos["last_price"] = price
        pos["peak_price"] = max(pos["peak_price"], price)
        gain = price / pos["entry"] - 1

        if gain >= BREAK_EVEN_ACTIVATION:
            fee_entry = pos["entry"] * (1 + 2 * FEE_RATE)
            pos["stop"] = max(pos["stop"], fee_entry)
        if gain >= TRAILING_ACTIVATION:
            trail = pos["peak_price"] * (1 - TRAILING_DISTANCE)
            pos["stop"] = max(pos["stop"], trail)

        reason = None
        if price <= pos["stop"]:
            reason = "IZ SUREN STOP" if gain >= TRAILING_ACTIVATION else "STOP"
        elif price >= pos["target"]:
            reason = "HEDEF"
        elif item["sell_signal"]:
            reason = "SAT SINYALI"

        if reason:
            trade = close_position(state, symbol, price, reason)
            if trade:
                messages.append(
                    f"SANAL SATIS: {symbol}\n"
                    f"Cikis: {price:.8f} TL\n"
                    f"Net K/Z: {trade['pnl']:+.2f} TL "
                    f"(%{trade['pnl_percent']:+.2f})\nNeden: {reason}"
                )
    return messages


def open_positions(state, market):
    messages = []
    if daily_loss_limit_reached(state, market):
        return messages

    candidates = sorted(
        market.items(),
        key=lambda x: x[1]["daily"]["buy_score"] + x[1]["weekly"]["buy_score"],
        reverse=True
    )
    for symbol, item in candidates:
        if len(state["positions"]) >= MAX_POSITIONS:
            break
        if symbol in state["positions"] or not item["buy_signal"]:
            continue

        cooldown = state["cooldowns"].get(symbol)
        if cooldown:
            try:
                if now() < datetime.fromisoformat(cooldown):
                    continue
            except (TypeError, ValueError):
                state["cooldowns"].pop(symbol, None)

        price, atr_value = item["price"], item["atr"]
        stop_distance = ATR_STOP_MULTIPLIER * atr_value
        stop = price - stop_distance
        target = price + ATR_TARGET_MULTIPLIER * atr_value
        if price <= 0 or stop <= 0 or stop_distance <= 0:
            continue

        current_equity = equity(state, market)
        risk_budget = current_equity * RISK_PER_TRADE
        # Pozisyon tutari, stop seviyesine kadar yaklasik %1 portfoy riski hedefler.
        risk_value = risk_budget * price / stop_distance
        allocation = min(
            MAX_TRADE_VALUE,
            risk_value,
            state["cash"] / (1 + FEE_RATE)
        )
        if allocation < 1.0:
            continue

        quantity = allocation / (price * (1 + FEE_RATE))
        cost = quantity * price * (1 + FEE_RATE)
        state["cash"] -= cost
        state["positions"][symbol] = {
            "entry": price,
            "quantity": quantity,
            "cost": cost,
            "stop": stop,
            "target": target,
            "peak_price": price,
            "last_price": price,
            "opened": iso_now()
        }
        messages.append(
            f"SANAL ALIM: {symbol}\n"
            f"Alis: {price:.8f} TL | Tutar: {cost:.2f} TL\n"
            f"Gunluk/haftalik puan: "
            f"{item['daily']['buy_score']}/{item['weekly']['buy_score']}\n"
            f"RSI: {item['daily']['rsi']:.1f} | "
            f"Hacim: {item['daily']['volume_ratio']:.2f}x\n"
            f"Stop: {stop:.8f} TL | Hedef: {target:.8f} TL"
        )
    return messages


def scan():
    symbols, prices = get_symbols()
    usdt_try = get_usdt_try_rate()
    market = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        futures = {
            pool.submit(evaluate_symbol, s, prices[s], usdt_try): s
            for s in symbols if s in prices
        }
        for future in concurrent.futures.as_completed(futures):
            symbol = futures[future]
            try:
                result = future.result()
                if result:
                    market[symbol] = result
            except Exception as exc:
                print(f"{symbol} gorev hatasi: {exc}")
    return symbols, market


def report(state, symbols, market):
    value = equity(state, market)
    trades = state["trades"]
    wins = sum(t["pnl"] > 0 for t in trades)
    losses = sum(t["pnl"] < 0 for t in trades)
    rate = 100 * wins / len(trades) if trades else 0
    daily = sum(x["daily"]["buy_score"] >= BUY_SCORE_MIN for x in market.values())
    weekly = sum(x["weekly"]["buy_score"] >= BUY_SCORE_MIN for x in market.values())
    combined = sum(x["buy_signal"] for x in market.values())
    return (
        "MASA TARAYICI 5.0 RAPORU\n"
        f"Saat: {now().strftime('%Y-%m-%d %H:%M UTC')}\n"
        f"Taranan: {len(symbols)} | Analiz edilen: {len(market)}\n"
        f"Gunluk AL adayi: {daily} | Haftalik AL adayi: {weekly}\n"
        f"Birlesik AL sinyali: {combined}\n"
        f"Sanal nakit: {state['cash']:.2f} TL\n"
        f"Portfoy: {value:.2f} TL | Toplam K/Z: {value-STARTING_BALANCE:+.2f} TL\n"
        f"Kapanan islem: {len(trades)} | Kazanan: {wins} | Kaybeden: {losses}\n"
        f"Kazanma orani: %{rate:.1f} | Acik pozisyon: {len(state['positions'])}\n"
        "GERCEK EMIR: KAPALI"
    )


def finish_test(state, market):
    messages = []
    for symbol in list(state["positions"]):
        price = market.get(symbol, {}).get(
            "price", state["positions"][symbol]["last_price"]
        )
        trade = close_position(state, symbol, price, "24 SAATLIK TEST SONU")
        if trade:
            messages.append(
                f"TEST SONU SANAL SATIS: {symbol}\n"
                f"Net K/Z: {trade['pnl']:+.2f} TL"
            )
    state["status"] = "completed"
    final_value = state["cash"]
    trades = state["trades"]
    wins = sum(t["pnl"] > 0 for t in trades)
    messages.append(
        "24 SAATLIK SANAL TEST TAMAMLANDI\n"
        f"Baslangic: {STARTING_BALANCE:.2f} TL\n"
        f"Son portfoy: {final_value:.2f} TL\n"
        f"Toplam K/Z: {final_value-STARTING_BALANCE:+.2f} TL\n"
        f"Islem: {len(trades)} | Kazanan: {wins} | "
        f"Kaybeden: {len(trades)-wins}\n"
        "Tum pozisyonlar sanal kapatildi. Gercek emir yok."
    )
    return messages


def main():
    state = load_state()
    if state.get("status") == "completed":
        print("24 saatlik test zaten tamamlanmis. Yeni test icin paper_state.json dosyasini sil.")
        return

    try:
        symbols, market = scan()
        state["scan_count"] = int(state.get("scan_count", 0)) + 1

        # Test suresi dolduysa yeni pozisyon acmadan once mevcutlari kapat.
        if now() >= datetime.fromisoformat(state["deadline"]):
            for message in finish_test(state, market):
                telegram(message)
            save_state(state)
            return

        for message in manage_positions(state, market):
            telegram(message)
        for message in open_positions(state, market):
            telegram(message)

        text_report = report(state, symbols, market)
        last_report = datetime.fromisoformat(state["last_report"])
        if now() - last_report >= timedelta(seconds=REPORT_INTERVAL_SECONDS):
            telegram(text_report)
            state["last_report"] = iso_now()
        print(text_report)
        save_state(state)

    except Exception as exc:
        # Mevcut durum korunur; hata loga ve Telegram'a yazilir.
        message = f"MASA TARAYICI HATASI\n{type(exc).__name__}: {exc}"
        print(message)
        telegram(message)
        raise


if __name__ == "__main__":
    main()
