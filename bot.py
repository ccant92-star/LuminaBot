import os
import threading
from discord.ext import commands, tasks
from flask import Flask
from cogs import inventory, sales, weather, quotes, events

# Load env variables
# from dotenv import load_dotenv
# load_dotenv("config.env")

TOKEN = os.getenv("TOKEN")
CHANNEL_ID = int(os.getenv("CHANNEL_ID"))

intents = discord.Intents.default()
intents.message_content = True
intents.members = True

bot = commands.Bot(command_prefix="!", intents=intents)

# Register cogs
bot.add_cog(inventory.InventoryCog(bot))
bot.add_cog(sales.SalesCog(bot))
bot.add_cog(weather.WeatherCog(bot))
bot.add_cog(quotes.QuotesCog(bot))
bot.add_cog(events.EventsCog(bot))

# Flask server
app = Flask("lumina_bot")
@app.route("/")
def home():
    return "Lumina Bot is running!"

def run_flask():
    app.run(host="0.0.0.0", port=6969)

threading.Thread(target=run_flask, daemon=True).start()

# Start bot
@bot.event
async def on_ready():
    print(f"{bot.user.name} is online!")
    channel = bot.get_channel(CHANNEL_ID)
    if channel:
        await channel.send("I'm Here!")
    # Start quote task
    quotes.send_quotes.start()

bot.run(TOKEN)
