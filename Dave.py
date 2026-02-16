import discord
import asyncio
from discord.ext import commands
from discord.ui import Button, View, Select
from google import genai

# =============================
# CONFIG
# =============================
import os

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

TICKET_PANEL_CHANNEL_ID = 1471699823043543103
TICKET_CATEGORY_ID = 1471692492608110773
STAFF_ROLE_ID = 1471690234109497404
TICKET_LOG_CHANNEL_ID = 1472805827738865715
STEWARD_ROLE_ID = 1471995325857402951

# =============================
# AI SETUP (GEMINI)
# =============================
client = genai.Client(api_key=GEMINI_API_KEY)

def load_rules():
    try:
        with open("trl_rules.txt", "r", encoding="utf-8") as f:
            return f.read()
    except:
        return "No rules file found."

RULES_TEXT = load_rules()

def get_dave_system_prompt(ticket_type="General"):
    return f"""
You are Dave, the official TRL Ticket Assistant for Total Racing League.

Your role:
- Help drivers with league questions.
- Acknowledge feedback and suggestions.
- Assist with incident reports and general tickets.
- You can also answer normal questions if users ask them.

Personality:
- Friendly, helpful, and slightly fun.
- Knowledgeable about racing and league rules.
- Calm, neutral, and fair.
- Never argue or insult users.
- Keep responses short and clear.
- Use light racing humour occasionally.

Behavior rules:

In feedback tickets:
- Thank the user for the suggestion.
- Confirm it will be passed to staff.
- Ask 1–2 short follow-up questions.

In incident or report tickets:
- Keep it simple and racing related.
- Guide the user to provide evidence if missing.

In general tickets:
- Answer the question normally.

League rules reference:
{RULES_TEXT}
"""

def ask_dave(user_message, ticket_type="General"):
    try:
        system_prompt = get_dave_system_prompt(ticket_type)
        response = client.models.generate_content(
            model="gemini-2.0-flash",
            contents=f"{system_prompt}\n\nUser: {user_message}"
        )
        return response.text
    except Exception as e:
        print("AI error:", e)
        return "Sorry, I’m having trouble responding right now."

# =============================
# DISCORD SETUP
# =============================
intents = discord.Intents.default()
intents.message_content = True
intents.members = True
intents.guilds = True

bot = commands.Bot(command_prefix="!", intents=intents)

ticket_timers = {}
ticket_messages = {}
muted_tickets = set()
ticket_types = {}

# =============================
# COLOUR SYSTEM
# =============================
def get_ticket_colour(ticket_type):
    if ticket_type == "Incident":
        return discord.Color.red()
    elif ticket_type == "Report":
        return discord.Color.gold()
    elif ticket_type == "Feedback":
        return discord.Color.green()
    else:
        return discord.Color.blurple()  # General

# =============================
# SUMMARY SYSTEM
# =============================
async def get_ticket_messages(channel):
    messages = []
    async for msg in channel.history(limit=100, oldest_first=True):
        if not msg.author.bot:
            messages.append(f"{msg.author.display_name}: {msg.content}")
    return "\n".join(messages)

def get_summary_prompt(ticket_type, transcript):
    if ticket_type == "Incident":
        style = "Write a short steward-style incident report."
    elif ticket_type == "Report":
        style = "Write a short moderation alert."
    elif ticket_type == "Feedback":
        style = "Summarise this feedback suggestion."
    else:
        style = "Summarise the question and answer."

    return f"""
You are Dave, TRL Ticket Assistant.

{style}

Keep it short and professional.

Transcript:
{transcript}
"""

def generate_summary(ticket_type, transcript):
    try:
        prompt = get_summary_prompt(ticket_type, transcript)
        response = client.models.generate_content(
            model="gemini-2.0-flash",
            contents=prompt
        )
        return response.text
    except Exception as e:
        print("Summary error:", e)
        return "Summary could not be generated."

async def log_ticket_summary(channel):
    log_channel = channel.guild.get_channel(TICKET_LOG_CHANNEL_ID)
    if not log_channel:
        return

    ticket_type = "General"
    if channel.topic and "-" in channel.topic:
        ticket_type = channel.topic.split("-")[1]

    transcript = await get_ticket_messages(channel)
    summary = generate_summary(ticket_type, transcript)

    embed = discord.Embed(
        title=f"{ticket_type} Ticket Summary",
        description=summary,
        color=get_ticket_colour(ticket_type)
    )

    embed.add_field(
        name="Channel",
        value=channel.name,
        inline=False
    )

    await log_channel.send(embed=embed)

# =============================
# UI ELEMENTS
# =============================
class TicketView(View):
    def __init__(self):
        super().__init__(timeout=None)
        self.add_item(TicketSelect())

class TicketPanel(View):
    def __init__(self):
        super().__init__(timeout=None)
        self.add_item(TicketSelect())

class CloseView(View):
    def __init__(self):
        super().__init__(timeout=None)
        self.add_item(ClaimButton())
        self.add_item(CloseButton())

