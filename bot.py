import discord
from discord.ext import tasks, commands
import requests
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from timezonefinder import TimezoneFinder
import random
import os
import asyncio
from fastapi import FastAPI
import uvicorn

# --- Environment Variables ---
TOKEN = os.getenv("TOKEN")
CHANNEL_ID = int(os.getenv("CHANNEL_ID"))  # main Discord channel
SALES_CHANNEL_ID = int(os.getenv("SALES_CHANNEL_ID"))  # #sales-reporting

if not TOKEN or not CHANNEL_ID or not SALES_CHANNEL_ID:
    raise ValueError("TOKEN, CHANNEL_ID, and SALES_CHANNEL_ID must be set in env")

# --- Bot Setup ---
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)

# --- Globals ---
user_zips = {}          # user_id: {zip, lat, lon}
posted_alerts = {}      # alert_id: end_datetime
sandbox_mode = False
sandbox_channel_id = None
tf = TimezoneFinder()
bot_channel = None      # main channel object

rep_sales = {}          # user_id: number of sales
rep_emojis = {}         # user_id: emoji

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
    "Tornado Watch": "🌪️ Be alert! Conditions are favorable for tornadoes. Stay tuned to local weather updates.",
    "Severe Thunderstorm Watch": "⛈️ Conditions are favorable for severe thunderstorms. Monitor forecasts and be ready to take shelter.",
    "Flash Flood Watch": "🌊 Conditions are favorable for flash flooding. Stay alert and plan how to reach higher ground if needed."
}

# --- Event Shorthand ---
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

# --- Helper Functions ---
def zip_to_coords(zip_code):
    """Converts a US ZIP code into (latitude, longitude) using Zippopotam.us API."""
    try:
        res = requests.get(f"http://api.zippopotam.us/us/{zip_code}", timeout=5)
        if res.status_code == 200:
            data = res.json()
            place = data['places'][0]
            return float(place['latitude']), float(place['longitude'])
    except Exception as e:
        print(f"Error fetching coordinates for ZIP {zip_code}: {e}")
        return None
    return None

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

# --- ZenQuotes Integration ---
def get_quote():
    try:
        res = requests.get("https://zenquotes.io/api/random")
        if res.status_code == 200:
            data = res.json()
            if data and isinstance(data, list):
                q = data[0]
                return f"{q['q']} — {q['a']}"
    except Exception as e:
        print("Error fetching quote:", e)
    return "Stay positive and keep going!"

# --- Discord Commands ---
@bot.command()
async def weather(ctx, zip_code):
    coords = zip_to_coords(zip_code)
    if coords:
        user_zips[ctx.author.id] = {"zip": zip_code, "lat": coords[0], "lon": coords[1]}
        await ctx.send(f"✅ {ctx.author.mention}, your ZIP {zip_code} has been registered for weather alerts.")
    else:
        await ctx.send("❌ Could not find that ZIP code. Please try again.")

@bot.command()
async def sandbox(ctx, code: int):
    global sandbox_mode, sandbox_channel_id
    if code == 8647:
        sandbox_mode = not sandbox_mode
        if sandbox_mode:
            sandbox_channel_id = ctx.channel.id
            await ctx.send("🧪 Sandbox mode **enabled**. All messages will only appear here.")
        else:
            sandbox_channel_id = None
            await ctx.send("✅ Sandbox mode **disabled**. Normal operation resumed.")
    else:
        await ctx.send("❌ Invalid sandbox code.")

@bot.command()
async def alert(ctx, code: str = None, zip_code: str = None):
    if not sandbox_mode:
        await ctx.send("❌ This command only works in sandbox mode.")
        return
    if not code or not zip_code:
        await ctx.send("❌ Usage: `!alert [code] [zip]`")
        return
    event = EVENT_SHORTHAND.get(code.lower())
    if not event:
        await ctx.send(f"❌ Unknown event code `{code}`. Valid codes: {', '.join(EVENT_SHORTHAND.keys())}")
        return
    users = [uid for uid, info in user_zips.items() if info["zip"] == zip_code]
    if not users:
        await ctx.send(f"❌ No users registered for ZIP `{zip_code}`.")
        return
    mentions = " ".join([f"<@{uid}>" for uid in users])
    start_local = datetime.now().strftime("%Y-%m-%d %I:%M %p %Z")
    end_local = (datetime.now() + timedelta(hours=1)).strftime("%Y-%m-%d %I:%M %p %Z")
    advice = SAFETY_ADVICE.get(event, "⚠️ Stay alert and follow local emergency guidance.")
    msg = (
        f"📍 ZIP `{zip_code}` {mentions}\n"
        f"⚠️ **{event}**\n"
        f"⏰ {start_local} → {end_local}\n\n"
        f"👉 **Safety Advice:** {advice}"
    )
    target_channel = bot.get_channel(sandbox_channel_id) if sandbox_mode else bot_channel
    if target_channel:
        await target_channel.send(msg)

# --- Sales Commands ---
@bot.command()
async def setemoji(ctx, emoji: str):
    """Set your personal emoji to represent a sale."""
    rep_emojis[ctx.author.id] = emoji
    await ctx.send(f"✅ {ctx.author.mention}, your sale emoji is now {emoji}")

