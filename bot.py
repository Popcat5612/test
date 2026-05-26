from __future__ import annotations

import asyncio
import logging
import os
import shutil
from collections import deque
from dataclasses import dataclass
from typing import Optional
from typing import Deque
from urllib.parse import parse_qs, urlparse

import discord
from discord import app_commands
from discord.ext import commands
from dotenv import load_dotenv
import yt_dlp

# keep_alive.py 있을 때만 사용
try:
    from keep_alive import keep_alive

    keep_alive()
except:
    pass


# =========================
# ENV
# =========================

load_dotenv()

COOKIE_PATH = "/tmp/cookies.txt"

if os.path.exists("/etc/secrets/cookies.txt"):
    shutil.copy("/etc/secrets/cookies.txt", COOKIE_PATH)

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
DISCORD_GUILD_ID = os.getenv("DISCORD_GUILD_ID")

FFMPEG_EXECUTABLE = (
    os.getenv("FFMPEG_EXECUTABLE")
    or shutil.which("ffmpeg")
    or "ffmpeg"
)

LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()

logging.basicConfig(
    level=LOG_LEVEL,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)

LOGGER = logging.getLogger("music-bot")


# =========================
# FFMPEG
# =========================

FFMPEG_BEFORE_OPTIONS = (
    "-reconnect 1 "
    "-reconnect_streamed 1 "
    "-reconnect_delay_max 5"
)

FFMPEG_OPTIONS = "-vn -loglevel warning"


# =========================
# YTDL
# =========================

