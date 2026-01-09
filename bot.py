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

# --- 2. ALPACA NUCLEAR AUTH FIX (Hardcoded Keys) ---
market = ccxt.alpaca({
    'apiKey': 'PK5TC5IC6AKHSV7L53XB7P5Q6I',
    'secret': '6S1Ka4mue5GEgNtqudrVwd9TBML5Fk7qZegw2xvrtqsR',
})

# Forced V2 Endpoints & Headers to kill 401 Error
market.urls['api']['rest'] = 'https://paper-api.alpaca.markets/v2'
market.headers = {
    'APCA-API-KEY-ID': 'PK5TC5IC6AKHSV7L53XB7P5Q6I',
    'APCA-API-SECRET-KEY': '6S1Ka4mue5GEgNtqudrVwd9TBML5Fk7qZegw2xvrtqsR'
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

# --- 3. THE SCANNER ---
def get_market_data(symbol):
    try:
        tf5 = market.fetch_ohlcv(symbol, timeframe='5m', limit=50)
        tf1 = market.fetch_ohlcv(symbol, timeframe='1m', limit=30)
        ticker = market.fetch_ticker(symbol)
        
        df5 = pd.DataFrame(tf5, columns=['t','o','h','l','c','v'])
        df1 = pd.DataFrame(tf1, columns=['t','o','h','l','c','v'])
        
        e20 = df5['c'].ewm(span=20).mean().iloc[-1]
        e50 = df5['c'].ewm(span=50).mean().iloc[-1]
        bias = "BUY" if e20 > e50 else "SELL"
        
        delta = df1['c'].diff()
        gain = (delta.where(delta > 0, 0)).rolling(window=14).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
        rsi = 100 - (100 / (1 + (gain.iloc[-1] / (loss.iloc[-1] if loss.iloc[-1] != 0 else 1))))
        
        return bias, rsi, ticker['last']
    except: return None

# --- 4. ENGINE (DEBT-KILLER RECOVERY) ---
def trade_engine(chat_id):
    u = get_user(chat_id)
    bot.send_message(chat_id, f"🌪️ **V17 ENGINE ACTIVE**\nTargeting: {u['symbol']}")
    
    while u["is_trading"]:
        try:
            if u["active_trade"] is None:
                data = get_market_data(u["symbol"])
                if not data: time.sleep(10); continue
                
                bias, rsi, price = data
                
                # AI Logic
                prompt = f"ASSET:{u['symbol']}. BIAS:{bias}. RSI:{round(rsi,1)}. Score 0-100. FORMAT:[SIDE]|[SCORE]"
                res = groq_client.chat.completions.create(
                    messages=[{"role":"user","content":prompt}], model="llama-3.3-70b-versatile",
                ).choices[0].message.content.strip().upper()
                
                side = "BUY" if "BUY" in res else "SELL"
                score = int(''.join(filter(str.isdigit, res))) if any(i.isdigit() for i in res) else 0

                if score < 45: time.sleep(15); continue

                # 10 Pip Scalp Math
                tp_dist = 10.0 if "BTC" in u["symbol"] else 1.0
                tp = price + tp_dist if side == "BUY" else price - tp_dist
                sl = price - tp_dist if side == "BUY" else price + tp_dist

                u["active_trade"] = {"side":side, "entry":price, "tp":tp, "sl":sl, "stake":u["current_stake"]}
                u["balance"] -= u["current_stake"]
                bot.send_message(chat_id, f"🔫 **ORDER FIRED {side}**\nEntry: {price} | TP: {round(tp,2)}")

            else:
                curr = market.fetch_ticker(u["symbol"])['last']
                t = u["active_trade"]
                win = ("BUY" in t['side'] and curr >= t['tp']) or ("SELL" in t['side'] and curr <= t['tp'])
                loss = ("BUY" in t['side'] and curr <= t['sl']) or ("SELL" in t['side'] and curr >= t['sl'])

                if win:
                    # 100% Debt Killer Math
                    profit = t['stake']
                    u["balance"] += (t['stake'] + profit + u["total_lost"] + (u["initial_stake"] * 0.1))
                    u["wins"] += 1
                    u["total_lost"], u["current_stake"] = 0, u["initial_stake"]
                    bot.send_message(chat_id, f"✅ **WIN! DEBT RECOVERED.**\nBalance: ${round(u['balance'], 2)}")
                    u["active_trade"] = None
                elif loss:
                    u["total_lost"] += t['stake']
                    u["losses"] += 1
                    # Formula to cover EVERYTHING + Profit
                    u["current_stake"] = (u["total_lost"] + u["initial_stake"]) * 1.5
                    bot.send_message(chat_id, f"❌ **LOSS.** Next Lot: ${round(u['current_stake'], 2)}")
                    u["active_trade"] = None
            
            time.sleep(2)
        except: time.sleep(10)

# --- 5. COMMANDS ---
@bot.message_handler(commands=['start', 'help'])
def help_cmd(m):
    h = "🚀 /trade - Start\n🛑 /stop - Stop\n📊 /status - Stats\n🔄 /reset - Clear\n🛠 /check - API Test"
    bot.reply_to(m, h)

@bot.message_handler(commands=['check'])
def check_api(m):
    try:
        # Manual Headers Check for Nuclear Auth
        ticker = market.fetch_ticker('BTC/USD')
        bal = market.fetch_balance()['total']['USD']
        bot.reply_to(m, f"✅ **AUTHORIZED!**\nBTC/USD: ${ticker['last']}\nBalance: ${bal}")
    except Exception as e:
        bot.reply_to(m, f"❌ **AUTH FAILED:** {str(e)[:100]}")

@bot.message_handler(commands=['status'])
def status_cmd(m):
    u = get_user(m.chat.id)
    bot.send_message(m.chat.id, f"📊 **PnL REPORT**\nBal: ${round(u['balance'],2)}\nDebt: ${round(u['total_lost'],2)}\nWins: {u['wins']} | Loss: {u['losses']}")

@bot.message_handler(commands=['reset'])
def reset_cmd(m):
    users[m.chat.id] = {"balance": 100000.0, "total_lost": 0.0, "wins": 0, "losses": 0, "initial_stake": 50.0, "current_stake": 50.0, "active_trade": None, "is_trading": False}
    bot.reply_to(m, "🔄 Reset Done.")

@bot.message_handler(commands=['stop'])
def stop_cmd(m):
    get_user(m.chat.id)["is_trading"] = False
    bot.reply_to(m, "🛑 Engine Stopping...")

@bot.message_handler(commands=['trade'])
def trade_cmd(m):
    kb = types.ReplyKeyboardMarkup(one_time_keyboard=True, resize_keyboard=True)
    kb.add("BTC/USD", "ETH/USD")
    msg = bot.send_message(m.chat.id, "Select Asset:", reply_markup=kb)
    bot.register_next_step_handler(msg, lambda msg: bot.register_next_step_handler(bot.send_message(m.chat.id, "Stake:"), lambda s: start_v17(msg, s)))

def start_v17(m, s):
    u = get_user(m.chat.id)
    u["symbol"], u["initial_stake"], u["current_stake"], u["is_trading"] = m.text, float(s.text), float(s.text), True
    threading.Thread(target=trade_engine, args=(m.chat.id,), daemon=True).start()

# --- 6. RENDER DEPLOY ---
@app.route('/')
def home(): return "V17 Alpha Alive", 200

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    threading.Thread(target=lambda: app.run(host='0.0.0.0', port=port), daemon=True).start()
    try:
        bot.remove_webhook()
        bot.delete_webhook(drop_pending_updates=True)
        bot.polling(non_stop=True, timeout=60)
    except Exception as e: print(f"Crash: {e}")