@bot.command()
async def addsale(ctx, count: int = 1):
    """Add one or more sales to your total."""
    rep_sales[ctx.author.id] = rep_sales.get(ctx.author.id, 0) + count
    await ctx.send(f"✅ {ctx.author.mention}, you now have {rep_sales[ctx.author.id]} sale(s).")

@bot.command()
async def repsale(ctx):
    """Post the current sales leaderboard in #sales-reporting automatically with current date."""
    if not rep_sales:
        await ctx.send("No sales recorded yet.")
        return

    sorted_reps = sorted(rep_sales.items(), key=lambda x: x[1], reverse=True)
    lines = []
    for i, (user_id, count) in enumerate(sorted_reps, start=1):
        user = await bot.fetch_user(user_id)
        emoji = rep_emojis.get(user_id, "🛒")
        lines.append(f"{i}. {user.mention} {' '.join([emoji]*count)}")

    leaderboard = "\n".join(lines)
    current_date = datetime.now().strftime("%Y-%m-%d")
    sales_channel = bot.get_channel(SALES_CHANNEL_ID)
    if sales_channel:
        await sales_channel.send(f"📊 **Sales Leaderboard — {current_date}**\n{leaderboard}")
        await ctx.send(f"✅ Sales leaderboard posted in {sales_channel.mention}")
    else:
        await ctx.send("❌ Could not find the sales channel.")

# --- Discord Tasks ---
@tasks.loop(hours=2)
async def send_quotes():
    """Post an inspirational quote every 2 hours between 8 AM and 8 PM."""
    now = datetime.now()
    if 8 <= now.hour <= 20:
        target_channel = bot.get_channel(sandbox_channel_id) if sandbox_mode else bot_channel
        if target_channel:
            await target_channel.send(get_quote())

@tasks.loop(minutes=60)
async def weather_monitor():
    now = datetime.utcnow()
    expired = [aid for aid, end in posted_alerts.items() if end < now]
    for aid in expired:
        del posted_alerts[aid]

    zip_groups = {}
    for user_id, info in user_zips.items():
        zip_groups.setdefault(info["zip"], {"lat": info["lat"], "lon": info["lon"], "users": []})["users"].append(user_id)

    alert_map = {}
    for zip_code, data in zip_groups.items():
        new_alerts = check_weather_alert(data["lat"], data["lon"])
        if new_alerts:
            mentions = " ".join([f"<@{uid}>" for uid in data["users"]])
            for alert_id, msg, end_time in new_alerts:
                if alert_id not in alert_map:
                    alert_map[alert_id] = {"msg": msg, "zips": [], "mentions": [], "end": end_time}
                alert_map[alert_id]["zips"].append(zip_code)
                alert_map[alert_id]["mentions"].append(mentions)

    for alert_id, details in alert_map.items():
        posted_alerts[alert_id] = details["end"]
        zips_str = ", ".join(details["zips"])
        mentions_str = " ".join(details["mentions"])
        target_channel = bot.get_channel(sandbox_channel_id) if sandbox_mode else bot_channel
        if target_channel:
            await target_channel.send(f"📍 ZIPs `{zips_str}` {mentions_str}\n{details['msg']}")

@tasks.loop(hours=24)
async def monday_reminder():
    now = datetime.now()
    if now.weekday() == 0:
        target_channel = bot.get_channel(sandbox_channel_id) if sandbox_mode else bot_channel
        if target_channel:
            await target_channel.send("📌 Reminder: Inventory is due by 12 PM today!")

# --- FastAPI Web Listener ---
app = FastAPI()

@app.get("/health")
async def health():
    return {"status": "ok"}

@app.post("/trigger-alert")
async def trigger_alert(data: dict):
    zip_code = data.get("zip")
    code = data.get("event_code")
    if zip_code and code:
        event = EVENT_SHORTHAND.get(code.lower())
        if event:
            users = [uid for uid, info in user_zips.items() if info["zip"] == zip_code]
            mentions = " ".join([f"<@{uid}>" for uid in users])
            msg = f"Webhook Trigger:\n📍 ZIP `{zip_code}` {mentions}\n⚠️ **{event}**"
            target_channel = bot.get_channel(sandbox_channel_id) if sandbox_mode else bot_channel
            if target_channel:
                await target_channel.send(msg)
            return {"status": "alert sent"}
    return {"status": "failed"}

# --- Bot Ready ---
@bot.event
async def on_ready():
    global bot_channel
    bot_channel = bot.get_channel(CHANNEL_ID)

    if bot_channel:
        print(f"✅ Found channel: {bot_channel.name}")
    else:
        print(f"❌ Could not find channel with ID {CHANNEL_ID}")

    print(f"{bot.user.name} is online!")

    # Start tasks safely
    if not send_quotes.is_running():
        send_quotes.start()
    if not monday_reminder.is_running():
        monday_reminder.start()
    if not weather_monitor.is_running():
        weather_monitor.start()

# --- Run Bot + FastAPI ---
async def main():
    config = uvicorn.Config(app, host="0.0.0.0", port=8000, log_level="info")
    server = uvicorn.Server(config)
    asyncio.create_task(server.serve())
    await bot.start(TOKEN)

asyncio.run(main())
