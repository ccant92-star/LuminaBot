import discord
from discord.ext import tasks, commands
import requests
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from timezonefinder import TimezoneFinder
import random
import os
import json
from asyncio import sleep

# ---------------- CONFIG ----------------
TOKEN = os.getenv("TOKEN")
CHANNEL_ID = int(os.getenv("CHANNEL_ID"))       # General bot posts
SALES_CHANNEL_ID = int(os.getenv("SALES_CHANNEL_ID"))  # Sales leaderboard channel
CENTRAL = ZoneInfo("America/Chicago")

if not TOKEN or not CHANNEL_ID or not SALES_CHANNEL_ID:
    raise ValueError("TOKEN, CHANNEL_ID, and SALES_CHANNEL_ID must be set in env")

intents = discord.Intents.default()
intents.message_content = True
intents.members = True
bot = commands.Bot(command_prefix="!", intents=intents)

# ---------------- GLOBALS ----------------
user_zips = {}            # user_id: {zip, lat, lon}
rep_sales = {}            # user_id: {"count": int, "emoji": str}
weekly_sales = {}         # user_id: cumulative weekly
posted_alerts = {}        # alert_id: end_datetime
sandbox_mode = False
sandbox_channel_id = None
quiet_mode = False
quiet_until = None
tf = TimezoneFinder()

ZIP_FILE = "user_zips.json"
SALES_FILE = "rep_sales.json"
WEEKLY_FILE = "weekly_sales.json"

# ---------------- UTILITIES ----------------
def save_json(file, data):
    with open(file, "w") as f:
        json.dump(data, f)

def load_json(file):
    try:
        with open(file, "r") as f:
            return json.load(f)
    except:
        return {}

user_zips = load_json(ZIP_FILE)
rep_sales = load_json(SALES_FILE)
weekly_sales = load_json(WEEKLY_FILE)

async def send_message(channel, msg):
    if quiet_mode:
        return
    await channel.send(msg)

