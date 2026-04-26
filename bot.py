import urllib.request
import asyncio, logging, time, json, hmac, hashlib, urllib.parse
import aiohttp

BINANCE_API_KEY = "qZ3dMupVbwtrX40OrLJgWpTMpTQGFq1XIpKAs5iMdZ7MBKok3wsv8vxE4HCVkB9G"
BINANCE_SECRET_KEY = "OH7WLiyXvJNi1dGQubeqkEH4b5emgCdnJ9gUpUUCR6WOvJt3SuEQvELYwpbldYjX"
TELEGRAM_TOKEN = "8485657376:AAHtabaend_BO2bdqxLd7fYTdWg7PyUUTlU"
TELEGRAM_CHAT_ID = "6581268682"
TRADE_AMOUNT = 20
TARGET_PCT = 3.0
STOP_PCT = 1.0
SCAN_SEC = 30
MIN_VOL_SPIKE = 3.0
BINANCE = "https://api.binance.com"
TG = "https://api.telegram.org/bot" + TELEGRAM_TOKEN

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
log = logging.getLogger(__name__)
pending = {}
tracker = {}
volume_history = {}

def sign(params):
    query = urllib.parse.urlencode(params)
    return hmac.new(BINANCE_SECRET_KEY.encode(), query.encode(), hashlib.sha256).hexdigest()

async def send(session, text, markup=None):
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": text, "parse_mode": "HTML"}
    if markup:
        payload["reply_markup"] = json.dumps(markup)
    async with session.post(TG + "/sendMessage", json=payload) as r:
        return await r.json()

async def answer(session, cid, text=""):
    async with session.post(TG + "/answerCallbackQuery", json={"callback_query_id": cid, "text": text}) as r:
        return await r.json()

async def get_updates(session, offset=0):
    async with session.get(TG + "/getUpdates", params={"offset": offset, "timeout": 10}) as r:
        return await r.json()

async def get_price(session, symbol):
    async with session.get(BINANCE + "/api/v3/ticker/price", params={"symbol": symbol}) as r:
        data = await r.json()
        return float(data.get("price", 0))

async def execute_buy(session, symbol, amount_usdt):
    params = {
        "symbol": symbol,
        "side": "BUY",
        "type": "MARKET",
        "quoteOrderQty": amount_usdt,
        "timestamp": int(time.time() * 1000)
    }
    params["signature"] = sign(params)
    headers = {"X-MBX-APIKEY": BINANCE_API_KEY}
    async with session.post(BINANCE + "/api/v3/order", params=params, headers=headers) as r:
        return await r.json()

async def find_pump(session):
    async with session.get(BINANCE + "/api/v3/ticker/24hr") as r:
        tickers = await r.json()

    candidates = []
    for t in tickers:
        s = t.get("symbol", "")
        if not s.endswith("USDT"):
            continue
        try:
            px = float(t["lastPrice"])
            vol_24h = float(t["quoteVolume"])
            ch_24h = float(t["priceChangePercent"])
        except:
            continue

        if px < 0.0001 or vol_24h < 100000:
            continue
        if ch_24h > 20:
            continue

        prev_vol = volume_history.get(s, 0)
        if prev_vol > 0:
            vol_spike = vol_24h / prev_vol
            if vol_spike >= MIN_VOL_SPIKE and ch_24h < 5:
                candidates.append({
                    "symbol": s,
                    "price": px,
                    "change": ch_24h,
                    "vol_spike": round(vol_spike, 1),
                    "volume": vol_24h
                })

        volume_history[s] = vol_24h

    candidates.sort(key=lambda x: x["vol_spike"], reverse=True)
    return candidates[:2]

async def propose(session, trade):
    s = trade["symbol"]
    px = trade["price"]
    ch = trade["change"]
    spike = trade["vol_spike"]
    tgt = round(px * (1 + TARGET_PCT / 100), 6)
    stp = round(px * (1 - STOP_PCT / 100), 6)

    text = (
        "<b>PUMP DETECTED!</b>\n\n"
        "<b>" + s + "</b>\n"
        "Price: $" + str(round(px, 6)) + "\n"
        "Change 24h: " + str(round(ch, 1)) + "%\n"
        "Volume spike: <b>x" + str(spike) + "</b>\n\n"
        "Target: $" + str(tgt) + " (+" + str(TARGET_PCT) + "%)\n"
        "Stop-loss: $" + str(stp) + " (-" + str(STOP_PCT) + "%)\n"
        "Amount: $" + str(TRADE_AMOUNT) + "\n\n"
        "<b>ACT FAST!</b>"
    )
    markup = {"inline_keyboard": [[
        {"text": "APPROVE", "callback_data": "approve_" + s},
        {"text": "REJECT", "callback_data": "reject_" + s}
    ]]}
    await send(session, text, markup)
    pending[s] = {"target": tgt, "stop": stp, "entry": px, "proposed_at": time.time()}
    tracker[s + "_" + str(int(time.time()))] = {
        "symbol": s, "entry": px, "target": tgt, "stop": stp,
        "decision": "pending", "proposed_at": time.time(), "result": None
    }
    log.info("Pump detected: " + s + " vol spike x" + str(spike))

