
import os
import json
import time
import urllib.request
import urllib.parse
from datetime import datetime, timedelta, timezone
from concurrent.futures import ThreadPoolExecutor, as_completed

TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
BINANCE = "https://data-api.binance.vision"

STARTING_BALANCE = 100.0
TEST_HOURS = 24

BUY_LIMIT = 80
SELL_LIMIT = 80

MAX_POSITIONS = 5
TRADE_ALLOCATION = 0.20
FEE = 0.001

STOP_ATR = 1.5
TARGET_ATR = 2.5

MAX_USDT_PAIRS = 40
MAX_TRY_PAIRS = 20
MAX_WORKERS = 8

STATE_FILE = "paper_state.json"

STABLECOINS = {
    "USDC", "USDT", "BUSD", "TUSD", "FDUSD", "USDP",
    "DAI", "USDE", "USDS", "RLUSD", "PYUSD", "EURC",
    "USDD", "FRAX", "USTC", "UST", "EURS", "AEUR",
    "USD1", "USDTB", "USDF"
}


def now():
    return datetime.now(timezone.utc)


def stamp(value=None):
    return (value or now()).isoformat()


def money(value):
    return f"{value:.2f} TL"


def price_money(value):
    value = float(value)
    absolute = abs(value)

    if absolute == 0:
        return "0 TL"

    decimals = (
        2 if absolute >= 100
        else 4 if absolute >= 1
        else 7 if absolute >= 0.01
        else 10
    )

    return f"{value:.{decimals}f} TL"


def get_json(url, timeout=15):
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "Mozilla/5.0"}
    )

    for attempt in range(2):
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return json.loads(response.read().decode("utf-8"))

        except Exception as error:
            print("Veri hatasi:", error)

            if attempt == 0:
                time.sleep(1)

    return None


def telegram(message):
    if not TOKEN or not CHAT_ID:
        print("Telegram bilgileri eksik.")
        return False

    url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"

    data = urllib.parse.urlencode({
        "chat_id": CHAT_ID,
        "text": message,
        "disable_web_page_preview": "true"
    }).encode("utf-8")

    try:
        request = urllib.request.Request(url, data=data)

        with urllib.request.urlopen(request, timeout=15) as response:
            result = json.loads(response.read().decode("utf-8"))

            if result.get("ok") is True:
                print("Telegram mesaji gonderildi.")
                return True

            print("Telegram hatasi:", result.get("description"))

    except Exception as error:
        print("Telegram gonderim hatasi:", error)

    return False


def base_asset_from_symbol(symbol):
    for quote in ("USDT", "TRY"):
        if symbol.endswith(quote):
            return symbol[:-len(quote)]

    return symbol


def new_test_state(history=None):
    started = now()

    return {
        "status": "running",
        "started": stamp(started),
        "deadline": stamp(started + timedelta(hours=TEST_HOURS)),
        "cash": STARTING_BALANCE,
        "positions": {},
        "trades": [],
        "last_alerts": {},
        "started_message": False,
        "history": history or []
    }


def load_state():
    if not os.path.exists(STATE_FILE):
        return new_test_state()

    try:
        with open(STATE_FILE, "r", encoding="utf-8") as file:
            state = json.load(file)

    except Exception as error:
        raise RuntimeError(
            "paper_state.json okunamadi. Kayitlar korunmasi icin islem durduruldu."
        ) from error

    state.setdefault("positions", {})
    state.setdefault("trades", [])
    state.setdefault("last_alerts", {})
    state.setdefault("cash", STARTING_BALANCE)
    state.setdefault("history", [])

    if state.get("status") != "completed":
        return state

    if os.getenv("GITHUB_EVENT_NAME") != "workflow_dispatch":
        return state

    old_summary = {
        "started": state.get("started"),
        "completed": state.get("completed"),
        "cash": state.get("cash"),
        "trades": state.get("trades", [])
    }

    history = list(state.get("history", []))
    history.append(old_summary)

    print("Onceki test arsivlendi. Yeni test baslatiliyor.")

    return new_test_state(history)


def save_state(state):
    temporary = STATE_FILE + ".tmp"

    with open(temporary, "w", encoding="utf-8") as file:
        json.dump(state, file, ensure_ascii=False, indent=2)

    os.replace(temporary, STATE_FILE)