YTDL_OPTIONS = {
    "format": "bestaudio/best",
    "noplaylist": True,
    "quiet": True,
    "no_warnings": True,
    "default_search": "ytsearch",
    "socket_timeout": 20,
    "extractor_retries": 10,
    "retries": 10,
    "nocheckcertificate": True,
    "geo_bypass": True,
    "source_address": "0.0.0.0",
    "extractor_args": {
        "youtube": {
            "player_client": [
                "android",
                "ios",
                "web",
                "mweb",
            ]
        }
    },
    "http_headers": {
        "User-Agent": (
            "Mozilla/5.0 "
            "(Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 "
            "(KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        )
    }
}


# =========================
# ERROR
# =========================

class MusicError(Exception):
    pass


# =========================
# TRACK
# =========================

@dataclass
class Track:
    title: str
    webpage_url: str
    duration: int | None
    requester_id: int
    requester_name: str
    thumbnail: str | None = None
    source_id: str | None = None

    @property
    def requester_mention(self) -> str:
        return self.requester_name


# =========================
# UTIL
# =========================

def youtube_watch_url(video_id: str) -> str:
    return f"https://www.youtube.com/watch?v={video_id}"


def youtube_video_id_from_url(url: str | None) -> str | None:
    if not url:
        return None

    parsed = urlparse(url)

    if parsed.hostname == "youtu.be":
        return parsed.path[1:]

    if parsed.hostname and "youtube" in parsed.hostname:
        query = parse_qs(parsed.query)
        return query.get("v", [None])[0]

    return None


def format_duration(seconds: int | None) -> str:
    if seconds is None:
        return "알 수 없음"

    hours, remainder = divmod(seconds, 3600)
    minutes, seconds = divmod(remainder, 60)

    if hours:
        return f"{hours}:{minutes:02}:{seconds:02}"

    return f"{minutes}:{seconds:02}"


def parse_volume_percent(value: str) -> int:
    try:
        value = int(value)
    except:
        raise MusicError("숫자를 입력해 주세요.")

    if value < 0 or value > 100:
        raise MusicError("0~100 사이만 가능해요.")

    return value


# =========================
# YTDL
# =========================

def extract_info(query: str) -> dict:

    opts = YTDL_OPTIONS.copy()

    if os.path.exists(COOKIE_PATH):
        opts["cookiefile"] = COOKIE_PATH

    try:
        with yt_dlp.YoutubeDL(opts) as ydl:

            if query.startswith(("http://", "https://")):
                search_query = query
            else:
                search_query = f"ytsearch1:{query}"

            LOGGER.info("Searching: %s", search_query)

            info = ydl.extract_info(
                search_query,
                download=False
            )

            if not info:
                raise MusicError("검색 결과가 없어요.")

    except Exception as e:
        LOGGER.exception("yt-dlp error: %s", e)
        raise MusicError("유튜브 검색 실패")

    if info.get("_type") == "playlist":

        entries = list(info.get("entries", []))

        valid_entries = [
            e for e in entries
            if e and (
                e.get("url")
                or e.get("webpage_url")
                or e.get("id")
            )
        ]

        if not valid_entries:
            raise MusicError("검색 결과가 없어요.")

        first = valid_entries[0]

        if not first.get("webpage_url"):
            video_id = first.get("id")

            if video_id:
                first["webpage_url"] = youtube_watch_url(video_id)

        return first

    return info


async def resolve_stream_url(track: Track) -> str:

    info = await asyncio.to_thread(
        extract_info,
        track.webpage_url
    )

    stream_url = info.get("url")

    if not stream_url:

        for f in reversed(info.get("formats", [])):
            if f.get("url"):
                stream_url = f["url"]
                break

    if not stream_url:
        raise MusicError("스트림 URL을 찾지 못했어요.")

    track.title = info.get("title") or track.title
    track.duration = info.get("duration") or track.duration
    track.thumbnail = info.get("thumbnail") or track.thumbnail

    return stream_url


async def build_track(
    query: str,
    requester: discord.abc.User
) -> Track:

    info = await asyncio.to_thread(
        extract_info,
        query
    )

    webpage_url = (
        info.get("webpage_url")
        or info.get("original_url")
    )

    title = info.get("title")

    if not webpage_url or not title:
        raise MusicError("곡 정보를 읽을 수 없어요.")

    return Track(
        title=title,
        webpage_url=webpage_url,
        duration=info.get("duration"),
        requester_id=requester.id,
        requester_name=requester.display_name,
        thumbnail=info.get("thumbnail"),
        source_id=info.get("id"),
    )


# =========================
# EMBED
# =========================

def track_embed(
    title: str,
    track: Track,
    color: discord.Color
) -> discord.Embed:

    embed = discord.Embed(
        title=title,
        description=f"[{track.title}]({track.webpage_url})",
        color=color,
    )

    embed.add_field(
        name="길이",
        value=format_duration(track.duration),
        inline=True,
    )

    embed.add_field(
        name="요청",
        value=track.requester_mention,
        inline=True,
    )

    if track.thumbnail:
        embed.set_thumbnail(url=track.thumbnail)

    return embed


# =========================
# MUSIC STATE
# =========================

class GuildMusicState:

    def __init__(self, bot, guild_id: int):

        self.bot = bot
        self.guild_id = guild_id

        self.queue: Deque[Track] = deque()

        self.current: Track | None = None

        self.voice: discord.VoiceClient | None = None

        self.volume = 0.7

        self.lock = asyncio.Lock()

    async def connect(self, interaction: discord.Interaction):

        if interaction.guild is None:
            raise MusicError("서버에서만 사용 가능")

        voice_state = getattr(interaction.user, "voice", None)

        if voice_state is None or voice_state.channel is None:
            raise MusicError("먼저 음성 채널에 들어가 주세요.")

        channel = voice_state.channel

        if interaction.guild.voice_client is None:

            self.voice = await channel.connect()

        else:

            self.voice = interaction.guild.voice_client

            if self.voice.channel != channel:
                await self.voice.move_to(channel)

    async def enqueue(self, track: Track):

        async with self.lock:

            self.queue.append(track)

            if not self.is_playing():
                await self.play_next()

    def is_playing(self):

        return (
            self.voice
            and (
                self.voice.is_playing()
                or self.voice.is_paused()
            )
        )

    async def play_next(self):

        if not self.queue:

            self.current = None

            return

        track = self.queue.popleft()

        self.current = track

        try:

            stream_url = await resolve_stream_url(track)

        except Exception as e:

            LOGGER.exception("Stream resolve failed: %s", e)

            await self.play_next()

            return

        source = discord.PCMVolumeTransformer(
            discord.FFmpegPCMAudio(
                stream_url,
                executable=FFMPEG_EXECUTABLE,
                before_options=FFMPEG_BEFORE_OPTIONS,
                options=FFMPEG_OPTIONS,
            ),
            volume=self.volume,
        )

        loop = asyncio.get_running_loop()

        def after_play(error):

            if error:
                LOGGER.error("Player error: %s", error)

            asyncio.run_coroutine_threadsafe(
                self.play_next(),
                loop
            )

        self.voice.play(
            source,
            after=after_play
        )

    async def skip(self):

        if self.voice and self.voice.is_playing():
            self.voice.stop()
            return True

        return False

    async def pause(self):

        if self.voice and self.voice.is_playing():
            self.voice.pause()
            return True

        return False

    async def resume(self):

        if self.voice and self.voice.is_paused():
            self.voice.resume()
            return True

        return False

    async def stop(self):

        self.queue.clear()

        self.current = None

        if self.voice:

            if self.voice.is_playing():
                self.voice.stop()

            await self.voice.disconnect()

            self.voice = None

    async def set_volume(self, percent: int):

        self.volume = percent / 100

        if (
            self.voice
            and isinstance(
                self.voice.source,
                discord.PCMVolumeTransformer
            )
        ):
            self.voice.source.volume = self.volume

    async def queue_embed(self):

        embed = discord.Embed(
            title="대기열",
            color=discord.Color.green()
        )

        if self.current:

            embed.add_field(
                name="현재곡",
                value=(
                    f"[{self.current.title}]"
                    f"({self.current.webpage_url})"
                ),
                inline=False
            )

        if self.queue:

            lines = []

            for i, track in enumerate(self.queue, start=1):

                lines.append(
                    f"{i}. "
                    f"[{track.title}]"
                    f"({track.webpage_url})"
                )

            embed.add_field(
                name="다음곡",
                value="\n".join(lines[:10]),
                inline=False
            )

        if not self.current and not self.queue:
            embed.description = "대기열 비어있음"

        return embed


# =========================
# BOT
# =========================

class MusicBot(commands.Bot):

    def __init__(self):

        intents = discord.Intents.default()

        super().__init__(
            command_prefix="!",
            intents=intents
        )

        self.states = {}

    async def setup_hook(self):

        if DISCORD_GUILD_ID:

            guild = discord.Object(
                id=int(DISCORD_GUILD_ID)
            )

            self.tree.copy_global_to(
                guild=guild
            )

            await self.tree.sync(
                guild=guild
            )

        else:

            await self.tree.sync()

    def get_state(self, guild_id: int):

        if guild_id not in self.states:

            self.states[guild_id] = (
                GuildMusicState(
                    self,
                    guild_id
                )
            )

        return self.states[guild_id]


bot = MusicBot()


# =========================
# COMMANDS
# =========================

def get_state(interaction):

    if interaction.guild_id is None:
        raise MusicError("서버에서만 사용 가능")

    return bot.get_state(interaction.guild_id)


@bot.tree.command(
    name="재생",
    description="음악 재생"
)
async def play(
    interaction: discord.Interaction,
    query: str
):

    await interaction.response.defer()

    try:

        state = get_state(interaction)

        await state.connect(interaction)

        track = await build_track(
            query,
            interaction.user
        )

        await state.enqueue(track)

        embed = track_embed(
            "추가됨",
            track,
            discord.Color.blurple()
        )

        await interaction.followup.send(
            embed=embed
        )

    except Exception as e:

        LOGGER.exception("Play error: %s", e)

        await interaction.followup.send(
            str(e)
        )


@bot.tree.command(
    name="건너뛰기",
    description="현재곡 스킵"
)
async def skip(interaction: discord.Interaction):

    state = get_state(interaction)

    skipped = await state.skip()

    if skipped:
        await interaction.response.send_message(
            "건너뜀"
        )
    else:
        await interaction.response.send_message(
            "재생중인 곡 없음"
        )


@bot.tree.command(
    name="일시정지",
    description="일시정지"
)
async def pause(interaction: discord.Interaction):

    state = get_state(interaction)

    paused = await state.pause()

    if paused:
        await interaction.response.send_message(
            "일시정지됨"
        )
    else:
        await interaction.response.send_message(
            "재생중인 곡 없음"
        )


@bot.tree.command(
    name="다시재생",
    description="다시 재생"
)
async def resume(interaction: discord.Interaction):

    state = get_state(interaction)

    resumed = await state.resume()

    if resumed:
        await interaction.response.send_message(
            "다시 재생"
        )
    else:
        await interaction.response.send_message(
            "일시정지 상태 아님"
        )


@bot.tree.command(
    name="정지",
    description="정지 후 퇴장"
)
async def stop(interaction: discord.Interaction):

    state = get_state(interaction)

    await state.stop()

    await interaction.response.send_message(
        "정지 완료"
    )


@bot.tree.command(
    name="볼륨",
    description="볼륨 변경"
)
async def volume(
    interaction: discord.Interaction,
    percent: app_commands.Range[int, 0, 100]
):

    state = get_state(interaction)

    await state.set_volume(percent)

    await interaction.response.send_message(
        f"볼륨 {percent}%"
    )


@bot.tree.command(
    name="대기열",
    description="대기열 보기"
)
async def queue(interaction: discord.Interaction):

    state = get_state(interaction)

    embed = await state.queue_embed()

    await interaction.response.send_message(
        embed=embed
    )


@bot.tree.command(
    name="현재곡",
    description="현재곡 보기"
)
async def now(interaction: discord.Interaction):

    state = get_state(interaction)

    if not state.current:

        await interaction.response.send_message(
            "재생중인 곡 없음"
        )

        return

    embed = track_embed(
        "현재 재생중",
        state.current,
        discord.Color.green()
    )

    await interaction.response.send_message(
        embed=embed
    )


# =========================
# READY
# =========================

@bot.event
async def on_ready():

    LOGGER.info(
        "Logged in as %s",
        bot.user
    )


# =========================
# MAIN
# =========================

def main():

    if not DISCORD_TOKEN:
        raise RuntimeError(
            "DISCORD_TOKEN 없음"
        )

    bot.run(DISCORD_TOKEN)


if __name__ == "__main__":
    main()