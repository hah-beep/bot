import discord
from discord.ext import commands
import asyncio
import json
import os
from collections import deque
from dotenv import load_dotenv
import yt_dlp

load_dotenv()

TOKEN = os.getenv('DISCORD_TOKEN')
PREFIX = os.getenv('PREFIX', '!')
CHANNELS_FILE = 'channels.json'

intents = discord.Intents.default()
intents.message_content = True
intents.voice_states = True

bot = commands.Bot(command_prefix=PREFIX, intents=intents)

_leaving = set()

YDL_OPTS = {
    'format': 'bestaudio/best',
    'noplaylist': True,
    'quiet': True,
    'no_warnings': True,
    'default_search': 'ytsearch',
    'source_address': '0.0.0.0',
    'extractor_args': {'youtube': {'player_client': ['ios']}},
}

FFMPEG_OPTS = {
    'before_options': '-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5',
    'options': '-vn',
}

# Per-guild state
music_queues: dict[int, deque] = {}
now_playing: dict[int, str] = {}


def load_channels() -> dict:
    if os.path.exists(CHANNELS_FILE):
        with open(CHANNELS_FILE, 'r') as f:
            return json.load(f)
    return {}


def save_channels(data: dict):
    with open(CHANNELS_FILE, 'w') as f:
        json.dump(data, f, indent=2)


home_channels: dict = load_channels()


def get_queue(guild_id: int) -> deque:
    if guild_id not in music_queues:
        music_queues[guild_id] = deque()
    return music_queues[guild_id]


async def connect_to_channel(channel: discord.VoiceChannel):
    guild = channel.guild
    if guild.voice_client:
        await guild.voice_client.move_to(channel)
    else:
        await channel.connect()


async def play_next(ctx):
    queue = get_queue(ctx.guild.id)
    if not queue or not ctx.voice_client:
        now_playing.pop(ctx.guild.id, None)
        return

    url, title = queue.popleft()
    now_playing[ctx.guild.id] = title

    source = discord.PCMVolumeTransformer(discord.FFmpegPCMAudio(url, **FFMPEG_OPTS))

    def after_play(error):
        if error:
            print(f'Player error: {error}')
        asyncio.run_coroutine_threadsafe(play_next(ctx), bot.loop)

    ctx.voice_client.play(source, after=after_play)
    await ctx.send(f'Now playing: **{title}**')


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
    if not ctx.voice_client:
        await ctx.send("I'm not in a voice channel.")
        return

    _leaving.add(ctx.guild.id)
    home_channels.pop(str(ctx.guild.id), None)
    save_channels(home_channels)
    music_queues.pop(ctx.guild.id, None)
    now_playing.pop(ctx.guild.id, None)
    await ctx.voice_client.disconnect()
    await ctx.send('Left the voice channel.')


@bot.command(name='status')
async def status(ctx):
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


@bot.command(name='play', aliases=['p'])
async def play(ctx, *, query: str):
    if not ctx.voice_client:
        if ctx.author.voice and ctx.author.voice.channel:
            channel = ctx.author.voice.channel
            await channel.connect()
            home_channels[str(ctx.guild.id)] = str(channel.id)
            save_channels(home_channels)
            _leaving.discard(ctx.guild.id)
        else:
            await ctx.send('Join a voice channel first.')
            return

    async with ctx.typing():
        loop = asyncio.get_event_loop()
        try:
            with yt_dlp.YoutubeDL(YDL_OPTS) as ydl:
                search = query if query.startswith('http') else f'ytsearch:{query}'
                info = await loop.run_in_executor(None, lambda: ydl.extract_info(search, download=False))
                if 'entries' in info:
                    info = info['entries'][0]
                url = info['url']
                title = info.get('title', 'Unknown')
        except Exception as e:
            await ctx.send(f'Could not find that: {e}')
            return

    queue = get_queue(ctx.guild.id)
    queue.append((url, title))

    if not ctx.voice_client.is_playing() and not ctx.voice_client.is_paused():
        await play_next(ctx)
    else:
        await ctx.send(f'Added to queue: **{title}**')


@bot.command(name='skip', aliases=['s'])
async def skip(ctx):
    if ctx.voice_client and (ctx.voice_client.is_playing() or ctx.voice_client.is_paused()):
        ctx.voice_client.stop()
        await ctx.send('Skipped.')
    else:
        await ctx.send('Nothing is playing.')


@bot.command(name='pause')
async def pause(ctx):
    if ctx.voice_client and ctx.voice_client.is_playing():
        ctx.voice_client.pause()
        await ctx.send('Paused.')
    else:
        await ctx.send('Nothing is playing.')


@bot.command(name='resume')
async def resume(ctx):
    if ctx.voice_client and ctx.voice_client.is_paused():
        ctx.voice_client.resume()
        await ctx.send('Resumed.')
    else:
        await ctx.send('Nothing is paused.')


@bot.command(name='stop')
async def stop(ctx):
    if not ctx.voice_client:
        await ctx.send("I'm not playing anything.")
        return
    music_queues.pop(ctx.guild.id, None)
    now_playing.pop(ctx.guild.id, None)
    ctx.voice_client.stop()
    await ctx.send('Stopped and cleared the queue.')


@bot.command(name='queue', aliases=['q'])
async def queue_cmd(ctx):
    queue = get_queue(ctx.guild.id)
    current = now_playing.get(ctx.guild.id)
    if not current and not queue:
        await ctx.send('The queue is empty.')
        return
    lines = []
    if current:
        lines.append(f'Now playing: **{current}**')
    for i, (_, title) in enumerate(queue, 1):
        lines.append(f'{i}. {title}')
    await ctx.send('\n'.join(lines))


@bot.command(name='nowplaying', aliases=['np'])
async def nowplaying(ctx):
    current = now_playing.get(ctx.guild.id)
    if current:
        await ctx.send(f'Now playing: **{current}**')
    else:
        await ctx.send('Nothing is playing right now.')


@bot.event
async def on_voice_state_update(member: discord.Member, before: discord.VoiceState, after: discord.VoiceState):
    if member.id != bot.user.id:
        return
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