class CloseButton(Button):
    def __init__(self):
        super().__init__(
            label="Close Ticket",
            style=discord.ButtonStyle.danger,
            custom_id="close_ticket"
        )

    async def callback(self, interaction: discord.Interaction):
        channel = interaction.channel

        await interaction.response.send_message(
            "📋 Dave is analysing the ticket...",
            ephemeral=True
        )

        await log_ticket_summary(channel)

        if channel.id in ticket_timers:
            ticket_timers[channel.id].cancel()
            del ticket_timers[channel.id]

        if channel.id in muted_tickets:
            muted_tickets.remove(channel.id)

        await channel.delete()

class ClaimButton(Button):
    def __init__(self):
        super().__init__(
            label="Claim Ticket",
            style=discord.ButtonStyle.primary,
            custom_id="claim_ticket"
        )

    async def callback(self, interaction: discord.Interaction):
        staff_role = interaction.guild.get_role(STAFF_ROLE_ID)

        if staff_role not in interaction.user.roles:
            await interaction.response.send_message(
                "You are not staff.", ephemeral=True
            )
            return

        muted_tickets.add(interaction.channel.id)

        embed = interaction.message.embeds[0]
        embed.add_field(
            name="Claimed By",
            value=interaction.user.mention,
            inline=False
        )

        await interaction.message.edit(embed=embed)
        await interaction.response.send_message(
            "Ticket claimed. Dave will stay quiet unless asked.",
            ephemeral=True
        )

# =============================
# TIMER SYSTEM
# =============================
async def start_timer(channel):
    try:
        total_seconds = 259200
        message = ticket_messages.get(channel.id)
        if not message:
            return

        while total_seconds > 0:
            await asyncio.sleep(60)
            total_seconds -= 60

        await log_ticket_summary(channel)
        await channel.delete()

    except:
        pass

def reset_timer(channel):
    if channel.id in ticket_timers:
        ticket_timers[channel.id].cancel()

    task = bot.loop.create_task(start_timer(channel))
    ticket_timers[channel.id] = task

# =============================
# TICKET SELECT
# =============================
class TicketSelect(Select):
    def __init__(self):
        options = [
            discord.SelectOption(label="General", emoji="🔹"),
            discord.SelectOption(label="Incident", emoji="🚨"),
            discord.SelectOption(label="Report", emoji="⚠️"),
            discord.SelectOption(label="Feedback", emoji="💡"),
        ]

        super().__init__(
            placeholder="Choose a ticket type...",
            min_values=1,
            max_values=1,
            options=options,
            custom_id="ticket_select"
        )

    async def callback(self, interaction: discord.Interaction):
        guild = interaction.guild
        user = interaction.user
        ticket_type = self.values[0]
        category = guild.get_channel(TICKET_CATEGORY_ID)

        await interaction.response.defer(ephemeral=True)

        channel_name = f"{ticket_type.lower()}-{user.display_name}".replace(" ", "-")
        staff_role = guild.get_role(STAFF_ROLE_ID)

        overwrites = {
            guild.default_role: discord.PermissionOverwrite(read_messages=False),
            user: discord.PermissionOverwrite(read_messages=True, send_messages=True),
            guild.me: discord.PermissionOverwrite(read_messages=True, send_messages=True),
        }

        if staff_role:
            overwrites[staff_role] = discord.PermissionOverwrite(
                read_messages=True,
                send_messages=True
            )

        ticket_channel = await guild.create_text_channel(
            name=channel_name,
            overwrites=overwrites,
            category=category,
            topic=f"{user.id}-{ticket_type}"
        )

        ticket_types[ticket_channel.id] = ticket_type

        embed = discord.Embed(
            title=f"{ticket_type} Ticket",
            description="Describe your issue and Dave will assist you.",
            color=get_ticket_colour(ticket_type)
        )

        embed.add_field(
            name="Auto-Close Timer",
            value="🕒 Closes in: **3d 0h 0m**",
            inline=False
        )

        ticket_message = await ticket_channel.send(
            content=user.mention,
            embed=embed,
            view=CloseView()
        )

        ticket_messages[ticket_channel.id] = ticket_message
        reset_timer(ticket_channel)

        await interaction.followup.send(
            f"Your ticket has been created: {ticket_channel.mention}",
            ephemeral=True
        )

        try:
            await interaction.message.edit(view=TicketPanel())
        except Exception as e:
            print("Panel reset error:", e)

# =============================
# PANEL COMMAND
# =============================
@bot.command()
@commands.has_permissions(administrator=True)
async def panel(ctx):
    embed = discord.Embed(
        title="🏁 Race Control Support",
        description="Select a ticket type below.",
        color=discord.Color.dark_blue()
    )

    await ctx.send(embed=embed, view=TicketView())

# =============================
# EVENTS
# =============================
@bot.event
async def on_ready():
    print(f"Logged in as {bot.user}")
    bot.add_view(TicketView())
    bot.add_view(CloseView())
    bot.add_view(TicketPanel())

@bot.event
async def on_message(message):
    await bot.process_commands(message)

    if message.author.bot:
        return

    if isinstance(message.channel, discord.TextChannel):
        if message.channel.category_id != TICKET_CATEGORY_ID:
            return
    else:
        return

    if message.channel.id in muted_tickets:
        return

    reset_timer(message.channel)

    ticket_type = ticket_types.get(message.channel.id, "General")
    reply = ask_dave(message.content, ticket_type)

    await message.channel.send(reply)

# =============================
# RUN BOT
# =============================
bot.run(DISCORD_TOKEN)
