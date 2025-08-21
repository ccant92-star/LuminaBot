import discord
from discord.ext import tasks, commands
import requests
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from timezonefinder import TimezoneFinder
import random
import os
import json
from flask import Flask
import threading

# --- Environment Variables ---
TOKEN = os.getenv("TOKEN")
CHANNEL_ID = int(os.getenv("CHANNEL_ID"))

if not TOKEN or not CHANNEL_ID:
    raise ValueError("TOKEN and CHANNEL_ID must be set in env")

# --- Discord Bot Setup ---
intents = discord.Intents.default()
intents.message_content = True
intents.members = True  # needed for welcome messages
bot = commands.Bot(command_prefix="!", intents=intents)

# --- Globals ---
user_zips = {}           # user_id: {zip, lat, lon}
sales_data = {}          # user_id: {"emoji": "🛒", "daily": 0, "weekly": 0}
posted_alerts = {}       # alert_id: end_datetime
sandbox_mode = False
sandbox_channel_id = None
quiet_until = None
tf = TimezoneFinder()

DATA_FILE = "lumina_data.json"

# --- Load persistent data ---
if os.path.exists(DATA_FILE):
    try:
        with open(DATA_FILE, "r") as f:
            data = json.load(f)
            user_zips = data.get("user_zips", {})
            sales_data = data.get("sales_data", {})
    except:
        pass

def save_data():
    with open(DATA_FILE, "w") as f:
        json.dump({"user_zips": user_zips, "sales_data": sales_data}, f)

# --- Safety Advice ---
SAFETY_ADVICE = {
    "Tornado Warning": "🌪️ Take shelter immediately in a basement or interior room on the lowest floor, away from windows.",
    "Severe Thunderstorm Warning": "⛈️ Stay indoors, avoid windows, and unplug electronics. Do not drive through flooded roads.",
    "Flash Flood Warning": "🌊 Move to higher ground immediately. Never drive into floodwaters.",
    "Heat Advisory": "🥵 Stay hydrated, avoid strenuous activity, and check on vulnerable people.",
    "Winter Storm Warning": "❄️ Stay off roads if possible, keep warm, and have supplies in case of power outage.",
    "High Wind Warning": "💨 Secure loose objects outdoors, avoid driving high-profile vehicles, and stay indoors.",
    "Excessive Heat Warning": "🔥 Stay indoors in AC if possible, drink plenty of water, and avoid outdoor activity.",
    "Hurricane Warning": "🌀 Follow evacuation orders. Move to higher ground, stay indoors away from windows.",
    "Tropical Storm Warning": "🌧️ Prepare for flooding and strong winds. Stay indoors if possible.",
    "Wildfire Warning": "🔥 Be ready to evacuate if ordered. Avoid breathing smoke and keep N95 masks if available.",
    "Dense Fog Advisory": "🌫️ If driving, use low beams, slow down, and allow extra distance.",
    "Blizzard Warning": "❄️ Avoid travel, stay indoors, and ensure you have food, water, and heat sources.",
}

EVENT_SHORTHAND = {
    "tornado": "Tornado Warning",
    "twatch": "Tornado Watch",
    "tstorm": "Severe Thunderstorm Warning",
    "tstormwatch": "Severe Thunderstorm Watch",
    "flashflood": "Flash Flood Warning",
    "ffwatch": "Flash Flood Watch",
    "heat": "Heat Advisory",
    "winter": "Winter Storm Warning",
    "wind": "High Wind Warning",
    "excessiveheat": "Excessive Heat Warning",
    "hurricane": "Hurricane Warning",
    "tropical": "Tropical Storm Warning",
    "wildfire": "Wildfire Warning",
    "fog": "Dense Fog Advisory",
    "blizzard": "Blizzard Warning"
}

# --- ZIP → lat/lon using Zippopotam ---
def zip_to_coords(zip_code):
    try:
        res = requests.get(f"http://api.zippopotam.us/us/{zip_code}")
        if res.status_code == 200:
            data = res.json()
            lat = float(data['places'][0]['latitude'])
            lon = float(data['places'][0]['longitude'])
            return lat, lon
    except:
        return None
    return None

# --- UTC → local ---
def to_local_time(utc_str, lat, lon):
    try:
        if not utc_str:
            return "N/A"
        utc_dt = datetime.fromisoformat(utc_str.replace("Z", "+00:00"))
        tz_name = tf.timezone_at(lat=lat, lng=lon)
        if tz_name:
            local_dt = utc_dt.astimezone(ZoneInfo(tz_name))
            return local_dt.strftime("%Y-%m-%d %I:%M %p %Z")
    except:
        return utc_str
    return utc_str

# --- Get NOAA alerts ---
def check_weather_alert(lat, lon):
    url = f"https://api.weather.gov/alerts/active?point={lat},{lon}"
    res = requests.get(url, headers={"User-Agent": "LuminaBot"})
    new_alerts = []
    if res.status_code == 200:
        data = res.json()
        alerts = data.get("features", [])
        now = datetime.utcnow()
        for alert in alerts:
            alert_id = alert["id"]
            props = alert["properties"]
            try:
                end_time = datetime.fromisoformat(props["ends"].replace("Z", "+00:00"))
            except:
                end_time = now + timedelta(hours=1)
            if alert_id not in posted_alerts or posted_alerts[alert_id] < now:
                event = props["event"]
                advice = SAFETY_ADVICE.get(event, "⚠️ Stay alert and follow local emergency guidance.")
                start_local = to_local_time(props["effective"], lat, lon)
                end_local = to_local_time(props["ends"], lat, lon)
                new_alerts.append(
                    (alert_id,
                     f"⚠️ **{event}**\n"
                     f"{props['headline']}\n"
                     f"⏰ {start_local} → {end_local}\n\n"
                     f"👉 **Safety Advice:** {advice}",
                     end_time)
                )
    return new_alerts