def ema(values, period):
    if not values:
        return []

    multiplier = 2 / (period + 1)
    current = values[0]
    result = []

    for value in values:
        current = value * multiplier + current * (1 - multiplier)
        result.append(current)

    return result


def rsi(values, period=14):
    if len(values) <= period:
        return 50.0

    changes = [
        values[i] - values[i - 1]
        for i in range(1, len(values))
    ]

    gains = [max(change, 0) for change in changes]
    losses = [max(-change, 0) for change in changes]

    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period

    for i in range(period, len(gains)):
        avg_gain = (
            avg_gain * (period - 1) + gains[i]
        ) / period

        avg_loss = (
            avg_loss * (period - 1) + losses[i]
        ) / period

    if avg_loss == 0:
        return 100.0

    rs = avg_gain / avg_loss

    return 100 - (100 / (1 + rs))


def analyze(candles):
    if len(candles) < 50:
        return None

    closes = [x["close"] for x in candles]
    highs = [x["high"] for x in candles]
    lows = [x["low"] for x in candles]
    volumes = [x["volume"] for x in candles]

    e20 = ema(closes, 20)
    e50 = ema(closes, 50)

    fast = ema(closes, 12)
    slow = ema(closes, 26)

    macd = [a - b for a, b in zip(fast, slow)]
    signal = ema(macd, 9)

    current_rsi = rsi(closes)

    buy = 0
    sell = 0

    if closes[-1] > e20[-1]:
        buy += 20
    elif closes[-1] < e20[-1]:
        sell += 20

    if e20[-1] > e50[-1]:
        buy += 20
    elif e20[-1] < e50[-1]:
        sell += 20

    if 50 <= current_rsi <= 68:
        buy += 15
    elif current_rsi < 38:
        buy += 10

    if current_rsi < 45:
        sell += 15
    elif current_rsi > 72:
        sell += 10

    if macd[-1] > signal[-1]:
        buy += 15
    else:
        sell += 15

    if closes[-1] > closes[-6]:
        buy += 15
    elif closes[-1] < closes[-6]:
        sell += 15

    recent_volume = sum(volumes[-5:]) / 5
    old_volume = sum(volumes[-20:-5]) / 15

    if old_volume > 0:
        ratio = recent_volume / old_volume

        if ratio > 1.1 and closes[-1] > closes[-2]:
            buy += 15

        elif ratio > 1.1 and closes[-1] < closes[-2]:
            sell += 15

    ranges = []

    for i in range(max(1, len(candles) - 14), len(candles)):
        ranges.append(max(
            highs[i] - lows[i],
            abs(highs[i] - closes[i - 1]),
            abs(lows[i] - closes[i - 1])
        ))

    atr = sum(ranges) / len(ranges) if ranges else 0

    decision = "BEKLE"

    if buy >= BUY_LIMIT and buy > sell:
        decision = "AL"

    elif sell >= SELL_LIMIT and sell > buy:
        decision = "SAT"

    return {
        "decision": decision,
        "buy": buy,
        "sell": sell,
        "price": closes[-1],
        "atr": atr,
        "rsi": current_rsi
    }


def weekly_candles(daily):
    weeks = {}

    for candle in daily:
        date = datetime.fromtimestamp(
            candle["time"] / 1000,
            tz=timezone.utc
        )

        key = date.strftime("%G-%V")

        if key not in weeks:
            weeks[key] = dict(candle)

        else:
            week = weeks[key]
            week["high"] = max(week["high"], candle["high"])
            week["low"] = min(week["low"], candle["low"])
            week["close"] = candle["close"]
            week["volume"] += candle["volume"]

    return list(weeks.values())[:-1]


