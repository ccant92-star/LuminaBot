import discord
from discord.ext import commands
from datetime import datetime
import json
import os

DATA_FILE = "lumina_data.json"

def load_data():
    if os.path.exists(DATA_FILE):
        with open(DATA_FILE, "r") as f:
            return json.load(f)
    return {"sales_data": {}}

def save_data(data):
    with open(DATA_FILE, "w") as f:
        json.dump(data, f)

class Sales(commands.Cog):
    def __init__(self, bot, channel_id):
        self.bot = bot
        self.channel_id = channel_id
        self.data = load_data()
    
    @commands.command()
    async def repsale(self, ctx):
        sales_data = self.data.get("sales_data", {})
        if not sales_data:
            await ctx.send("❌ No sales reported today.")
            return
        sorted_sales = sorted(sales_data.items(), key=lambda x: x[1].get("daily",0), reverse=True)
        today = datetime.now().strftime("%Y-%m-%d")
        msg = f"📊 **Daily Sales Leaderboard – {today}**\n"
        for idx, (uid, data) in enumerate(sorted_sales, start=1):
            emoji = data.get("emoji","🛒")
            count = data.get("daily",0)
            msg += f"{idx}. <@{uid}> {emoji} x{count}\n"
        channel = self.bot.get_channel(self.channel_id)
        await channel.send(msg)

def setup(bot):
    bot.add_cog(Sales(bot, int(os.getenv("CHANNEL_ID"))))
