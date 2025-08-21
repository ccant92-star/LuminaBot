import discord
from discord.ext import tasks, commands
import requests
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from timezonefinder import TimezoneFinder
import random
import os
import json
import feedparser

TOKEN = os.getenv("TOKEN")
CHANNEL_ID = int(os.getenv("CHANNEL_ID"))

if not TOKEN or not CHANNEL_ID:
    raise ValueError("TOKEN and CHANNEL_ID must be set in env")

intents = discord.Intents.default()
intents.message_content = True
intents.members = True  # Required for welcome messages
bot = commands.Bot(command_prefix="!", intents=intents)

# --- Globals ---
user_zips = {}            # user_id: {zip, lat, lon}
posted_alerts = {}        # alert_id: end_datetime
daily_sales = {}          # user_id: sales count
weekly_sales = {}         # user_id: sales count
user_emoji = {}           # user_id: emoji
sandbox_mode = False
sandbox_channel_id = None
quiet_until = None        # datetime until bot is quiet
tf = TimezoneFinder()
NOAA_RSS_URL = "https://alerts.weather.gov/cap/us.php?x=1"

DATA_FILE = "lumina_data.json"

# --- Load persisted data ---
if os.path.exists(DATA_FILE):
    with open(DATA_FILE, "r") as f:
        data = json.load(f)
        user_zips = data.get("user_zips", {})
        daily_sales = data.get("daily_sales", {})
        weekly_sales = data.get("weekly_sales", {})
        user_emoji = data.get("user_emoji", {})

# --- Save data ---
def save_data():
    with open(DATA_FILE, "w") as f:
        json.dump({
            "user_zips": user_zips,
            "daily_sales": daily_sales,
            "weekly_sales": weekly_sales,
            "user_emoji": user_emoji
        }, f)

# --- Safety advice ---
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

# --- Event shorthand ---
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

# --- Zippopotam ZIP → lat/lon ---
def zip_to_coords(zip_code):
    try:
        res = requests.get(f"http://api.zippopotam.us/us/{zip_code}")
        if res.status_code == 200:
            data = res.json()
            return float(data["places"][0]["latitude"]), float(data["places"][0]["longitude"])
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

# --- Weather command ---
@bot.command()
async def weather(ctx, zip_code: str = None):
    uid = str(ctx.author.id)
    coords = None
    if zip_code:
        coords = zip_to_coords(zip_code)
        if coords:
            user_zips[uid] = {"zip": zip_code, "lat": coords[0], "lon": coords[1]}
            save_data()
            await ctx.send(f"✅ {ctx.author.mention}, ZIP {zip_code} registered.")
        else:
            await ctx.send("❌ Invalid ZIP code.")
            return
    elif uid in user_zips:
        zip_code = user_zips[uid]["zip"]
        coords = user_zips[uid]["lat"], user_zips[uid]["lon"]
    else:
        await ctx.send("❌ No ZIP code registered. Use `!weather [ZIP]` to register.")
        return

    # Show current alerts for ZIP
    alerts = []
    feed = feedparser.parse(NOAA_RSS_URL)
    for entry in feed.entries:
        # Basic check if alert affects the user's location (simplified)
        alerts.append(f"⚠️ {entry.title}: {entry.summary}")
    if alerts:
        await ctx.send("\n".join(alerts[:5]))  # limit to 5 alerts
    else:
        await ctx.send("✅ No active alerts for your area.")

# --- Sandbox toggle ---
@bot.command()
@commands.has_permissions(manage_guild=True)
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
        await ctx.send(f"❌ Unknown event code `{code}`.")
        return
    users = [uid for uid, info in user_zips.items() if info["zip"] == zip_code]
    if not users:
        await ctx.send(f"❌ No users registered for ZIP `{zip_code}`.")
        return
    mentions = " ".join([f"<@{uid}>" for uid in users])
    now_str = datetime.now().strftime("%Y-%m-%d %I:%M %p %Z")
    advice = SAFETY_ADVICE.get(event, "⚠️ Stay alert and follow local guidance.")
    msg = f"📍 ZIP `{zip_code}` {mentions}\n⚠️ **{event}**\n⏰ {now_str}\n\n👉 **Safety Advice:** {advice} [SANDBOX MODE]"
    channel = bot.get_channel(sandbox_channel_id)
    await channel.send(msg)

# --- Repsale commands ---
@bot.command()
async def repsale(ctx):
    today_str = datetime.now().strftime("%Y-%m-%d")
    channel = bot.get_channel(CHANNEL_ID)
    if not daily_sales:
        await ctx.send("No sales reported today.")
        return
    sorted_sales = sorted(daily_sales.items(), key=lambda x: x[1], reverse=True)
    msg = f"📊 Daily Sales Leaderboard ({today_str}):\n"
    for i, (uid, count) in enumerate(sorted_sales, 1):
        emoji = user_emoji.get(uid, "🛒")
        member = ctx.guild.get_member(int(uid))
        name = member.display_name if member else uid
        msg += f"{i}. {name} {emoji * count}\n"
    await channel.send(msg)

@bot.command()
async def setemoji(ctx, emoji: str):
    user_emoji[str(ctx.author.id)] = emoji
    save_data()
    await ctx.send(f"✅ {ctx.author.mention}, your sales emoji is now {emoji}")