# ---------------- SAFETY ADVICE ----------------
SAFETY_ADVICE = {
    "Tornado Warning": "🌪️ Take shelter immediately in a basement or interior room on the lowest floor.",
    "Severe Thunderstorm Warning": "⛈️ Stay indoors, avoid windows, unplug electronics.",
    "Flash Flood Warning": "🌊 Move to higher ground immediately.",
    "Heat Advisory": "🥵 Stay hydrated, avoid strenuous activity.",
    "Winter Storm Warning": "❄️ Stay off roads if possible, keep warm.",
    "High Wind Warning": "💨 Secure loose objects outdoors.",
    "Excessive Heat Warning": "🔥 Stay indoors in AC if possible.",
    "Hurricane Warning": "🌀 Follow evacuation orders.",
    "Tropical Storm Warning": "🌧️ Prepare for flooding and strong winds.",
    "Wildfire Warning": "🔥 Be ready to evacuate if ordered.",
    "Dense Fog Advisory": "🌫️ Use low beams, drive slowly.",
    "Blizzard Warning": "❄️ Avoid travel, stay indoors, ensure supplies."
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

# ---------------- ZIP TO COORDS ----------------
def zip_to_coords(zip_code):
    try:
        url = f"http://api.zippopotam.us/us/{zip_code}"
        res = requests.get(url)
        if res.status_code == 200:
            data = res.json()
            lat = float(data["places"][0]["latitude"])
            lon = float(data["places"][0]["longitude"])
            return lat, lon
    except:
        return None
    return None

# ---------------- UTC → LOCAL ----------------
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

# ---------------- WEATHER ALERTS ----------------
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
                advice = SAFETY_ADVICE.get(event, "⚠️ Stay alert and follow local guidance.")
                start_local = to_local_time(props["effective"], lat, lon)
                end_local = to_local_time(props["ends"], lat, lon)
                new_alerts.append(
                    (alert_id,
                     f"⚠️ **{event}**\n{props['headline']}\n⏰ {start_local} → {end_local}\n\n👉 **Safety Advice:** {advice}",
                     end_time)
                )
    return new_alerts

# ---------------- COMMANDS ----------------
@bot.command()
async def weather(ctx, zip_code: str = None):
    user_id = str(ctx.author.id)
    if zip_code is None:
        if user_id in user_zips:
            zip_code = user_zips[user_id]["zip"]
        else:
            await ctx.send("❌ Please provide a ZIP code first, e.g., `!weather 12345`")
            return
    coords = zip_to_coords(zip_code)
    if coords:
        lat, lon = coords
        user_zips[user_id] = {"zip": zip_code, "lat": lat, "lon": lon}
        save_json(ZIP_FILE, user_zips)
        alerts = check_weather_alert(lat, lon)
        msg = f"✅ {ctx.author.mention}, current alerts for ZIP {zip_code}:\n"
        if alerts:
            for _, alert_msg, _ in alerts:
                if sandbox_mode:
                    alert_msg = "[SANDBOX MODE] " + alert_msg
                msg += alert_msg + "\n"
        else:
            msg += "No active alerts."
        await ctx.send(msg)
    else:
        await ctx.send("❌ Could not find that ZIP code.")

@bot.command()
async def repsale(ctx):
    user_id = str(ctx.author.id)
    if user_id not in rep_sales:
        rep_sales[user_id] = {"count": 0, "emoji": "🛒"}
        save_json(SALES_FILE, rep_sales)
    await post_daily_leaderboard()

@bot.command()
async def setemoji(ctx, emoji: str):
    user_id = str(ctx.author.id)
    if user_id not in rep_sales:
        rep_sales[user_id] = {"count": 0, "emoji": emoji}
    else:
        rep_sales[user_id]["emoji"] = emoji
    save_json(SALES_FILE, rep_sales)
    await ctx.send(f"✅ {ctx.author.mention}, your sales emoji is now {emoji}")

@bot.command()
@commands.has_permissions(manage_guild=True)
async def quiet(ctx, minutes: int = 30):
    global quiet_mode, quiet_until
    quiet_mode = True
    quiet_until = datetime.now(CENTRAL) + timedelta(minutes=minutes)
    await ctx.send(f"🤫 Lumina will be quiet for {minutes} minutes.")
    await sleep(minutes * 60)
    quiet_mode = False
    quiet_until = None
    await ctx.send("✅ Quiet period over. Lumina is active again.")

@bot.command()
@commands.has_permissions(manage_guild=True)
async def sandbox(ctx, code: int = None):
    global sandbox_mode, sandbox_channel_id
    if code != 8647:
        await ctx.send("❌ Invalid sandbox code.")
        return
    sandbox_mode = not sandbox_mode
    sandbox_channel_id = ctx.channel.id if sandbox_mode else None
    await ctx.send(f"🧪 Sandbox mode {'enabled' if sandbox_mode else 'disabled'}.")

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
    start_local = datetime.now().strftime("%Y-%m-%d %I:%M %p %Z")
    end_local = (datetime.now() + timedelta(hours=1)).strftime("%Y-%m-%d %I:%M %p %Z")
    advice = SAFETY_ADVICE.get(event, "⚠️ Stay alert and follow local guidance.")
    msg = f"[SANDBOX MODE]\n📍 ZIP `{zip_code}` {mentions}\n⚠️ **{event}**\n⏰ {start_local} → {end_local}\n\n👉 **Safety Advice:** {advice}"
    channel = bot.get_channel(sandbox_channel_id if sandbox_mode else CHANNEL_ID)
    await send_message(channel, msg)

@bot.command()
@commands.has_permissions(manage_guild=True)
async def resetleaderboard(ctx, scope: str = "daily"):
    global rep_sales, weekly_sales
    if scope.lower() == "daily":
        for uid in rep_sales:
            rep_sales[uid]["count"] = 0
        save_json(SALES_FILE, rep_sales)
        await ctx.send("✅ Daily leaderboard reset.")
    elif scope.lower() == "weekly":
        for uid in weekly_sales:
            weekly_sales[uid] = 0
        save_json(WEEKLY_FILE, weekly_sales)
        await ctx.send("✅ Weekly leaderboard reset.")
    else:
        await ctx.send("❌ Invalid scope. Use `daily` or `weekly`.")

@bot.command()
async def weeklyleaderboard(ctx):
    if not weekly_sales:
        await ctx.send("📊 Weekly leaderboard: no sales yet this week.")
        return
    sorted_sales = sorted(weekly_sales.items(), key=lambda x: x[1], reverse=True)
    lines = []
    for i, (user_id, count) in enumerate(sorted_sales, start=1):
        member = ctx.guild.get_member(int(user_id))
        if member:
            emoji = rep_sales.get(user_id, {}).get("emoji", "🛒")
            lines.append(f"{i}. {member.mention} {emoji} ({count})")
    now = datetime.now(CENTRAL)
    start_of_week = (now - timedelta(days=now.weekday())).strftime("%Y-%m-%d")
    today_str = now.strftime("%Y-%m-%d")
    msg = f"📊 **Weekly Sales Leaderboard — {start_of_week} → {today_str}**\n" + "\n".join(lines)
    channel = bot.get_channel(SALES_CHANNEL_ID)
    await send_message(channel, msg)

# ---------------- TASKS ----------------
@tasks.loop(minutes=60)
async def weather_monitor():
    now = datetime.utcnow()
    expired = [aid for aid, end in posted_alerts.items() if end < now]
    for aid in expired:
        del posted_alerts[aid]

    channel = bot.get_channel(CHANNEL_ID)
    zip_groups = {}
    for user_id, info in user_zips.items():
        if info["zip"] not in zip_groups:
            zip_groups[info["zip"]] = {"lat": info["lat"], "lon": info["lon"], "users": []}
        zip_groups[info["zip"]]["users"].append(user_id)

    for zip_code, data in zip_groups.items():
        new_alerts = check_weather_alert(data["lat"], data["lon"])
        for alert_id, msg, end_time in new_alerts:
            if alert_id not in posted_alerts:
                posted_alerts[alert_id] = end_time
                mentions = " ".join([f"<@{uid}>" for uid in data["users"]])
                msg = f"[SANDBOX MODE] {msg}" if sandbox_mode else msg
                await send_message(channel, f"📍 ZIP `{zip_code}` {mentions}\n{msg}")

@tasks.loop(hours=24)
async def daily_reset():
    now = datetime.now(CENTRAL)
    if now.hour == 8:
        for uid in rep_sales:
            rep_sales[uid]["count"] = 0
        save_json(SALES_FILE, rep_sales)
        await post_daily_leaderboard()
        await post_weekly_leaderboard()
    if now.hour == 23:
        await post_daily_leaderboard()

async def post_daily_leaderboard():
    if not rep_sales or quiet_mode:
        return
    sorted_sales = sorted(rep_sales.items(), key=lambda x: x[1]["count"], reverse=True)
    lines = []
    for i, (user_id, info) in enumerate(sorted_sales, start=1):
        member = bot.get_guild(bot.get_channel(SALES_CHANNEL_ID).guild.id).get_member(int(user_id))
        if member:
            lines.append(f"{i}. {member.mention} {info['emoji']} ({info['count']})")
    now_str = datetime.now(CENTRAL).strftime("%Y-%m-%d")
    msg = f"📊 **Daily Sales Leaderboard — {now_str}**\n" + "\n".join(lines)
    channel = bot.get_channel(SALES_CHANNEL_ID)
    await send_message(channel, msg)

async def post_weekly_leaderboard():
    if not weekly_sales or quiet_mode:
        return
    sorted_sales = sorted(weekly_sales.items(), key=lambda x: x[1], reverse=True)
    lines = []
    now = datetime.now(CENTRAL)
    start_of_week = (now - timedelta(days=now.weekday())).strftime("%Y-%m-%d")
    today_str = now.strftime("%Y-%m-%d")
    for i, (user_id, count) in enumerate(sorted_sales, start=1):
        member = bot.get_guild(bot.get_channel(SALES_CHANNEL_ID).guild.id).get_member(int(user_id))
        if member:
            emoji = rep_sales.get(user_id, {}).get("emoji", "🛒")
            lines.append(f"{i}. {member.mention} {emoji} ({count})")
    msg = f"📊 **Weekly Sales Leaderboard — {start_of_week} → {today_str}**\n" + "\n".join(lines)
    channel = bot.get_channel(SALES_CHANNEL_ID)
    await send_message(channel, msg)

# ---------------- QUOTES ----------------
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
    now = datetime.now(CENTRAL)
    if 8 <= now.hour <= 20 and not quiet_mode:
        channel = bot.get_channel(CHANNEL_ID)
        await send_message(channel, get_quote())

# ---------------- WELCOME ----------------
@bot.event
async def on_member_join(member):
    channel = bot.get_channel(CHANNEL_ID)
    await send_message(channel, f"👋 Welcome {member.mention} to the server!")

# ---------------- BOT READY ----------------
@bot.event
async def on_ready():
    print(f"{bot.user.name} is online!")
    
    # Send "I'm Here!" message
    channel = bot.get_channel(CHANNEL_ID)
    if channel:
        await channel.send("👋 I'm Here!")

    daily_reset.start()
    send_quotes.start()
    weather_monitor.start()

bot.run(TOKEN)