def binance_data(held_symbols=None):
    held_symbols = set(held_symbols or [])

    info = get_json(BINANCE + "/api/v3/exchangeInfo")
    tickers = get_json(BINANCE + "/api/v3/ticker/24hr")

    if not info or not tickers:
        raise RuntimeError("Binance verisi alinamadi.")

    symbol_info = {}

    for item in info["symbols"]:
        if (
            item.get("status") == "TRADING"
            and item.get("quoteAsset") in ("USDT", "TRY")
            and item.get("isSpotTradingAllowed", False)
            and (
                item.get("baseAsset") not in STABLECOINS
                or item.get("symbol") == "USDTTRY"
            )
        ):
            symbol_info[item["symbol"]] = item

    prices = {}
    volumes_usdt = []
    volumes_try = []

    for ticker in tickers:
        symbol = ticker.get("symbol")
        details = symbol_info.get(symbol)

        if not details:
            continue

        try:
            prices[symbol] = float(ticker["lastPrice"])

            if symbol == "USDTTRY":
                continue

            quote_volume = float(ticker.get("quoteVolume", 0))
            pair = (symbol, quote_volume)

            if details["quoteAsset"] == "USDT":
                volumes_usdt.append(pair)

            elif details["quoteAsset"] == "TRY":
                volumes_try.append(pair)

        except (TypeError, ValueError, KeyError):
            continue

    usdt_pairs = sorted(
        volumes_usdt,
        key=lambda x: x[1],
        reverse=True
    )[:MAX_USDT_PAIRS]

    try_pairs = sorted(
        volumes_try,
        key=lambda x: x[1],
        reverse=True
    )[:MAX_TRY_PAIRS]

    selected = [x[0] for x in usdt_pairs + try_pairs]

    for symbol in held_symbols:
        if symbol in prices and symbol not in selected:
            selected.append(symbol)

    return list(dict.fromkeys(selected)), prices


def binance_candles(symbol):
    url = (
        BINANCE + "/api/v3/klines"
        + f"?symbol={urllib.parse.quote(symbol)}"
        + "&interval=1d&limit=1000"
    )

    data = get_json(url)

    if not data:
        return []

    candles = []

    for row in data:
        try:
            candles.append({
                "time": int(row[0]),
                "open": float(row[1]),
                "high": float(row[2]),
                "low": float(row[3]),
                "close": float(row[4]),
                "volume": float(row[5])
            })

        except (TypeError, ValueError, IndexError):
            continue

    return candles


def fetch_symbol(symbol):
    return symbol, binance_candles(symbol)


def evaluate(name, candles, current_price, conversion=1.0):
    if len(candles) < 60 or current_price is None:
        return None

    daily = candles[:-1]

    day = analyze(daily)
    week = analyze(weekly_candles(daily))

    if not day or not week:
        return None

    buy_alert = (
        day["decision"] == "AL"
        and day["buy"] >= BUY_LIMIT
        and week["decision"] == "AL"
        and week["buy"] >= BUY_LIMIT
    )

    sell_alert = (
        day["decision"] == "SAT"
        and day["sell"] >= SELL_LIMIT
    ) or (
        week["decision"] == "SAT"
        and week["sell"] >= SELL_LIMIT
    )

    return {
        "name": name,
        "price": current_price * conversion,
        "atr": day["atr"] * conversion,
        "daily": day,
        "weekly": week,
        "buy_alert": buy_alert,
        "sell_alert": sell_alert
    }


def close_position(state, name, price, reason):
    position = state["positions"].pop(name, None)

    if not position or price is None or price <= 0:
        if position:
            state["positions"][name] = position
        return None

    proceeds = position["quantity"] * price * (1 - FEE)
    pnl = proceeds - position["cost"]

    state["cash"] += proceeds

    trade = {
        "name": name,
        "entry": position["entry"],
        "exit": price,
        "cost": position["cost"],
        "proceeds_after_fee": proceeds,
        "pnl": pnl,
        "pnl_percent": pnl / position["cost"] * 100,
        "reason": reason,
        "closed": stamp()
    }

    state["trades"].append(trade)

    return trade


