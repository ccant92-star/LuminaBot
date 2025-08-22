import os
import discord
from discord.ext import commands, tasks
import requests
from datetime import datetime, timedelta
from dateutil import tz
from timezonefinder import TimezoneFinder
from flask import Flask
import threading

# ---- DISCORD SETUP ----
intents = discord.Intents.default()
intents.message_content = True
intents.members = True  # Needed for welcomes
bot = commands.Bot(command_prefix="!", intents=intents)

# ---- DATA STORAGE ----
sales = {}        # {user_id: count}
user_emojis = {}  # {user_id: emoji}
quiet_until = None
sandbox_mode = False

# ---- FLASK KEEPALIVE ----
app = Flask(__name__)

@app.route("/")
def home():
    return "Lumina is alive!"

def run_flask():
    app.run(host="0.0.0.0", port=6969)

threading.Thread(target=run_flask).start()

# ---- HELPERS ----
def get_quote():
    try:
        res = requests.get("https://zenquotes.io/api/random")
        data = res.json()
        return f"💡 {data[0]['q']} — *{data[0]['a']}*"
    except Exception:
        return "✨ Keep pushing forward, you’ve got this!"

async def post_leaderboard(guild):
    channel = discord.utils.get(guild.text_channels, name="sales-reporting")
    if not channel:
        return

    if not sales:
        await channel.send("📊 No sales reported yet today.")
        return

    sorted_sales = sorted(sales.items(), key=lambda x: x[1], reverse=True)
    leaderboard = []
    for i, (user_id, count) in enumerate(sorted_sales, 1):
        user = guild.get_member(user_id)
        emoji = user_emojis.get(user_id, "🦩")
        leaderboard.append(f"{i}. {user.mention} {' '.join([emoji] * count)}")

    now = datetime.now().strftime("%Y-%m-%d")
    message = f"📊 **Sales Leaderboard - {now}** 📊\n\n" + "\n".join(leaderboard)
    await channel.send(message)

# ---- EVENTS ----
@bot.event
async def on_ready():
    print(f"{bot.user} is online!")
    for guild in bot.guilds:
        default_channel = discord.utils.get(guild.text_channels, name="general")
        if default_channel:
            await default_channel.send("✨ I’m Here!")
    send_quotes.start()

@bot.event
async def on_member_join(member):
    channel = discord.utils.get(member.guild.text_channels, name="general")
    if channel:
        await channel.send(f"👋 Welcome to the server, {member.mention}!")

# ---- TASKS ----
@tasks.loop(hours=2)
async def send_quotes():
    quote = get_quote()
    for guild in bot.guilds:
        channel = discord.utils.get(guild.text_channels, name="general")
        if channel:
            await channel.send(quote if not sandbox_mode else f"⚠️ [SANDBOX] {quote}")

# ---- COMMANDS ----
@bot.command()
async def weather(ctx, zip_code: str = None):
    if quiet_until and datetime.now() < quiet_until:
        return

    if not zip_code:
        await ctx.send("🌦 Please provide a ZIP code, e.g., `!weather 90210`")
        return

    try:
        res = requests.get(f"https://api.zippopotam.us/us/{zip_code}")
        data = res.json()
        lat = data["places"][0]["latitude"]
        lon = data["places"][0]["longitude"]

        weather = requests.get(
            f"https://api.open-meteo.com/v1/forecast?latitude={lat}&longitude={lon}&current=temperature,weathercode"
        ).json()
        temp = weather["current"]["temperature"]
        await ctx.send(f"🌡 Weather at {zip_code}: {temp}°C")
    except Exception:
        await ctx.send("⚠️ Could not fetch weather for that ZIP code.")

@bot.command()
async def repsale(ctx):
    if quiet_until and datetime.now() < quiet_until:
        return

    user_id = ctx.author.id
    sales[user_id] = sales.get(user_id, 0) + 1
    await ctx.send(f"{ctx.author.mention}, your sale has been recorded! ✅")
    await post_leaderboard(ctx.guild)

@bot.command()
async def leaderboard(ctx):
    await post_leaderboard(ctx.guild)

@bot.command()
async def setemoji(ctx, emoji: str):
    user_emojis[ctx.author.id] = emoji
    await ctx.send(f"{ctx.author.mention}, your emoji has been set to {emoji}")

@bot.command()
async def quiet(ctx):
    global quiet_until
    if not ctx.author.guild_permissions.manage_messages:
        await ctx.send("🚫 You don’t have permission to use this command.")
        return
    quiet_until = datetime.now() + timedelta(minutes=30)
    await ctx.send("🔇 Lumina will be quiet for 30 minutes.")

@bot.command()
async def sandbox(ctx, mode: str):
    global sandbox_mode
    if not ctx.author.guild_permissions.manage_guild:
        await ctx.send("🚫 You don’t have permission to toggle sandbox.")
        return
    sandbox_mode = (mode.lower() == "on")
    await ctx.send(f"⚠️ Sandbox mode is now {'ON' if sandbox_mode else 'OFF'}.")

# ---- RUN BOT ----
bot.run(os.getenv("DISCORD_TOKEN"))
