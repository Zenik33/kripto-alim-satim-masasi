
import concurrent.futures
import json
import os
import time
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

# =========================================================
# MASA TARAYICI 5.1
# SANAL ISLEM SISTEMI - GERCEK EMIR GONDERMEZ
# =========================================================

BASE_URL = "https://data-api.binance.vision"
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
    "USDC", "USDT", "BUSD", "TUSD", "FDUSD",
    "USDP", "DAI", "USDE", "USDS", "PYUSD",
    "EURC", "USTC"
}


# =========================================================
# ZAMAN
# =========================================================

def now():
    return datetime.now(timezone.utc)


def iso_now():
    return now().isoformat()


# =========================================================
# TELEGRAM
# =========================================================

def telegram(message):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        print(message)
        return False

    url = (
        f"https://api.telegram.org/bot"
        f"{TELEGRAM_TOKEN}/sendMessage"
    )

    payload = urllib.parse.urlencode({
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message[:4000]
    }).encode()

    request = urllib.request.Request(
        url,
        data=payload,
        method="POST"
    )

    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            result = json.loads(response.read().decode())
            return bool(result.get("ok"))

    except Exception as exc:
        print("Telegram gonderim hatasi:", exc)
        return False


# =========================================================
# BINANCE API
# =========================================================

def http_get(path, params=None):
    url = BASE_URL + path

    if params:
        url += "?" + urllib.parse.urlencode(params)

    error = None

    for attempt in range(3):
        try:
            request = urllib.request.Request(
                url,
                headers={"User-Agent": "MasaTarayici/5.1"}
            )

            with urllib.request.urlopen(
                request,
                timeout=25
            ) as response:
                return json.loads(response.read().decode())

        except Exception as exc:
            error = exc
            time.sleep(1.5 * (attempt + 1))

    raise RuntimeError(f"Binance API hatasi: {error}")


def get_usdt_try_rate():
    data = http_get(
        "/api/v3/ticker/price",
        {"symbol": "USDTTRY"}
    )

    return float(data["price"])


# =========================================================
# PARITE LISTESI VE FIYATLAR
# =========================================================

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

    usdt_pairs = []
    try_pairs = []
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

        # Tum uygun paritelerin fiyatlari saklanir.
        # Sadece secilen pariteler analiz edilir.
        prices[symbol] = price

        row = (symbol, volume)

        if allowed[symbol]["quoteAsset"] == "USDT":
            usdt_pairs.append(row)
        else:
            try_pairs.append(row)

    usdt_pairs.sort(
        key=lambda x: x[1],
        reverse=True
    )

    try_pairs.sort(
        key=lambda x: x[1],
        reverse=True
    )

    selected = (
        [s for s, _ in usdt_pairs[:USDT_PAIR_COUNT]]
        +
        [s for s, _ in try_pairs[:TRY_PAIR_COUNT]]
    )

    return selected, prices


def convert_prices_to_try(prices, usdt_try):
    converted = {}

    for symbol, price in prices.items():
        if symbol.endswith("USDT"):
            converted[symbol] = price * usdt_try
        else:
            converted[symbol] = price

    return converted


# =========================================================
# MUM VERILERI
# =========================================================

def get_klines(symbol, interval, limit=120):
    rows = http_get(
        "/api/v3/klines",
        {
            "symbol": symbol,
            "interval": interval,
            "limit": limit
        }
    )

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

        # Yalnizca kapanmis mumlar kullanilir.
        if candle["close_time"] < current_ms:
            result.append(candle)

    return result


# =========================================================
# TEKNIK INDIKATORLER
# =========================================================

def ema(values, period):
    if len(values) < period:
        return []

    alpha = 2.0 / (period + 1)
    result = [float(values[0])]

    for value in values[1:]:
        result.append(
            alpha * float(value)
            + (1 - alpha) * result[-1]
        )

    return result