def paper_update(state, market):
    events = []

    for name in list(state["positions"].keys()):
        item = market.get(name)

        if not item:
            print("Pozisyon fiyat verisi yok:", name)
            continue

        position = state["positions"][name]
        price = item["price"]

        position["last_price"] = price
        position["last_price_time"] = stamp()

        reason = None

        if price <= position["stop"]:
            reason = "STOP"

        elif price >= position["target"]:
            reason = "HEDEF"

        elif item["sell_alert"]:
            reason = "SAT SINYALI"

        if reason:
            trade = close_position(state, name, price, reason)

            if trade:
                events.append(
                    f"🔴 SANAL SATIS: {name}\n"
                    f"Net sonuc: {money(trade['pnl'])}\n"
                    f"Getiri: %{trade['pnl_percent']:.2f}\n"
                    f"Neden: {reason}"
                )

    for name, item in market.items():
        if len(state["positions"]) >= MAX_POSITIONS:
            break

        if name in state["positions"]:
            continue

        if base_asset_from_symbol(name) in STABLECOINS:
            continue

        if not item["buy_alert"]:
            continue

        if state["cash"] < 5:
            break

        allocation = min(
            state["cash"],
            state["cash"] * TRADE_ALLOCATION
        )

        price = item["price"]
        atr = item["atr"]

        if price <= 0 or atr <= 0:
            continue

        quantity = allocation / (price * (1 + FEE))
        state["cash"] -= allocation

        state["positions"][name] = {
            "entry": price,
            "last_price": price,
            "last_price_time": stamp(),
            "quantity": quantity,
            "cost": allocation,
            "stop": max(price - atr * STOP_ATR, price * 0.90),
            "target": price + atr * TARGET_ATR,
            "opened": stamp()
        }

        events.append(
            f"🟢 SANAL ALIM: {name}\n"
            f"Alis: {price_money(price)}\n"
            f"Yatirilan: {money(allocation)}\n"
            f"Gunluk AL: %{item['daily']['buy']}\n"
            f"Haftalik AL: %{item['weekly']['buy']}\n"
            f"Stop: {price_money(state['positions'][name]['stop'])}\n"
            f"Hedef: {price_money(state['positions'][name]['target'])}"
        )

    return events


def portfolio_value(state, market):
    total = state["cash"]

    for name, position in state["positions"].items():
        item = market.get(name)

        price = (
            item["price"]
            if item
            else position.get("last_price", position["entry"])
        )

        total += position["quantity"] * price

    return total


def print_signal_diagnostics(market):
    daily_al = sum(
        1 for x in market.values()
        if x["daily"]["decision"] == "AL"
    )

    weekly_al = sum(
        1 for x in market.values()
        if x["weekly"]["decision"] == "AL"
    )

    both_strong = sum(
        1 for x in market.values()
        if x["buy_alert"]
    )

    print("--- AL SINYAL TESHiSI ---")
    print("Gunluk AL:", daily_al)
    print("Haftalik AL:", weekly_al)
    print("Iki zaman diliminde guclu AL:", both_strong)
    print("-------------------------")


def send_alerts(state, market):
    current = now()
    sent_count = 0

    for name, item in market.items():
        if not item["buy_alert"]:
            continue

        alert_key = f"{name}_AL"
        previous = state["last_alerts"].get(alert_key)

        if previous:
            try:
                last_time = datetime.fromisoformat(previous)

                if current - last_time < timedelta(hours=6):
                    print("AL bildirimi beklemede:", name)
                    continue

            except Exception:
                pass

        message = (
            f"🟢 GÜÇLÜ AL: {name}\n"
            f"Günlük AL: %{item['daily']['buy']}\n"
            f"Haftalık AL: %{item['weekly']['buy']}\n"
            f"Güncel fiyat: {price_money(item['price'])}\n"
            "Sinyal puanı kazanç olasılığı değildir."
        )

        if telegram(message):
            state["last_alerts"][alert_key] = stamp()
            sent_count += 1

    return sent_count


