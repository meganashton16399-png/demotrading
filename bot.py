import telebot
from telebot import types
import ccxt
import time
import os
import threading
from groq import Groq
from flask import Flask
import pandas as pd

# --- 1. CONFIG ---
TELE_TOKEN = os.environ.get("BOT_TOKEN")
GROQ_KEY = os.environ.get("GROQ_API_KEY")

bot = telebot.TeleBot(TELE_TOKEN)
groq_client = Groq(api_key=GROQ_KEY)
app = Flask(__name__)

# --- 2. THE CLEANER (Nuclear Solution) ---
# Bhai, Alpaca ke liye Key ID 20 chars aur Secret 40 chars hona must hai.
RAW_KEY = 'PK5TC5IC6AKHSV7L53XB7P5Q6I'.strip()
RAW_SECRET = '6S1Ka4mue5GEgNtqudrVwd9TBML5Fk7qZegw2xvrtqsR'.strip()

# Auto-trim to standard lengths
CLEAN_KEY = RAW_KEY[:20] if len(RAW_KEY) > 20 else RAW_KEY
CLEAN_SECRET = RAW_SECRET[:40] if len(RAW_SECRET) > 40 else RAW_SECRET

market = ccxt.alpaca({
    'apiKey': CLEAN_KEY,
    'secret': CLEAN_SECRET,
})

# Forced Endpoint Fix
market.urls['api']['rest'] = 'https://paper-api.alpaca.markets/v2'
market.headers = {
    'APCA-API-KEY-ID': CLEAN_KEY,
    'APCA-API-SECRET-KEY': CLEAN_SECRET
}

users = {}

def get_user(chat_id):
    if chat_id not in users:
        users[chat_id] = {
            "balance": 100000.0, "total_lost": 0.0, "wins": 0, "losses": 0,
            "initial_stake": 100.0, "current_stake": 100.0,
            "active_trade": None, "is_trading": False, "symbol": "BTC/USD"
        }
    return users[chat_id]

# --- 3. THE HEARTBEAT (Market Scanner) ---
def get_market_data(symbol):
    try:
        ticker = market.fetch_ticker(symbol)
        tf5 = market.fetch_ohlcv(symbol, timeframe='5m', limit=50)
        df5 = pd.DataFrame(tf5, columns=['t','o','h','l','c','v'])
        e20 = df5['c'].ewm(span=20).mean().iloc[-1]
        e50 = df5['c'].ewm(span=50).mean().iloc[-1]
        return ("BUY" if e20 > e50 else "SELL"), ticker['last']
    except: return None

# --- 4. THE DEBT-KILLER ENGINE ---
def trade_engine(chat_id):
    u = get_user(chat_id)
    bot.send_message(chat_id, "🌪️ **V19 SNIPER ONLINE**")
    
    while u["is_trading"]:
        try:
            if u["active_trade"] is None:
                data = get_market_data(u["symbol"])
                if not data: time.sleep(10); continue
                
                bias, price = data
                prompt = f"BIAS:{bias} | PRICE:{price}. Format: [SIDE]|[SCORE]"
                res = groq_client.chat.completions.create(
                    messages=[{"role":"user","content":prompt}], model="llama-3.3-70b-versatile"
                ).choices[0].message.content.strip().upper()
                
                side = "BUY" if "BUY" in res else "SELL"
                score = int(''.join(filter(str.isdigit, res))) if any(i.isdigit() for i in res) else 0

                if score < 45: time.sleep(15); continue

                # 10 Pip Scalp
                dist = 10.0 if "BTC" in u["symbol"] else 1.0
                tp = price + dist if side == "BUY" else price - dist
                sl = price - dist if side == "BUY" else price + dist

                u["active_trade"] = {"side":side, "entry":price, "tp":tp, "sl":sl, "stake":u["current_stake"]}
                u["balance"] -= u["current_stake"]
                bot.send_message(chat_id, f"🔫 **ORDER FIRED {side}**\nTarget: {round(tp,2)}")

            else:
                ticker = market.fetch_ticker(u["symbol"])
                curr = ticker['last']
                t = u["active_trade"]
                win = ("BUY" in t['side'] and curr >= t['tp']) or ("SELL" in t['side'] and curr <= t['tp'])
                loss = ("BUY" in t['side'] and curr <= t['sl']) or ("SELL" in t['side'] and curr >= t['sl'])

                if win:
                    profit = t['stake']
                    u["balance"] += (t['stake'] + profit + u["total_lost"] + (u["initial_stake"] * 0.15))
                    u["wins"] += 1
                    u["total_lost"], u["current_stake"] = 0, u["initial_stake"]
                    bot.send_message(chat_id, f"✅ **WIN! RECOVERED.**\nBal: ${round(u['balance'], 2)}")
                    u["active_trade"] = None
                elif loss:
                    u["total_lost"] += t['stake']
                    u["losses"] += 1
                    u["current_stake"] = (u["total_lost"] + u["initial_stake"]) * 1.5
                    bot.send_message(chat_id, f"❌ **LOSS.** Next: ${round(u['current_stake'], 2)}")
                    u["active_trade"] = None
            
            time.sleep(1)
        except: time.sleep(10)

