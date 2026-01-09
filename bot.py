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
# Tokens for Telegram and Groq (Keep these in Render Env Variables)
TELE_TOKEN = os.environ.get("BOT_TOKEN")
GROQ_KEY = os.environ.get("GROQ_API_KEY")

bot = telebot.TeleBot(TELE_TOKEN)
groq_client = Groq(api_key=GROQ_KEY)
app = Flask(__name__)

# --- 2. ALPACA HARDCODED SETUP ---
# Directly using your provided keys to bypass ENV issues
market = ccxt.alpaca({
    'apiKey': 'PK5TC5IC6AKHSV7L53XB7P5Q6I',
    'secret': '6S1Ka4mue5GEgNtqudrVwd9TBML5Fk7qZegw2xvrtqsR',
    'urls': {
        'api': {'rest': 'https://paper-api.alpaca.markets'} 
    }
})

users = {}

def get_user(chat_id):
    if chat_id not in users:
        users[chat_id] = {
            "balance": 100000.0, "total_lost": 0.0, "wins": 0, "losses": 0,
            "initial_stake": 50.0, "current_stake": 50.0,
            "active_trade": None, "is_trading": False, "symbol": "BTC/USD"
        }
    return users[chat_id]

# --- 3. MARKET DATA ENGINE ---
def get_market_data(symbol):
    try:
        tf5 = market.fetch_ohlcv(symbol, timeframe='5m', limit=50)
        tf1 = market.fetch_ohlcv(symbol, timeframe='1m', limit=30)
        ticker = market.fetch_ticker(symbol)
        
        df5 = pd.DataFrame(tf5, columns=['t','o','h','l','c','v'])
        df1 = pd.DataFrame(tf1, columns=['t','o','h','l','c','v'])
        
        # EMA 20/50 Bias
        e20 = df5['c'].ewm(span=20).mean().iloc[-1]
        e50 = df5['c'].ewm(span=50).mean().iloc[-1]
        bias = "BUY" if e20 > e50 else "SELL"
        
        # RSI 14
        delta = df1['c'].diff()
        gain = (delta.where(delta > 0, 0)).rolling(window=14).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
        rsi = 100 - (100 / (1 + (gain.iloc[-1] / (loss.iloc[-1] if loss.iloc[-1] != 0 else 1))))
        
        return bias, rsi, ticker['last']
    except Exception as e:
        print(f"Data Fetch Error: {e}")
        return None

# --- 4. THE TRADE ENGINE (DEBT-KILLER) ---
def trade_engine(chat_id):
    u = get_user(chat_id)
    bot.send_message(chat_id, f"🚀 **V16 ENGINE STARTED**\nTargeting: {u['symbol']}")
    
    while u["is_trading"]:
        try:
            if u["active_trade"] is None:
                data = get_market_data(u["symbol"])
                if not data:
                    time.sleep(10); continue
                
                bias, rsi, price = data
                
                # AI Decision Call
                prompt = f"ASSET: {u['symbol']}. BIAS: {bias}. RSI: {round(rsi,1)}. PRICE: {price}. SIDE? Score 0-100."
                res = groq_client.chat.completions.create(
                    messages=[{"role": "user", "content": prompt}],
                    model="llama-3.3-70b-versatile",
                ).choices[0].message.content.strip().upper()
                
                side = "BUY" if "BUY" in res else "SELL"
                score = int(''.join(filter(str.isdigit, res))) if any(i.isdigit() for i in res) else 0

                if score < 45: 
                    time.sleep(15); continue

                # 10 Pip Target Calculation
                tp_dist = 10.0 if "BTC" in u["symbol"] else 1.0
                tp = price + tp_dist if side == "BUY" else price - tp_dist
                sl = price - tp_dist if side == "BUY" else price + tp_dist

                u["active_trade"] = {"side": side, "entry": price, "tp": tp, "sl": sl, "stake": u["current_stake"]}
                u["balance"] -= u["current_stake"]
                bot.send_message(chat_id, f"🔫 **SNIPER FIRED: {side}**\nEntry: {price}\nTarget: {round(tp,2)}")

            else:
                # Real-time monitoring loop
                curr = market.fetch_ticker(u["symbol"])['last']
                t = u["active_trade"]
                
                win = ("BUY" in t['side'] and curr >= t['tp']) or ("SELL" in t['side'] and curr <= t['tp'])
                loss = ("BUY" in t['side'] and curr <= t['sl']) or ("SELL" in t['side'] and curr >= t['sl'])

                if win:
                    # 100% Recovery Math
                    profit = t['stake'] # 1:1 RR
                    u["balance"] += (t['stake'] + profit + u["total_lost"] + (u["initial_stake"] * 0.1))
                    u["wins"] += 1
                    u["total_lost"], u["current_stake"] = 0, u["initial_stake"]
                    bot.send_message(chat_id, f"✅ **WIN! DEBT RECOVERED.**\nBalance: ${round(u['balance'], 2)}")
                    u["active_trade"] = None
                elif loss:
                    u["total_lost"] += t['stake']
                    u["losses"] += 1
                    # Formula to cover EVERYTHING in next trade
                    u["current_stake"] = (u["total_lost"] + u["initial_stake"]) * 1.5
                    bot.send_message(chat_id, f"❌ **LOSS.** Next Lot: ${round(u['current_stake'], 2)}")
                    u["active_trade"] = None
            
            time.sleep(2)
        except Exception as e:
            time.sleep(10)