async def check_results(session):
    now = time.time()
    for key, t in list(tracker.items()):
        if t["decision"] == "pending":
            continue
        if t["result"] is not None:
            continue
        if now - t["proposed_at"] < 1800:
            continue
        try:
            current_price = await get_price(session, t["symbol"])
            entry = t["entry"]
            pct = round((current_price - entry) / entry * 100, 2)
            t["result"] = pct
            decision = t["decision"]
            msg = (
                "<b>RESULT after 30 min</b>\n\n"
                "<b>" + t["symbol"] + "</b>\n"
                "Entry: $" + str(round(entry, 6)) + "\n"
                "Now: $" + str(round(current_price, 6)) + "\n"
            )
            if pct > 0:
                msg += "Change: +$" + str(pct) + "%\n\n"
            else:
                msg += "Change: " + str(pct) + "%\n\n"

            if decision == "approved":
                profit = round(TRADE_AMOUNT * pct / 100, 2)
                if pct > 0:
                    msg += "Your decision: APPROVE\nResult: +$" + str(profit) + " PROFIT"
                else:
                    msg += "Your decision: APPROVE\nResult: $" + str(profit) + " LOSS"
            else:
                if pct > 0:
                    msg += "Your decision: REJECT\nWould have made: +$" + str(round(TRADE_AMOUNT * pct / 100, 2))
                else:
                    msg += "Your decision: REJECT\nGood call! Saved: $" + str(abs(round(TRADE_AMOUNT * pct / 100, 2)))
            await send(session, msg)
        except Exception as e:
            log.error("Check result error: " + str(e))

async def handle_cb(session, cb):
    qid = cb["id"]
    data = cb.get("data", "")
    if data.startswith("approve_"):
        s = data[8:]
        trade = pending.get(s)
        if not trade:
            await answer(session, qid, "Expired")
            return
        await answer(session, qid, "Executing...")
        result = await execute_buy(session, s, TRADE_AMOUNT)
        for key, t in tracker.items():
            if t["symbol"] == s and t["decision"] == "pending":
                t["decision"] = "approved"
        if "orderId" in result:
            msg = (
                "BOUGHT: " + s + "\n"
                "Order ID: " + str(result["orderId"]) + "\n"
                "Target: $" + str(trade["target"]) + "\n"
                "Stop: $" + str(trade["stop"]) + "\n\n"
                "Result in 30 min"
            )
            await send(session, msg)
        else:
            await send(session, "Order failed: " + str(result))
        del pending[s]
    elif data.startswith("reject_"):
        s = data[7:]
        await answer(session, qid, "Rejected")
        for key, t in tracker.items():
            if t["symbol"] == s and t["decision"] == "pending":
                t["decision"] = "rejected"
        pending.pop(s, None)
        await send(session, "REJECTED: " + s + "\nResult in 30 min")

async def main():
    log.info("Pump Detector starting...")
    async with aiohttp.ClientSession() as session:
        try:
            my_ip = urllib.request.urlopen("https://api.ipify.org").read().decode()
            await send(session, "Server IP: " + my_ip)
        except:
            pass
        await send(session, (
            "<b>PUMP DETECTOR LIVE!</b>\n\n"
            "Scanning every 30 seconds\n"
            "Looking for volume spikes x3+\n"
            "Amount per trade: $" + str(TRADE_AMOUNT) + "\n"
            "Target: +" + str(TARGET_PCT) + "%\n"
            "Stop-loss: -" + str(STOP_PCT) + "%"
        ))

        log.info("Building volume baseline...")
        async with session.get(BINANCE + "/api/v3/ticker/24hr") as r:
            tickers = await r.json()
        for t in tickers:
            s = t.get("symbol", "")
            if s.endswith("USDT"):
                try:
                    volume_history[s] = float(t["quoteVolume"])
                except:
                    pass
        log.info("Baseline ready: " + str(len(volume_history)) + " pairs")

        last_id = 0
        last_scan = 0
        proposed = set()

        while True:
            try:
                updates = await get_updates(session, offset=last_id + 1)
                for u in updates.get("result", []):
                    last_id = u["update_id"]
                    if "callback_query" in u:
                        await handle_cb(session, u["callback_query"])

                if time.time() - last_scan >= SCAN_SEC:
                    last_scan = time.time()
                    pumps = await find_pump(session)
                    for p in pumps:
                        if p["symbol"] not in proposed and p["symbol"] not in pending:
                            await propose(session, p)
                            proposed.add(p["symbol"])
                            await asyncio.sleep(2)
                    if not pumps:
                        log.info("No pumps detected")

                await check_results(session)
                now = time.time()
                for s in [k for k, v in pending.items() if now - v["proposed_at"] > 300]:
                    del pending[s]
                await asyncio.sleep(2)

            except Exception as e:
                log.error("Error: " + str(e))
                await asyncio.sleep(10)

asyncio.run(main())