def rsi(values, period=14):
    if len(values) <= period:
        return 50.0

    changes = [
        values[i] - values[i - 1]
        for i in range(1, len(values))
    ]

    gains = [max(x, 0.0) for x in changes]
    losses = [max(-x, 0.0) for x in changes]

    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period

    for i in range(period, len(gains)):
        avg_gain = (
            (period - 1) * avg_gain + gains[i]
        ) / period

        avg_loss = (
            (period - 1) * avg_loss + losses[i]
        ) / period

    if avg_loss == 0:
        return 100.0 if avg_gain > 0 else 50.0

    rs = avg_gain / avg_loss

    return 100.0 - 100.0 / (1.0 + rs)


def atr(candles, period=14):
    if len(candles) <= period:
        return 0.0

    ranges = []

    for i in range(1, len(candles)):
        current = candles[i]
        previous_close = candles[i - 1]["close"]

        ranges.append(max(
            current["high"] - current["low"],
            abs(current["high"] - previous_close),
            abs(current["low"] - previous_close)
        ))

    return sum(ranges[-period:]) / period


# =========================================================
# PUANLAMA
# =========================================================

def analyze(candles):
    if len(candles) < 60:
        return None

    closes = [c["close"] for c in candles]
    volumes = [c["volume"] for c in candles]

    e20 = ema(closes, 20)
    e50 = ema(closes, 50)
    e12 = ema(closes, 12)
    e26 = ema(closes, 26)

    if not all((e20, e50, e12, e26)):
        return None

    macd = [
        a - b
        for a, b in zip(e12, e26)
    ]

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

    vol_ratio = (
        recent_vol / base_vol
        if base_vol > 0
        else 0
    )

    if (
        vol_ratio >= MIN_VOLUME_RATIO
        and closes[-1] > closes[-2]
    ):
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


# =========================================================
# PARITE ANALIZI
# =========================================================

def evaluate_symbol(symbol, raw_price, usdt_try):
    try:
        daily = get_klines(symbol, "1d", 120)
        weekly = get_klines(symbol, "1w", 120)

        day = analyze(daily)
        week = analyze(weekly)

        if day is None or week is None:
            return None

        conversion = (
            usdt_try
            if symbol.endswith("USDT")
            else 1.0
        )

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


# =========================================================
# SANAL PORTFOY
# =========================================================

def new_state():
    start = now()

    return {
        "version": 5.1,
        "status": "running",
        "started": start.isoformat(),
        "deadline": (
            start + timedelta(hours=TEST_HOURS)
        ).isoformat(),
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
        json.dump(
            state,
            file,
            ensure_ascii=False,
            indent=2
        )

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
            "status",
            "started",
            "deadline",
            "cash",
            "positions",
            "trades",
            "cooldowns",
            "daily_date",
            "daily_start_equity",
            "last_report",
            "scan_count"
        }

        if not required.issubset(state):
            raise ValueError(
                "Durum dosyasinda gerekli alanlar eksik"
            )

        # Eski surumdeki pozisyonlar korunur.
        state["version"] = 5.1

        return state

    except Exception as exc:
        telegram(
            "UYARI: Portfoy dosyasi okunamadi: "
            f"{exc}. Yeni sanal test baslatiliyor."
        )

        state = new_state()
        save_state(state)

        return state


# =========================================================
# PORTFOY DEGERI
# =========================================================

def equity(state, current_prices):
    total = float(state["cash"])

    for symbol, pos in state["positions"].items():
        price = current_prices.get(
            symbol,
            pos.get("last_price", pos["entry"])
        )

        total += (
            pos["quantity"]
            * price
            * (1 - FEE_RATE)
        )

    return total


def daily_loss_limit_reached(state, current_prices):
    today = now().date().isoformat()

    if state["daily_date"] != today:
        state["daily_date"] = today
        state["daily_start_equity"] = equity(
            state,
            current_prices
        )

    start = float(state["daily_start_equity"])

    if start <= 0:
        return True

    current = equity(state, current_prices)

    return (
        (start - current) / start
        >= MAX_DAILY_LOSS
    )


