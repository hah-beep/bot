import discord
from discord.ext import commands
import asyncio
import json
import os
from dotenv import load_dotenv

load_dotenv()

TOKEN = os.getenv('DISCORD_TOKEN')
PREFIX = os.getenv('PREFIX', '!')
CHANNELS_FILE = 'channels.json'

intents = discord.Intents.default()
intents.message_content = True
intents.voice_states = True

bot = commands.Bot(command_prefix=PREFIX, intents=intents)

_leaving = set()  # guild IDs where !leave was issued (suppress reconnect)


def load_channels() -> dict:
    if os.path.exists(CHANNELS_FILE):
        with open(CHANNELS_FILE, 'r') as f:
            return json.load(f)
    return {}


def save_channels(data: dict):
    with open(CHANNELS_FILE, 'w') as f:
        json.dump(data, f, indent=2)


home_channels: dict = load_channels()


async def connect_to_channel(channel: discord.VoiceChannel):
    guild = channel.guild
    if guild.voice_client:
        await guild.voice_client.move_to(channel)
    else:
        await channel.connect()


@bot.event
async def on_ready():
    print(f'Logged in as {bot.user} (ID: {bot.user.id})')
    print(f'Guilds: {[g.name for g in bot.guilds]}')
    for guild_id, channel_id in list(home_channels.items()):
        guild = bot.get_guild(int(guild_id))
        if not guild:
            continue
        channel = guild.get_channel(int(channel_id))
        if channel and not guild.voice_client:
            try:
                await channel.connect()
                print(f'Auto-joined #{channel.name} in {guild.name}')
            except Exception as e:
                print(f'Failed to auto-join in {guild.name}: {e}')


@bot.command(name='join')
async def join(ctx, *, channel_name: str = None):
    """Join a voice channel. Provide a name or run it from inside a channel."""
    if channel_name:
        channel = discord.utils.get(ctx.guild.voice_channels, name=channel_name)
        if not channel:
            await ctx.send(f'No voice channel named **{channel_name}** found.')
            return
    elif ctx.author.voice and ctx.author.voice.channel:
        channel = ctx.author.voice.channel
    else:
        await ctx.send('Join a voice channel first, or run `!join <channel name>`.')
        return

    try:
        await connect_to_channel(channel)
    except discord.ClientException as e:
        await ctx.send(f'Could not join: {e}')
        return

    home_channels[str(ctx.guild.id)] = str(channel.id)
    save_channels(home_channels)
    _leaving.discard(ctx.guild.id)
    await ctx.send(f'Joined and locked to **{channel.name}** — I will stay here and rejoin if disconnected.')


@bot.command(name='leave')
async def leave(ctx):
    """Leave the voice channel and stop auto-rejoining."""
    if not ctx.voice_client:
        await ctx.send("I'm not in a voice channel.")
        return

    _leaving.add(ctx.guild.id)
    home_channels.pop(str(ctx.guild.id), None)
    save_channels(home_channels)
    await ctx.voice_client.disconnect()
    await ctx.send('Left the voice channel.')


@bot.command(name='status')
async def status(ctx):
    """Show which voice channel the bot is in."""
    if ctx.voice_client and ctx.voice_client.channel:
        await ctx.send(f'Connected to **{ctx.voice_client.channel.name}**.')
    else:
        stored = home_channels.get(str(ctx.guild.id))
        if stored:
            channel = ctx.guild.get_channel(int(stored))
            name = channel.name if channel else f'ID {stored}'
            await ctx.send(f'Not currently connected, but home channel is **{name}**.')
        else:
            await ctx.send("Not connected and no home channel set. Use `!join` to set one.")


@bot.event
async def on_voice_state_update(member: discord.Member, before: discord.VoiceState, after: discord.VoiceState):
    if member.id != bot.user.id:
        return
    # Bot was disconnected from a channel
    if before.channel and not after.channel:
        guild_id = before.channel.guild.id
        if guild_id in _leaving:
            _leaving.discard(guild_id)
            return
        channel_id = home_channels.get(str(guild_id))
        if not channel_id:
            return
        await asyncio.sleep(3)
        guild = before.channel.guild
        channel = guild.get_channel(int(channel_id))
        if channel and not guild.voice_client:
            try:
                await channel.connect()
                print(f'Reconnected to #{channel.name} in {guild.name}')
            except Exception as e:
                print(f'Reconnect failed in {guild.name}: {e}')


if not TOKEN:
    raise RuntimeError('DISCORD_TOKEN is not set. Copy .env.example to .env and fill it in.')

bot.run(TOKEN)
