"""Discord bot bridge — lets you chat with Lydia from Discord.
Listens for DMs and `/lydia <message>` in a guild, routes to process_request,
and replies back in the same channel.
"""
from __future__ import annotations
import asyncio
import threading
from pathlib import Path

from brain.config import load_dotenv

_REPO_ROOT = Path(__file__).parent.parent

_typing_lock = threading.Lock()


def _token() -> str:
    return load_dotenv().get("DISCORD_BOT_TOKEN", "")


def _latest_screenshot() -> Path | None:
    """Return the most recent .png screenshot found on the Desktop (or Pictures/Screenshots)."""
    candidates = []
    desktop = Path.home() / "Desktop"
    if desktop.is_dir():
        candidates += list(desktop.glob("*.png"))
        candidates += list(desktop.glob("*.jpg"))
        candidates += list(desktop.glob("*.jpeg"))
    shots = Path.home() / "Pictures" / "Screenshots"
    if shots.is_dir():
        candidates += list(shots.glob("*.png"))
    if not candidates:
        return None
    return max(candidates, key=lambda p: p.stat().st_mtime)


def start_discord_bot() -> None:
    """Start the Discord bot in a background thread (non-blocking)."""
    token = _token()
    if not token:
        print("[Discord] No DISCORD_BOT_TOKEN in .env — Discord bot disabled.")
        return

    try:
        import discord
        from discord.ext import commands
    except ImportError:
        print("[Discord] discord.py not installed — run: pip install discord.py")
        return

    intents = discord.Intents.default()
    intents.message_content = True
    intents.dm_messages = True

    bot = commands.Bot(command_prefix="!", intents=intents, help_command=None)

    @bot.event
    async def on_ready():
        print(f"[Discord] Logged in as {bot.user} (ID {bot.user.id})")

    @bot.command(name="lydia")
    async def lydia_chat(ctx, *, message: str = ""):
        """Chat with Lydia. Usage: /lydia <your message>"""
        message = (message or "").strip()
        if not message:
            await ctx.reply("Give me something to work with, bro! Usage: `/lydia <message>`")
            return
        await _handle(ctx, message)

    @bot.command(name="photo")
    async def send_photo(ctx):
        """Send your latest Desktop screenshot. Usage: !photo"""
        latest = _latest_screenshot()
        if latest is None:
            await ctx.reply("I couldn't find any screenshots on your Desktop yet, bro.")
            return
        try:
            await ctx.reply(file=discord.File(latest))
        except Exception as e:
            await ctx.reply(f"Oops, couldn't send the photo: {e}")

    @bot.command(name="sendfile")
    async def send_file(ctx, *, path: str = ""):
        """Send any image/file by path. Usage: !sendfile C:\\path\\to\\file.png"""
        path = (path or "").strip().strip('"').strip("'")
        if not path:
            await ctx.reply("Give me a path, bro! Usage: `!sendfile C:\\path\\to\\picture.png`")
            return
        p = Path(path)
        if not p.is_file():
            await ctx.reply(f"I couldn't find that file: `{path}`")
            return
        try:
            await ctx.reply(file=discord.File(p))
        except Exception as e:
            await ctx.reply(f"Oops, couldn't send that file: {e}")

    @bot.event
    async def on_message(message):
        # Ignore messages from the bot itself.
        if message.author.bot:
            return

        # Handle DMs.
        if isinstance(message.channel, discord.DMChannel):
            # If it's a command (starts with !), let the command system handle it.
            if message.content.strip().lower().startswith((
                "!photo", "!sendfile", "!lydia", "!help"
            )):
                await bot.process_commands(message)
                return
            await _handle(message, message.content)
            return

        # Allow normal command handling in guilds too.
        await bot.process_commands(message)

    async def _handle(ctx_or_channel, text: str):
        text = (text or "").strip()
        if not text:
            return
        try:
            await ctx_or_channel.trigger_typing()
        except Exception:
            pass

        # Run process_request on the event loop executor so we don't block Discord.
        from brain.main import process_request
        # Use get_running_loop() — on Python 3.12 get_event_loop() raises
        # "no current event loop" on non-main (bot) threads. A running loop is
        # always present here since Discord runs inside asyncio.
        loop = asyncio.get_running_loop()
        try:
            response = await loop.run_in_executor(None, process_request, text)
        except Exception as e:
            response = f"Oops, my brain hiccupped: {e}"

        # Split long replies to stay under Discord's 2000-char limit.
        MAX = 1900
        if len(response) > MAX:
            parts = [response[i:i + MAX] for i in range(0, len(response), MAX)]
            for part in parts:
                await ctx_or_channel.reply(part)
        else:
            await ctx_or_channel.reply(response)

    def _run():
        try:
            bot.run(token)
        except discord.LoginFailure as e:
            print(f"[Discord] Login failed — check DISCORD_BOT_TOKEN in .env: {e}")
        except Exception as e:
            print(f"[Discord] Error running bot: {e}")

    t = threading.Thread(target=_run, daemon=True)
    t.start()
    print("[Discord] Bot thread started.")