# =========================================================
# POZISYON KAPATMA
# =========================================================

def close_position(state, symbol, price, reason):
    pos = state["positions"].get(symbol)

    if not pos:
        return None

    proceeds = (
        pos["quantity"]
        * price
        * (1 - FEE_RATE)
    )

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
        "pnl_percent": (
            100 * pnl / pos["cost"]
            if pos["cost"]
            else 0
        ),
        "reason": reason,
        "closed": iso_now()
    }

    state["trades"].append(trade)

    return trade


# =========================================================
# ACIK POZISYONLARI YONET
# =========================================================

def manage_positions(state, current_prices, market):
    messages = []

    for symbol in list(state["positions"]):
        pos = state["positions"][symbol]

        # 5.1 DUZELTMESI:
        # Pozisyon secilen analiz listesinden ciksa bile
        # tum uygun Binance fiyatlari icinden takip edilir.
        price = current_prices.get(symbol)

        if price is None or price <= 0:
            print(
                f"{symbol}: Guncel fiyat bulunamadi. "
                "Pozisyon korunuyor."
            )
            continue

        pos["last_price"] = price
        pos["peak_price"] = max(
            pos.get("peak_price", pos["entry"]),
            price
        )

        gain = price / pos["entry"] - 1

        # Kar %3'e ulasinca maliyet civari stop.
        if gain >= BREAK_EVEN_ACTIVATION:
            fee_entry = (
                pos["entry"] * (1 + 2 * FEE_RATE)
            )

            pos["stop"] = max(
                pos["stop"],
                fee_entry
            )

        # Kar %5'e ulasinca iz suren stop.
        if gain >= TRAILING_ACTIVATION:
            trail = (
                pos["peak_price"]
                * (1 - TRAILING_DISTANCE)
            )

            pos["stop"] = max(
                pos["stop"],
                trail
            )

        # Analiz sonucu yoksa SAT sinyali varsayilmaz.
        item = market.get(symbol, {})
        sell_signal = item.get("sell_signal", False)

        reason = None

        if price <= pos["stop"]:
            reason = (
                "IZ SUREN STOP"
                if gain >= TRAILING_ACTIVATION
                else "STOP"
            )

        elif price >= pos["target"]:
            reason = "HEDEF"

        elif sell_signal:
            reason = "SAT SINYALI"

        if reason:
            trade = close_position(
                state,
                symbol,
                price,
                reason
            )

            if trade:
                messages.append(
                    f"SANAL SATIS: {symbol}\n"
                    f"Cikis: {price:.8f} TL\n"
                    f"Net K/Z: {trade['pnl']:+.2f} TL "
                    f"(%{trade['pnl_percent']:+.2f})\n"
                    f"Neden: {reason}"
                )

    return messages


# =========================================================
# YENI POZISYON AC
# =========================================================

def open_positions(state, market, current_prices):
    messages = []

    if daily_loss_limit_reached(
        state,
        current_prices
    ):
        return messages

    candidates = sorted(
        market.items(),
        key=lambda x: (
            x[1]["daily"]["buy_score"]
            + x[1]["weekly"]["buy_score"]
        ),
        reverse=True
    )

    for symbol, item in candidates:
        if len(state["positions"]) >= MAX_POSITIONS:
            break

        if symbol in state["positions"]:
            continue

        if not item["buy_signal"]:
            continue

        cooldown = state["cooldowns"].get(symbol)

        if cooldown:
            try:
                if now() < datetime.fromisoformat(cooldown):
                    continue

            except (TypeError, ValueError):
                state["cooldowns"].pop(symbol, None)

        price = item["price"]
        atr_value = item["atr"]

        stop_distance = (
            ATR_STOP_MULTIPLIER * atr_value
        )

        stop = price - stop_distance
        target = (
            price
            + ATR_TARGET_MULTIPLIER * atr_value
        )

        if (
            price <= 0
            or stop <= 0
            or stop_distance <= 0
        ):
            continue

        current_equity = equity(
            state,
            current_prices
        )

        risk_budget = (
            current_equity * RISK_PER_TRADE
        )

        risk_value = (
            risk_budget * price / stop_distance
        )

        allocation = min(
            MAX_TRADE_VALUE,
            risk_value,
            state["cash"] / (1 + FEE_RATE)
        )

        if allocation < 1.0:
            continue

        quantity = (
            allocation
            / (price * (1 + FEE_RATE))
        )

        cost = (
            quantity
            * price
            * (1 + FEE_RATE)
        )

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
            f"Alis: {price:.8f} TL\n"
            f"Tutar: {cost:.2f} TL\n"
            f"Gunluk/haftalik puan: "
            f"{item['daily']['buy_score']}/"
            f"{item['weekly']['buy_score']}\n"
            f"RSI: {item['daily']['rsi']:.1f}\n"
            f"Hacim: "
            f"{item['daily']['volume_ratio']:.2f}x\n"
            f"Stop: {stop:.8f} TL\n"
            f"Hedef: {target:.8f} TL"
        )

    return messages