# --- 5. COMMAND HANDLERS ---

@bot.message_handler(commands=['start', 'help'])
def help_cmd(m):
    text = (
        "🤖 **V16 FINAL ALPHA COMMANDS**\n\n"
        "🚀 /trade - Start Trading\n"
        "🛑 /stop - Stop Engine\n"
        "📊 /status - View Balance\n"
        "🔄 /reset - Wipe Data\n"
        "🛠 /check - Test API Connection"
    )
    bot.reply_to(m, text)

@bot.message_handler(commands=['check'])
def check_api(m):
    try:
        ticker = market.fetch_ticker('BTC/USD')
        bot.reply_to(m, f"✅ **ALPACA KEY VALID**\nBTC/USD Price: ${ticker['last']}\nStatus: Paper Trading Authorized.")
    except Exception as e:
        bot.reply_to(m, f"❌ **AUTH ERROR:** {str(e)}")

@bot.message_handler(commands=['status'])
def status_cmd(m):
    u = get_user(m.chat.id)
    rate = (u['wins'] / (u['wins'] + u['losses']) * 100) if (u['wins'] + u['losses']) > 0 else 0
    bot.send_message(m.chat.id, 
        f"📊 **LIVE STATUS**\nBal: ${round(u['balance'], 2)}\nDebt: ${round(u['total_lost'], 2)}\nWin Rate: {round(rate, 1)}%\nWins/Loss: {u['wins']}W / {u['losses']}L")

@bot.message_handler(commands=['reset'])
def reset_cmd(m):
    users[m.chat.id] = {"balance": 100000.0, "total_lost": 0.0, "wins": 0, "losses": 0, "initial_stake": 50.0, "current_stake": 50.0, "active_trade": None, "is_trading": False}
    bot.reply_to(m, "🔄 Reset Complete. Balance: $100,000")

@bot.message_handler(commands=['stop'])
def stop_cmd(m):
    u = get_user(m.chat.id)
    u["is_trading"] = False
    bot.reply_to(m, "🛑 Engine Stopping...")

@bot.message_handler(commands=['trade'])
def trade_cmd(m):
    kb = types.ReplyKeyboardMarkup(one_time_keyboard=True, resize_keyboard=True)
    kb.add("BTC/USD", "ETH/USD")
    msg = bot.send_message(m.chat.id, "Select Asset:", reply_markup=kb)
    bot.register_next_step_handler(msg, asset_step)

def asset_step(m):
    u = get_user(m.chat.id)
    u["symbol"] = m.text
    msg = bot.send_message(m.chat.id, f"Enter Base Stake (e.g. 100):")
    bot.register_next_step_handler(msg, final_step)

def final_step(m):
    try:
        u = get_user(m.chat.id)
        u["initial_stake"] = float(m.text)
        u["current_stake"] = u["initial_stake"]
        u["is_trading"] = True
        threading.Thread(target=trade_engine, args=(m.chat.id,), daemon=True).start()
    except:
        bot.send_message(m.chat.id, "❌ Invalid stake value.")

# --- 6. RENDER DEPLOYMENT ---
@app.route('/')
def health_check():
    return "V16 Sniper Online", 200

if __name__ == "__main__":
    #滿足 Render 端口探測
    port = int(os.environ.get("PORT", 10000))
    threading.Thread(target=lambda: app.run(host='0.0.0.0', port=port), daemon=True).start()
    
    # Telegram Polling
    try:
        bot.remove_webhook()
        bot.delete_webhook(drop_pending_updates=True)
        bot.polling(non_stop=True, timeout=60)
    except Exception as e:
        print(f"Polling Crash: {e}")
