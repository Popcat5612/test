from __future__ import annotations

import asyncio
import copy
import logging
import os
import shutil
from collections import deque
from dataclasses import dataclass, field
from typing import Optional
from typing import Deque
from urllib.parse import parse_qs, urlparse

import discord
from discord import app_commands
from discord.ext import commands
from dotenv import load_dotenv
import yt_dlp


# =========================
# ENV
# =========================

load_dotenv(override=True)

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

LOGGER.info("Node path: %s", shutil.which("node"))
LOGGER.info("FFmpeg path: %s", shutil.which("ffmpeg"))


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
    "default_search": "ytsearch",
    "quiet": False,
    "no_warnings": False,
    "socket_timeout": 30,
    "extractor_retries": 3,
    "retries": 3,
    "cachedir": False,
    "source_address": "0.0.0.0",
    "extractor_args": {
        "youtube": {
            "player_client": ["android", "ios"],
        }
    },
    "http_headers": {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/137.0.0.0 Safari/537.36"
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
    stream_url: str | None = None

    @property
    def requester_mention(self) -> str:
        return f"<@{self.requester_id}>"


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
    opts = copy.deepcopy(YTDL_OPTIONS)

    LOGGER.info(
        "Cookie exists: %s",
        os.path.exists(COOKIE_PATH)
    )

    if os.path.exists(COOKIE_PATH):
        LOGGER.info(
            "Using cookie file: %s",
            COOKIE_PATH
        )
        opts["cookiefile"] = COOKIE_PATH

    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            if query.startswith(("http://", "https://")):
                search_query = query
            else:
                search_query = f"ytsearch1:{query}"

            LOGGER.info(
                "Searching: %s",
                search_query
            )

            info = ydl.extract_info(
                search_query,
                download=False,
                process=True
            )

            if not info:
                raise MusicError(
                    "검색 결과가 없어요."
                )

    except MusicError:
        raise
    except Exception as e:
        LOGGER.exception(
            "yt-dlp failed: %s",
            e
        )
        raise MusicError(
            "유튜브 정보를 가져오지 못했어요. 잠시 후 다시 시도해 주세요."
        )

    if (
        isinstance(info, dict)
        and info.get("_type") == "playlist"
    ):
        entries = [
            entry
            for entry in info.get("entries", [])
            if entry
        ]

        if not entries:
            raise MusicError(
                "검색 결과가 없어요."
            )

        first = entries[0]

        if 'url' not in first or not first.get('url'):
            with yt_dlp.YoutubeDL(opts) as ydl_video:
                video_url = first.get("webpage_url") or youtube_watch_url(first.get("id"))
                first = ydl_video.extract_info(video_url, download=False)

        if not first.get("webpage_url"):
            video_id = first.get("id")
            if video_id:
                first["webpage_url"] = (
                    youtube_watch_url(video_id)
                )

        return first

    return info


async def resolve_stream_url(track: Track) -> str:
    if track.stream_url:
        LOGGER.info("Using cached stream_url for: %s", track.title)
        return track.stream_url

    info = await asyncio.to_thread(
        extract_info,
        track.webpage_url
    )

    stream_url = info.get("url")

    if not stream_url:
        for f in reversed(info.get("formats", [])):
            if f.get("url"):
                if f.get("vcodec") != "none" and f.get("acodec") == "none":
                    continue
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

    if isinstance(info, list):
        if len(info) == 0:
            raise MusicError("검색 결과가 없어요.")
        info = info[0]

    webpage_url = (
        info.get("webpage_url")
        or info.get("url")
        or info.get("original_url")
    )

    title = info.get("title")

    if not webpage_url or not title:
        raise MusicError("곡 정보를 읽을 수 없어요.")

    if webpage_url and not webpage_url.startswith(("http://", "https://")):
        webpage_url = youtube_watch_url(webpage_url)

    audio_source_url = info.get("url")

    return Track(
        title=title,
        webpage_url=webpage_url,
        duration=info.get("duration"),
        requester_id=requester.id,
        requester_name=requester.display_name,
        thumbnail=info.get("thumbnail"),
        source_id=info.get("id"),
        stream_url=audio_source_url,
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

    vc = interaction.guild.voice_client

    try:

        if vc:

            if vc.is_connected():

                self.voice = vc

                if vc.channel != channel:
                    await vc.move_to(channel)
                    await asyncio.sleep(1)

                return

            try:
                await vc.disconnect(force=True)
                await asyncio.sleep(2)
            except Exception:
                pass

        LOGGER.info("VOICE CONNECT START")

        self.voice = await channel.connect(
            timeout=60.0
        )

        for _ in range(20):

            if self.voice and self.voice.is_connected():

                LOGGER.info("VOICE CONNECT SUCCESS")

                await asyncio.sleep(2)
                return

            await asyncio.sleep(0.5)

        raise MusicError(
            "음성 채널 연결에 실패했어요."
        )

    except MusicError:
        raise

    except asyncio.TimeoutError:
        raise MusicError(
            "음성 채널 연결 시간이 초과됐어요."
        )

    except discord.ClientException as e:
        raise MusicError(
            f"음성 채널 연결 실패: {e}"
        )

    except Exception as e:

        LOGGER.exception(
            "VOICE CONNECT ERROR: %s",
            e
        )

        raise MusicError(
            f"알 수 없는 음성 연결 오류: {e}"
        )

    async def play_next(self):

    # Voice 연결 확인
    if (
        not self.voice
        or not self.voice.is_connected()
    ):
        LOGGER.warning(
            "Voice disconnected, cancelling playback"
        )

        self.current = None
        return

    if not self.queue:

        self.current = None
        return

    track = self.queue.popleft()

    self.current = track

    try:

        stream_url = await resolve_stream_url(track)

    except Exception as e:

        LOGGER.exception(
            "Stream resolve failed: %s",
            e
        )

        self.current = None

        if self.queue:
            await self.play_next()

        return

    # resolve 중 연결 끊겼는지 재확인
    if (
        not self.voice
        or not self.voice.is_connected()
    ):
        LOGGER.warning(
            "Voice disconnected after resolve"
        )

        self.current = None
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
            LOGGER.error(
                "Player error: %s",
                error
            )

        if (
            self.voice
            and self.voice.is_connected()
        ):
            asyncio.run_coroutine_threadsafe(
                self.play_next(),
                loop
            )

    try:

        self.voice.play(
            source,
            after=after_play
        )

        LOGGER.info(
            "Now playing: %s",
            track.title
        )

    except discord.ClientException as e:

        LOGGER.exception(
            "Voice play failed: %s",
            e
        )

        self.current = None

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
    try:
        await interaction.response.defer()
    except Exception:
        pass

    try:
        state = get_state(interaction)

        await state.connect(interaction)

        try:
            track = await build_track(
                query,
                interaction.user
            )
        except MusicError as me:
            await interaction.followup.send(str(me))
            return
        except Exception as exc:
            LOGGER.exception("Track building failed: %s", exc)
            await interaction.followup.send("유튜브 오디오 스트림을 가져오는 데 실패했습니다. 잠시 후 다시 시도해 주세요.")
            return

        await state.enqueue(track)

        embed = track_embed(
            "추가됨",
            track,
            discord.Color.blurple()
        )

        await interaction.followup.send(
            embed=embed
        )

    except MusicError as e:
        try:
            await interaction.followup.send(str(e))
        except Exception:
            pass
    except Exception as e:
        LOGGER.exception("Play error: %s", e)
        try:
            await interaction.followup.send(
                f"⚠️ 재생 중 오류가 발생했습니다: {str(e)}"
            )
        except Exception:
            pass


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


@bot.event
async def on_voice_state_update(
    member,
    before,
    after
):

    if bot.user and member.id == bot.user.id:

        LOGGER.info(
            "VOICE STATE: %s -> %s",
            before.channel,
            after.channel
        )


@bot.event
async def on_voice_state_update(
    member,
    before,
    after
):

    if bot.user and member.id == bot.user.id:

        LOGGER.info(
            "VOICE STATE: %s -> %s",
            before.channel,
            after.channel
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