# =========================================================
# TARAMA
# =========================================================

def scan():
    symbols, raw_prices = get_symbols()

    usdt_try = get_usdt_try_rate()

    # Tum uygun paritelerin TL fiyatlari.
    current_prices = convert_prices_to_try(
        raw_prices,
        usdt_try
    )

    market = {}

    with concurrent.futures.ThreadPoolExecutor(
        max_workers=MAX_WORKERS
    ) as pool:

        futures = {
            pool.submit(
                evaluate_symbol,
                symbol,
                raw_prices[symbol],
                usdt_try
            ): symbol

            for symbol in symbols
            if symbol in raw_prices
        }

        for future in concurrent.futures.as_completed(
            futures
        ):
            symbol = futures[future]

            try:
                result = future.result()

                if result:
                    market[symbol] = result

            except Exception as exc:
                print(
                    f"{symbol} gorev hatasi: {exc}"
                )

    return symbols, market, current_prices


# =========================================================
# RAPOR
# =========================================================

def report(state, symbols, market, current_prices):
    value = equity(state, current_prices)

    trades = state["trades"]

    wins = sum(
        trade["pnl"] > 0
        for trade in trades
    )

    losses = sum(
        trade["pnl"] < 0
        for trade in trades
    )

    rate = (
        100 * wins / len(trades)
        if trades
        else 0
    )

    daily = sum(
        item["daily"]["buy_score"] >= BUY_SCORE_MIN
        for item in market.values()
    )

    weekly = sum(
        item["weekly"]["buy_score"] >= BUY_SCORE_MIN
        for item in market.values()
    )

    combined = sum(
        item["buy_signal"]
        for item in market.values()
    )

    unrealized = 0.0
    position_lines = []

    for symbol, pos in state["positions"].items():
        price = current_prices.get(
            symbol,
            pos.get("last_price", pos["entry"])
        )

        current_value = (
            pos["quantity"]
            * price
            * (1 - FEE_RATE)
        )

        pnl = current_value - pos["cost"]

        unrealized += pnl

        percent = (
            100 * pnl / pos["cost"]
            if pos["cost"]
            else 0
        )

        position_lines.append(
            f"{symbol}: {pnl:+.2f} TL "
            f"(%{percent:+.2f}) | "
            f"Fiyat: {price:.8f} TL"
        )

    if position_lines:
        positions_text = "\n".join(position_lines)
    else:
        positions_text = "Acik pozisyon yok."

    return (
        "MASA TARAYICI 5.1 RAPORU\n"
        f"Saat: {now().strftime('%Y-%m-%d %H:%M UTC')}\n"
        f"Taranan: {len(symbols)} | "
        f"Analiz edilen: {len(market)}\n"
        f"Gunluk AL adayi: {daily} | "
        f"Haftalik AL adayi: {weekly}\n"
        f"Birlesik AL sinyali: {combined}\n"
        "------------------------\n"
        f"Sanal nakit: {state['cash']:.2f} TL\n"
        f"Portfoy: {value:.2f} TL\n"
        f"Toplam K/Z: "
        f"{value - STARTING_BALANCE:+.2f} TL\n"
        f"Acik pozisyon K/Z: {unrealized:+.2f} TL\n"
        "------------------------\n"
        f"Kapanan islem: {len(trades)}\n"
        f"Kazanan: {wins} | Kaybeden: {losses}\n"
        f"Kazanma orani: %{rate:.1f}\n"
        f"Acik pozisyon: {len(state['positions'])}\n"
        "------------------------\n"
        "ACIK POZISYONLAR:\n"
        f"{positions_text}\n"
        "------------------------\n"
        f"Tarama sayisi: {state['scan_count']}\n"
        "GERCEK EMIR: KAPALI"
    )