@bot.command()
@commands.has_permissions(manage_guild=True)
async def resetleaderboard(ctx, scope: str):
    global daily_sales, weekly_sales
    if scope.lower() == "daily":
        daily_sales = {}
    elif scope.lower() == "weekly":
        weekly_sales = {}
    else:
        await ctx.send("Usage: `!resetleaderboard [daily|weekly]`")
        return
    save_data()
    await ctx.send(f"✅ {scope.capitalize()} leaderboard reset.")

@bot.command()
async def weeklyleaderboard(ctx):
    today_str = datetime.now().strftime("%Y-%m-%d")
    channel = bot.get_channel(CHANNEL_ID)
    if not weekly_sales:
        await ctx.send("No sales reported this week.")
        return
    sorted_sales = sorted(weekly_sales.items(), key=lambda x: x[1], reverse=True)
    msg = f"📊 Weekly Sales Leaderboard ({today_str}):\n"
    for i, (uid, count) in enumerate(sorted_sales, 1):
        emoji = user_emoji.get(uid, "🛒")
        member = ctx.guild.get_member(int(uid))
        name = member.display_name if member else uid
        msg += f"{i}. {name} {emoji * count}\n"
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

@tasks.loop(minutes=120)
async def send_quotes():
    now = datetime.now()
    if 8 <= now.hour <= 20:
        if quiet_until and datetime.now() < quiet_until:
            return
        channel = bot.get_channel(CHANNEL_ID)
        await channel.send(get_quote())

# --- Quiet mode ---
@bot.command()
@commands.has_permissions(manage_guild=True)
async def quiet(ctx, minutes: int = 30):
    global quiet_until
    quiet_until = datetime.now() + timedelta(minutes=minutes)
    await ctx.send(f"🤫 Bot will be quiet for {minutes} minutes.")

# --- Weather monitor (RSS) ---
@tasks.loop(minutes=2)
async def weather_monitor():
    now = datetime.utcnow()
    expired = [aid for aid, end in posted_alerts.items() if end < now]
    for aid in expired:
        del posted_alerts[aid]

    if quiet_until and datetime.now() < quiet_until:
        return

    channel = bot.get_channel(sandbox_channel_id if sandbox_mode else CHANNEL_ID)

    feed = feedparser.parse(NOAA_RSS_URL)
    zip_groups = {}
    for uid, info in user_zips.items():
        if info["zip"] not in zip_groups:
            zip_groups[info["zip"]] = {"lat": info["lat"], "lon": info["lon"], "users": []}
        zip_groups[info["zip"]]["users"].append(uid)

    for entry in feed.entries:
        alert_id = entry.id
        title = entry.title
        summary = entry.summary
        try:
            end_time = datetime.strptime(entry.updated, "%Y-%m-%dT%H:%M:%S%z").astimezone(tz=None)
        except:
            end_time = now + timedelta(hours=1)

        if alert_id not in posted_alerts or posted_alerts[alert_id] < now:
            posted_alerts[alert_id] = end_time
            advice = SAFETY_ADVICE.get(title, "⚠️ Stay alert and follow local guidance.")
            sandbox_tag = " [SANDBOX MODE]" if sandbox_mode else ""
            mentions_list = []
            for zip_code, data in zip_groups.items():
                mentions = " ".join([f"<@{uid}>" for uid in data["users"]])
                mentions_list.append(f"📍 ZIP `{zip_code}` {mentions}")
            mentions_str = "\n".join(mentions_list) if mentions_list else "No registered users."
            await channel.send(f"⚠️ **{title}**{sandbox_tag}\n{summary}\n\n👉 **Safety Advice:** {advice}\n\n{mentions_str}")

# --- Monday EOD reminders ---
@tasks.loop(hours=24)
async def monday_reminder():
    now = datetime.now()
    if now.weekday() == 0 and (not quiet_until or datetime.now() > quiet_until):
        channel = bot.get_channel(CHANNEL_ID)
        await channel.send("📌 Reminder: Inventory is due by 12 PM today!")

# --- Daily leaderboard reset at 8 AM Central ---
@tasks.loop(minutes=60)
async def daily_reset():
    now = datetime.now(ZoneInfo("America/Chicago"))
    if now.hour == 8 and now.minute < 60:
        global daily_sales
        daily_sales = {}
        save_data()
        channel = bot.get_channel(CHANNEL_ID)
        await channel.send("🔄 Daily sales leaderboard reset.")

# --- Weekly leaderboard reset every Sunday 8 AM Central ---
@tasks.loop(minutes=60)
async def weekly_reset():
    now = datetime.now(ZoneInfo("America/Chicago"))
    if now.weekday() == 6 and now.hour == 8:
        global weekly_sales
        weekly_sales = {}
        save_data()
        channel = bot.get_channel(CHANNEL_ID)
        await channel.send("🔄 Weekly sales leaderboard reset.")

# --- Welcome new members ---
@bot.event
async def on_member_join(member):
    channel = member.guild.system_channel
    if channel:
        await channel.send(f"👋 Welcome {member.mention}!")

# --- Bot ready ---
@bot.event
async def on_ready():
    print(f"{bot.user.name} is online!")
    channel = bot.get_channel(CHANNEL_ID)
    if channel:
        await channel.send("👋 I'm Here!")
    send_quotes.start()
    weather_monitor.start()
    monday_reminder.start()
    daily_reset.start()
    weekly_reset.start()

bot.run(TOKEN)
