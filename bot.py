import discord
from discord.ext import tasks, commands
import requests
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from timezonefinder import TimezoneFinder
import random

TOKEN = "YOUR_BOT_TOKEN"
CHANNEL_ID = 123456789012345678  # Shared alert channel

intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)

# --- Globals ---
user_zips = {}            # user_id: {zip, lat, lon}
posted_alerts = {}        # alert_id: end_datetime
sandbox_mode = False
sandbox_channel_id = None
tf = TimezoneFinder()

# --- Safety advice ---
SAFETY_ADVICE = {
    # Warnings
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

    # Watches
    "Tornado Watch": "🌪️ Be alert! Conditions are favorable for tornadoes. Stay tuned to local weather updates.",
    "Severe Thunderstorm Watch": "⛈️ Conditions are favorable for severe thunderstorms. Monitor forecasts and be ready to take shelter.",
    "Flash Flood Watch": "🌊 Conditions are favorable for flash flooding. Stay alert and plan how to reach higher ground if needed."
}

# --- Shorthand mapping ---
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

# --- ZIP → lat/lon ---
def zip_to_coords(zip_code):
    try:
        url = f"https://nominatim.openstreetmap.org/search?postalcode={zip_code}&country=US&format=json"
        res = requests.get(url, headers={"User-Agent": "LuminaBot"})
        data = res.json()
        if data:
            return float(data[0]["lat"]), float(data[0]["lon"])
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
            # parse end time
            try:
                end_time = datetime.fromisoformat(props["ends"].replace("Z", "+00:00"))
            except:
                end_time = now + timedelta(hours=1)
            # only post if not active in posted_alerts
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

# --- Register ZIP ---
@bot.command()
async def weather(ctx, zip_code):
    coords = zip_to_coords(zip_code)
    if coords:
        user_zips[ctx.author.id] = {"zip": zip_code, "lat": coords[0], "lon": coords[1]}
        await ctx.send(f"✅ {ctx.author.mention}, your ZIP {zip_code} has been registered for weather alerts.")
    else:
        await ctx.send("❌ Could not find that ZIP code. Please try again.")

# --- Sandbox toggle ---
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

# --- Sandbox alert ---
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
    channel = bot.get_channel(sandbox_channel_id if sandbox_mode else CHANNEL_ID)
    await channel.send(msg)

# --- Monitor alerts every hour ---
@tasks.loop(minutes=60)
async def weather_monitor():
    # Cleanup expired alerts
    now = datetime.utcnow()
    expired = [aid for aid, end in posted_alerts.items() if end < now]
    for aid in expired:
        del posted_alerts[aid]

    channel = bot.get_channel(sandbox_channel_id if sandbox_mode else CHANNEL_ID)
    zip_groups = {}
    for user_id, info in user_zips.items():
        if info["zip"] not in zip_groups:
            zip_groups[info["zip"]] = {"lat": info["lat"], "lon": info["lon"], "users": []}
        zip_groups[info["zip"]]["users"].append(user_id)
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
        await channel.send(f"📍 ZIPs `{zips_str}` {mentions_str}\n{details['msg']}")

# --- Quotes every 2 hours 8AM-8PM ---
def get_quote():
    try:
        res = requests.get("https://type.fit/api/quotes")
        if res.status_code == 200:
            q = random.choice(res.json())
            return f"{q['text']} — {q.get('author','Unknown')}"
    except:
        pass
    return "Stay positive and keep going!"

@tasks.loop(minutes=120)
async def send_quotes():
    now = datetime.now()
    if 8 <= now.hour <= 20:
        channel = bot.get_channel(sandbox_channel_id if sandbox_mode else CHANNEL_ID)
        await channel.send(get_quote())

# --- Monday inventory reminder ---
@tasks.loop(hours=24)
async def monday_reminder():
    now = datetime.now()
    if now.weekday() == 0:
        channel = bot.get_channel(sandbox_channel_id if sandbox_mode else CHANNEL_ID)
        await channel.send("📌 Reminder: Inventory is due by 12 PM today!")

# --- Bot ready ---
@bot.event
async def on_ready():
    print(f"{bot.user.name} is online!")
    send_quotes.start()
    monday_reminder.start()
    weather_monitor.start()

bot.run(TOKEN)
