import urllib.request
import asyncio, logging, time, json
import aiohttp

BINANCE_API_KEY = "qZ3dMupVbwtrX40OrLJgWpTMpTQGFq1XIpKAs5iMdZ7MBKok3wsv8vxE4HCVkB9G"
BINANCE_SECRET_KEY = "OH7WLiyXvJNi1dGQubeqkEH4b5emgCdnJ9gUpUUCR6WOvJt3SuEQvELYwpbldYjX"TELEGRAM_TOKEN = "8485657376:AAHtabaend_BO2bdqxLd7fYTdWg7PyUUTlU"
TELEGRAM_CHAT_ID = "6581268682"
TRADE_AMOUNT = 50
TARGET_PCT = 3.0
STOP_PCT = 1.5
SCAN_SEC = 60
MIN_VOL = 1000000
BINANCE = "https://api.binance.com"
TG = "https://api.telegram.org/bot" + TELEGRAM_TOKEN

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
log = logging.getLogger(__name__)
pending = {}

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

async def find_opps(session):
    async with session.get(BINANCE + "/api/v3/ticker/24hr") as r:
        tickers = await r.json()
    out = []
    for t in tickers:
        s = t.get("symbol", "")
        if not s.endswith("USDT"):
            continue
        try:
            ch = float(t["priceChangePercent"])
            vol = float(t["quoteVolume"])
            px = float(t["lastPrice"])
        except:
            continue
        if vol < MIN_VOL or ch < 2.0 or px < 0.0001:
            continue
        out.append({"symbol": s, "change": ch, "volume": vol, "price": px})
    out.sort(key=lambda x: x["change"], reverse=True)
    return out[:3]

async def propose(session, trade):
    s = trade["symbol"]
    px = trade["price"]
    ch = trade["change"]
    tgt = round(px * (1 + TARGET_PCT / 100), 6)
    stp = round(px * (1 - STOP_PCT / 100), 6)
    lines = [
        "<b>TRADE OPPORTUNITY</b>",
        "",
        "<b>" + s + "</b>",
        "Price: $" + str(round(px, 6)),
        "Change 24h: +" + str(round(ch, 1)) + "%",
        "",
        "Target: $" + str(tgt) + " (+" + str(TARGET_PCT) + "%)",
        "Stop-loss: $" + str(stp) + " (-" + str(STOP_PCT) + "%)",
        "Amount: $" + str(TRADE_AMOUNT),
    ]
    text = "\n".join(lines)
    markup = {"inline_keyboard": [[
        {"text": "APPROVE", "callback_data": "approve_" + s},
        {"text": "REJECT", "callback_data": "reject_" + s}
    ]]}
    await send(session, text, markup)
    pending[s] = {"target": tgt, "stop": stp, "proposed_at": time.time()}
    log.info("Proposed: " + s)

async def handle_cb(session, cb):
    qid = cb["id"]
    data = cb.get("data", "")
    if data.startswith("approve_"):
        s = data[8:]
        trade = pending.get(s)
        if not trade:
            await answer(session, qid, "Expired")
            return
        await answer(session, qid, "Approved!")
        msg = "APPROVED: " + s + "\nTarget: $" + str(trade["target"]) + "\nStop: $" + str(trade["stop"]) + "\n\nDEMO MODE - add Secret Key for real trading"
        await send(session, msg)
        del pending[s]
    elif data.startswith("reject_"):
        s = data[7:]
        await answer(session, qid, "Rejected")
        pending.pop(s, None)
        await send(session, "REJECTED: " + s)

async def main():
    log.info("Bot starting...")
    try:
        my_ip = urllib.request.urlopen("https://api.ipify.org").read().decode()
        log.info("Server IP: " + my_ip)
    except:
        pass
    async with aiohttp.ClientSession() as session:
        try:
            my_ip = urllib.request.urlopen("https://api.ipify.org").read().decode()
            await send(session, "Server IP: " + my_ip)
        except:
            pass
        await send(session, "<b>Bot is LIVE!</b>\nScanning every 60 seconds...")
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
                    log.info("Scanning market...")
                    opps = await find_opps(session)
                    for o in opps:
                        if o["symbol"] not in proposed and o["symbol"] not in pending:
                            await propose(session, o)
                            proposed.add(o["symbol"])
                            await asyncio.sleep(2)
                now = time.time()
                for s in [k for k, v in pending.items() if now - v["proposed_at"] > 600]:
                    del pending[s]
                await asyncio.sleep(2)
            except Exception as e:
                log.error("Error: " + str(e))
                await asyncio.sleep(10)

asyncio.run(main())
