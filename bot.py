import os
from dotenv import load_dotenv

load_dotenv()
TOKEN = os.getenv("TOKEN")

import discord
from discord.ext import tasks, commands
# Lumina bot code goes here (full code can be inserted)
bot = commands.Bot(command_prefix="!", intents=discord.Intents.default())

@bot.event
async def on_ready():
    print(f"{bot.user.name} is online!")

bot.run(TOKEN)