# --- 5. COMMANDS ---
@bot.message_handler(commands=['check'])
def check_api(m):
    try:
        # Step 1: Manual Price Test
        ticker = market.fetch_ticker('BTC/USD')
        # Step 2: Full Auth Balance Test
        balance = market.fetch_balance()
        bot.reply_to(m, f"✅ **AUTH FIXED!**\nBTC: ${ticker['last']}\nPaper Balance: ${balance['total']['USD']}")
    except Exception as e:
        bot.reply_to(m, f"❌ **DEBUG:** Key length used: {len(CLEAN_KEY)} ID / {len(CLEAN_SECRET)} Sec.\nError: {str(e)[:100]}")

@bot.message_handler(commands=['trade'])
def trade_cmd(m):
    kb = types.ReplyKeyboardMarkup(one_time_keyboard=True, resize_keyboard=True)
    kb.add("BTC/USD", "ETH/USD")
    msg = bot.send_message(m.chat.id, "Select Asset:", reply_markup=kb)
    bot.register_next_step_handler(msg, lambda msg: bot.register_next_step_handler(bot.send_message(m.chat.id, "Stake:"), lambda s: start_v19(msg, s)))

def start_v19(m, s):
    u = get_user(m.chat.id)
    u["symbol"], u["initial_stake"], u["current_stake"], u["is_trading"] = m.text, float(s.text), float(s.text), True
    threading.Thread(target=trade_engine, args=(m.chat.id,), daemon=True).start()

@bot.message_handler(commands=['status', 'reset', 'stop', 'help'])
def utils(m):
    if 'status' in m.text:
        u = get_user(m.chat.id)
        bot.reply_to(m, f"📊 Bal: ${round(u['balance'],2)} | Debt: ${round(u['total_lost'],2)}\nWins: {u['wins']} | Loss: {u['losses']}")
    elif 'reset' in m.text:
        users[m.chat.id] = {"balance": 100000.0, "total_lost": 0.0, "wins": 0, "losses": 0, "initial_stake": 100.0, "current_stake": 100.0, "active_trade": None, "is_trading": False}
        bot.reply_to(m, "🔄 Reset Done.")
    elif 'stop' in m.text:
        get_user(m.chat.id)["is_trading"] = False; bot.reply_to(m, "🛑 Stopped.")
    else: bot.reply_to(m, "/trade, /status, /check, /reset, /stop")

# --- 6. RENDER DEPLOY ---
@app.route('/')
def home(): return "V19 Atomic Sniper Active", 200

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    threading.Thread(target=lambda: app.run(host='0.0.0.0', port=port), daemon=True).start()
    bot.remove_webhook()
    bot.polling(non_stop=True)