def finish_test(state, market):
    # Acik pozisyonlar otomatik satilmaz.
    # Son fiyatla portfoy degeri hesaplanir.

    for name, item in market.items():
        if name in state["positions"]:
            state["positions"][name]["last_price"] = item["price"]
            state["positions"][name]["last_price_time"] = stamp()

    equity = portfolio_value(state, market)

    total_pnl = equity - STARTING_BALANCE

    realized = sum(
        trade["pnl"]
        for trade in state["trades"]
    )

    unrealized = total_pnl - realized

    wins = sum(
        1 for trade in state["trades"]
        if trade["pnl"] > 0
    )

    losses = sum(
        1 for trade in state["trades"]
        if trade["pnl"] <= 0
    )

    closed_count = len(state["trades"])

    win_rate = (
        wins / closed_count * 100
        if closed_count else 0
    )

    open_lines = []

    for name, position in state["positions"].items():
        item = market.get(name)

        price = (
            item["price"]
            if item
            else position.get("last_price", position["entry"])
        )

        value = position["quantity"] * price
        open_pnl = value * (1 - FEE) - position["cost"]

        open_lines.append(
            f"• {name}: tahmini net {money(open_pnl)}"
        )

    state["status"] = "completed"
    state["completed"] = stamp()
    state["ending_equity"] = equity
    state["total_pnl"] = total_pnl

    message = (
        "🏁 24 SAATLİK SANAL TEST TAMAMLANDI\n\n"
        f"Başlangıç: {money(STARTING_BALANCE)}\n"
        f"Son portföy değeri: {money(equity)}\n"
        f"Toplam kâr/zarar: {money(total_pnl)}\n"
        f"Gerçekleşmiş kâr/zarar: {money(realized)}\n"
        f"Açık pozisyon tahmini kâr/zararı: {money(unrealized)}\n"
        f"Kapanan işlem: {closed_count}\n"
        f"Kazanan işlem: {wins}\n"
        f"Kaybeden işlem: {losses}\n"
        f"Kazanma oranı: %{win_rate:.1f}\n"
        f"Açık pozisyon: {len(state['positions'])}\n\n"
    )

    if open_lines:
        message += (
            "Açık pozisyonlar:\n"
            + "\n".join(open_lines)
            + "\n\n"
        )

    message += (
        "Açık pozisyonlar otomatik satılmadı. "
        "Değerler son fiyat üzerinden tahminidir. "
        "Gerçek emir gönderilmedi."
    )

    save_state(state)
    telegram(message)

    print(message)


def main():
    print("Kripto taramasi basladi:", stamp())

    state = load_state()

    if state["status"] == "completed":
        print(
            "Test tamamlanmis. Yeni test icin GitHub Actions "
            "Run workflow kullan."
        )
        return

    if not state.get("started_message"):
        started_text = (
            "🧪 24 SAATLİK SANAL TEST BAŞLADI\n"
            "Başlangıç bakiye: 100 TL\n"
            "Binance Spot kripto pariteleri taranıyor.\n"
            "Sabit coinler yeni alıma kapalı.\n"
            "Gerçek para kullanılmıyor."
        )

        if telegram(started_text):
            state["started_message"] = True
            save_state(state)

    held_symbols = list(state["positions"].keys())

    symbols, prices = binance_data(held_symbols)

    usdt_try = prices.get("USDTTRY")

    print("Taranacak kripto parite:", len(symbols))

    candles_by_symbol = {}

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures = {
            executor.submit(fetch_symbol, symbol): symbol
            for symbol in symbols
        }

        completed = 0

        for future in as_completed(futures):
            symbol = futures[future]

            try:
                returned_symbol, candles = future.result()
                candles_by_symbol[returned_symbol] = candles

            except Exception as error:
                print("Mum verisi hatasi:", symbol, error)

            completed += 1

            if completed % 10 == 0:
                print(
                    "Verisi alinan parite:",
                    completed,
                    "/",
                    len(symbols)
                )

    market = {}

    for symbol in symbols:
        candles = candles_by_symbol.get(symbol, [])
        current = prices.get(symbol)

        if symbol.endswith("USDT"):
            if not usdt_try:
                continue

            conversion = usdt_try

        else:
            conversion = 1.0

        item = evaluate(
            symbol,
            candles,
            current,
            conversion
        )

        if item:
            market[symbol] = item

    print("Analiz edilen kripto:", len(market))

    print_signal_diagnostics(market)

    events = paper_update(state, market)

    for event in events:
        telegram(event)

    sent_count = send_alerts(state, market)

    print("Gonderilen guclu AL bildirimi:", sent_count)

    deadline = datetime.fromisoformat(state["deadline"])

    if now() >= deadline:
        finish_test(state, market)

    else:
        value = portfolio_value(state, market)

        print("Sanal nakit:", money(state["cash"]))
        print("Portfoy degeri:", money(value))
        print("Acik pozisyon:", len(state["positions"]))
        print("Kalan sure:", deadline - now())

        save_state(state)

    print("Kripto taramasi tamamlandi.")


if __name__ == "__main__":
    main()