# --- Weather registration ---
@bot.command()
async def weather(ctx, zip_code: str = None):
    user_id = str(ctx.author.id)
    if zip_code:
        coords = zip_to_coords(zip_code)
        if coords:
            user_zips[user_id] = {"zip": zip_code, "lat": coords[0], "lon": coords[1]}
            save_data()
            await ctx.send(f"✅ {ctx.author.mention}, your ZIP {zip_code} has been registered for weather alerts.")
        else:
            await ctx.send("❌ Could not find that ZIP code. Please try again.")
    else:
        info = user_zips.get(user_id)
        if not info:
            await ctx.send("❌ No ZIP registered. Use `!weather [ZIP]` to register.")
            return
        new_alerts = check_weather_alert(info["lat"], info["lon"])
        if new_alerts:
            for _, msg, _ in new_alerts:
                await ctx.send(msg)
        else:
            await ctx.send(f"✅ {ctx.author.mention}, no active alerts for ZIP {info['zip']}.")

# --- Sales leaderboard ---
@bot.command()
async def setemoji(ctx, emoji: str):
    uid = str(ctx.author.id)
    if uid not in sales_data:
        sales_data[uid] = {"emoji": emoji, "daily": 0, "weekly": 0}
    else:
        sales_data[uid]["emoji"] = emoji
    save_data()
    await ctx.send(f"✅ {ctx.author.mention}, your sales emoji has been set to {emoji}.")

@bot.command()
async def repsale(ctx):
    channel = bot.get_channel(CHANNEL_ID)
    if not sales_data:
        await channel.send("❌ No sales reported today.")
        return
    sorted_sales = sorted(sales_data.items(), key=lambda x: x[1].get("daily",0), reverse=True)
    today = datetime.now().strftime("%Y-%m-%d")
    msg = f"📊 **Daily Sales Leaderboard – {today}**\n"
    for idx, (uid, data) in enumerate(sorted_sales, start=1):
        emoji = data.get("emoji","🛒")
        count = data.get("daily",0)
        msg += f"{idx}. <@{uid}> {emoji} x{count}\n"
    await channel.send(msg)

@bot.command()
async def weeklyleaderboard(ctx):
    channel = bot.get_channel(CHANNEL_ID)
    if not sales_data:
        await channel.send("❌ No sales reported this week.")
        return
    sorted_sales = sorted(sales_data.items(), key=lambda x: x[1].get("weekly",0), reverse=True)
    today = datetime.now().strftime("%Y-%m-%d")
    msg = f"📊 **Weekly Sales Leaderboard – {today}**\n"
    for idx, (uid, data) in enumerate(sorted_sales, start=1):
        emoji = data.get("emoji","🛒")
        count = data.get("weekly",0)
        msg += f"{idx}. <@{uid}> {emoji} x{count}\n"
    await channel.send(msg)

# --- Inspirational quotes ---
def get_quote():
    try:
        res = requests.get("https://zenquotes.io/api/random")
        if res.status_code == 200:
            q = res.json()[0]
            return f"{q['q']} — {q['a']}"
    except:
        pass
    return "Stay positive and keep going!"

@tasks.loop(hours=2)
async def send_quotes():
    if quiet_until and datetime.utcnow() < quiet_until:
        return
    now = datetime.now()
    if 8 <= now.hour <= 20:
        channel = bot.get_channel(CHANNEL_ID)
        await channel.send(get_quote())

# --- Weather monitor every 5 minutes ---
@tasks.loop(minutes=5)
async def weather_monitor():
    if quiet_until and datetime.utcnow() < quiet_until:
        return
    now = datetime.utcnow()
    expired = [aid for aid, end in posted_alerts.items() if end < now]
    for aid in expired:
        del posted_alerts[aid]

    channel = bot.get_channel(CHANNEL_ID)
    zip_groups = {}
    for user_id, info in user_zips.items():
        zip_groups.setdefault(info["zip"], {"lat": info["lat"], "lon": info["lon"], "users":[]})
        zip_groups[info["zip"]]["users"].append(user_id)
    
    for zip_code, data in zip_groups.items():
        new_alerts = check_weather_alert(data["lat"], data["lon"])
        if new_alerts:
            mentions = " ".join([f"<@{uid}>" for uid in data["users"]])
            for alert_id, msg, end_time in new_alerts:
                if alert_id not in posted_alerts:
                    posted_alerts[alert_id] = end_time
                    await channel.send(f"📍 ZIP `{zip_code}` {mentions}\n{msg}")

# --- Welcome new members ---
@bot.event
async def on_member_join(member):
    channel = member.guild.system_channel
    if channel:
        await channel.send(f"👋 Welcome {member.mention} to the server!")

# --- Bot ready ---
@bot.event
async def on_ready():
    print(f"{bot.user.name} is online!")
    channel = bot.get_channel(CHANNEL_ID)
    if channel:
        await channel.send("I'm Here!")
    send_quotes.start()
    weather_monitor.start()

# --- Flask server to bind port ---
app = Flask('')

@app.route('/')
def home():
    return "Lumina Bot is running!"

def run_flask():
    app.run(host='0.0.0.0', port=6969)

threading.Thread(target=run_flask).start()

# --- Run bot ---
bot.run(TOKEN)