# =========================================================
# TESTI BITIR
# =========================================================

def finish_test(state, current_prices):
    messages = []

    for symbol in list(state["positions"]):
        pos = state["positions"][symbol]

        price = current_prices.get(
            symbol,
            pos.get("last_price", pos["entry"])
        )

        trade = close_position(
            state,
            symbol,
            price,
            "24 SAATLIK TEST SONU"
        )

        if trade:
            messages.append(
                f"TEST SONU SANAL SATIS: {symbol}\n"
                f"Net K/Z: {trade['pnl']:+.2f} TL"
            )

    state["status"] = "completed"

    final_value = state["cash"]
    trades = state["trades"]

    wins = sum(
        trade["pnl"] > 0
        for trade in trades
    )

    messages.append(
        "24 SAATLIK SANAL TEST TAMAMLANDI\n"
        f"Baslangic: {STARTING_BALANCE:.2f} TL\n"
        f"Son portfoy: {final_value:.2f} TL\n"
        f"Toplam K/Z: "
        f"{final_value - STARTING_BALANCE:+.2f} TL\n"
        f"Islem: {len(trades)}\n"
        f"Kazanan: {wins}\n"
        f"Kaybeden: {len(trades) - wins}\n"
        "Tum pozisyonlar sanal kapatildi.\n"
        "Gercek emir yok."
    )

    return messages


# =========================================================
# ANA PROGRAM
# =========================================================

def main():
    state = load_state()

    if state.get("status") == "completed":
        print(
            "24 saatlik test zaten tamamlanmis. "
            "Yeni test icin paper_state.json dosyasini "
            "bilerek sifirlamalisin."
        )
        return

    try:
        symbols, market, current_prices = scan()

        state["scan_count"] = (
            int(state.get("scan_count", 0)) + 1
        )

        # Test suresi dolduysa yeni alim yapmadan
        # once mevcut pozisyonlar kapatilir.
        deadline = datetime.fromisoformat(
            state["deadline"]
        )

        if now() >= deadline:
            for message in finish_test(
                state,
                current_prices
            ):
                telegram(message)

            save_state(state)
            return

        # Once acik pozisyonlar yonetilir.
        for message in manage_positions(
            state,
            current_prices,
            market
        ):
            telegram(message)

        # Sonra yeni alim firsatlari kontrol edilir.
        for message in open_positions(
            state,
            market,
            current_prices
        ):
            telegram(message)

        text_report = report(
            state,
            symbols,
            market,
            current_prices
        )

        last_report = datetime.fromisoformat(
            state["last_report"]
        )

        if (
            now() - last_report
            >= timedelta(
                seconds=REPORT_INTERVAL_SECONDS
            )
        ):
            telegram(text_report)
            state["last_report"] = iso_now()

        print(text_report)

        save_state(state)

    except Exception as exc:
        message = (
            "MASA TARAYICI HATASI\n"
            f"{type(exc).__name__}: {exc}"
        )

        print(message)
        telegram(message)

        # Hata durumunda mevcut sanal durum korunur.
        save_state(state)

        raise


if __name__ == "__main__":
    main()
