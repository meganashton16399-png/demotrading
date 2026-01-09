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

# --- Alpaca Paper Trading Connection ---
market = ccxt.alpaca({
    'apiKey': 'PKEDOK646QSYPMKIJCOLF5NGGE',
    'secret': 'HWNi68ZoQrNvg5tqa2syUR2iqd2sx6qJzyrJ3WP25vL3',
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

# --- 2. THE MECHANICAL ENGINE ---
def get_market_data(symbol, chat_id):
    try:
        # Fetching 5m for Bias, 1m for Entry
        tf5 = market.fetch_ohlcv(symbol, timeframe='5m', limit=50)
        tf1 = market.fetch_ohlcv(symbol, timeframe='1m', limit=30)
        ticker = market.fetch_ticker(symbol)
        
        df5 = pd.DataFrame(tf5, columns=['t','o','h','l','c','v'])
        df1 = pd.DataFrame(tf1, columns=['t','o','h','l','c','v'])
        
        # 5m Bias (EMA 20 vs 50)
        e20 = df5['c'].ewm(span=20).mean().iloc[-1]
        e50 = df5['c'].ewm(span=50).mean().iloc[-1]
        bias = "BUY" if e20 > e50 else "SELL"
        
        # 1m RSI (14)
        delta = df1['c'].diff()
        gain = (delta.where(delta > 0, 0)).rolling(window=14).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
        rsi = 100 - (100 / (1 + (gain.iloc[-1] / (loss.iloc[-1] if loss.iloc[-1] != 0 else 1))))
        
        return bias, rsi, ticker['last'], df1.iloc[-2].to_dict()
    except Exception as e:
        print(f"Data Fetch Error: {e}")
        return None

# --- 3. AI PROBABILITY (ZERO HESITATION) ---
def get_ai_v15(symbol, chat_id):
    data = get_market_data(symbol, chat_id)
    if not data: return None, None, "Data Err", 0
    bias, rsi, price, last_c = data

    # Short message to show activity
    pulse = bot.send_message(chat_id, f"📡 Scanning {symbol}...")

    prompt = (
        f"ASSET: {symbol}. BIAS: {bias}. RSI: {round(rsi,1)}. PRICE: {price}. "
        f"RULES: Scalp 10 pips. AI Confidence MUST be > 40. "
        f"OUTPUT: [SIDE/SKIP] | [SCORE] | [REASON 5 WORDS]"
    )

    try:
        res = groq_client.chat.completions.create(
            messages=[{"role": "user", "content": prompt}],
            model="llama-3.3-70b-versatile",
        ).choices[0].message.content.strip().upper()
        
        bot.delete_message(chat_id, pulse.message_id)
        parts = res.split("|")
        side = parts[0].strip()
        score = int(''.join(filter(str.isdigit, parts[1]))) if len(parts) > 1 else 0
        reason = parts[2].strip() if len(parts) > 2 else "Market Momentum"
        
        if score < 40 or "SKIP" in side: return "SKIP", price, reason, score
        return side, price, reason, score
    except:
        bot.delete_message(chat_id, pulse.message_id)
        return None, None, "AI Busy", 0

# --- 4. 100% RECOVERY ENGINE ---
def trade_engine(chat_id):
    u = get_user(chat_id)
    bot.send_message(chat_id, "🚀 **ALPACA TURBO SNIPER V15.3 ACTIVE**")
    
    while u["is_trading"]:
        try:
            if u["active_trade"] is None:
                side, price, reason, score = get_ai_v15(u["symbol"], chat_id)
                if not side or side == "SKIP":
                    time.sleep(10); continue

                # 10 Pip Scalp Logic
                tp_dist = 10.0 if "BTC" in u["symbol"] else 1.0
                tp = price + tp_dist if side == "BUY" else price - tp_dist
                sl = price - tp_dist if side == "BUY" else price + tp_dist

                u["active_trade"] = {"side": side, "entry": price, "tp": tp, "sl": sl, "stake": u["current_stake"]}
                u["balance"] -= u["current_stake"]
                bot.send_message(chat_id, f"🔫 **ORDER FIRED: {side} ({score}%)**\nEntry: {price}\nReason: {reason}")

            else:
                # Instant Price Check (1s interval)
                ticker = market.fetch_ticker(u["symbol"])
                curr = ticker['last']
                t = u["active_trade"]
                
                win = ("BUY" in t['side'] and curr >= t['tp']) or ("SELL" in t['side'] and curr <= t['tp'])
                loss = ("BUY" in t['side'] and curr <= t['sl']) or ("SELL" in t['side'] and curr >= t['sl'])

                if win:
                    # 100% Recovery Math: Win covers all past debt + Initial Stake Profit
                    profit = t['stake'] # 1:1 RR
                    u["balance"] += (t['stake'] + profit + u["total_lost"] + (u["initial_stake"] * 0.1))
                    u["wins"] += 1
                    u["total_lost"], u["current_stake"] = 0, u["initial_stake"]
                    bot.send_message(chat_id, f"✅ **WIN! LOSS RECOVERED.**\nBalance: ${round(u['balance'], 2)}")
                    u["active_trade"] = None
                elif loss:
                    u["total_lost"] += t['stake']
                    u["losses"] += 1
                    # Formula to cover EVERYTHING in next trade:
                    u["current_stake"] = (u["total_lost"] + u["initial_stake"]) * 1.5
                    bot.send_message(chat_id, f"❌ **LOSS.** Next Stake: ${round(u['current_stake'], 2)}")
                    u["active_trade"] = None
            
            time.sleep(1) # Fast heartbeat
        except Exception as e:
            time.sleep(10)

# --- 5. COMMANDS ---
@bot.message_handler(commands=['help', 'start'])
def help_cmd(m):
    text = ("🤖 **V15.3 COMMANDS**\n"
            "/trade - Start Engine\n"
            "/stop - Stop Engine\n"
            "/status - View PnL\n"
            "/reset - Reset Wallet\n"
            "/check - Test Keys")
    bot.reply_to(m, text)

@bot.message_handler(commands=['check'])
def check(m):
    try:
        ticker = market.fetch_ticker('BTC/USD')
        bot.reply_to(m, f"✅ **Alpaca Connected.**\nBTC/USD: ${ticker['last']}")
    except Exception as e:
        bot.reply_to(m, f"❌ **Key Error:** {str(e)[:50]}")

@bot.message_handler(commands=['status'])
def report(m):
    u = get_user(m.chat.id)
    bot.send_message(m.chat.id, f"📊 **V15.3 REPORT**\nBal: ${round(u['balance'],2)}\nWins: {u['wins']} | Loss: {u['losses']}\nDebt: ${round(u['total_lost'],2)}")

@bot.message_handler(commands=['reset'])
def reset(m):
    users[m.chat.id] = {"balance": 100000.0, "total_lost": 0.0, "wins": 0, "losses": 0, "initial_stake": 50.0, "current_stake": 50.0, "active_trade": None, "is_trading": False}
    bot.reply_to(m, "🔄 Reset Complete.")

@bot.message_handler(commands=['trade'])
def trade_init(m):
    kb = types.ReplyKeyboardMarkup(one_time_keyboard=True, resize_keyboard=True)
    kb.add("BTC/USD", "ETH/USD")
    msg = bot.send_message(m.chat.id, "Select Asset:", reply_markup=kb)
    bot.register_next_step_handler(msg, lambda msg: bot.register_next_step_handler(bot.send_message(m.chat.id, "Base Stake:"), lambda s: start_v15(msg, s)))

def start_v15(m, s):
    u = get_user(m.chat.id)
    u["symbol"], u["initial_stake"], u["current_stake"], u["is_trading"] = m.text, float(s.text), float(s.text), True
    threading.Thread(target=trade_engine, args=(m.chat.id,), daemon=True).start()

@bot.message_handler(commands=['stop'])
def stop(m): get_user(m.chat.id)["is_trading"] = False; bot.reply_to(m, "🛑 Stopped.")

@app.route('/')
def home(): return "V15.3 Turbo Active", 200

if __name__ == "__main__":
    threading.Thread(target=lambda: app.run(host='0.0.0.0', port=int(os.environ.get("PORT", 5000))), daemon=True).start()
    bot.remove_webhook()
    bot.delete_webhook(drop_pending_updates=True)
    bot.polling(non_stop=True)
