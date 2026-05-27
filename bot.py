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
    # 🌟 [오디오 포맷] 유튜브 오디오 스트림을 폭넓게 수용하여 포맷 없음(403) 에러를 방지합니다.
    "format": "bestaudio/best",

    # 플레이리스트 방지
    "noplaylist": True,

    # ytsearch 자동 사용
    "default_search": "ytsearch",

    # 로그 안정화
    "quiet": True,
    "no_warnings": True,

    # 네트워크 안정화
    "socket_timeout": 20,
    "extractor_retries": 10,
    "retries": 10,
    "source_address": "0.0.0.0",

    # 인증서/지역 우회
    "nocheckcertificate": True,
    "prefer_insecure": False,
    "geo_bypass": True,
    "geo_bypass_country": "US",

    # 🌟 [Render 디스크 최적화] 가상 서버 권한 에러 및 용량 부족 문제를 완전 차단합니다.
    "cachedir": False,

    # 🌟 [유튜브 클라이언트 핵심 수정] 
    # 토큰 에러를 내뿜는 ios를 제외하고, 보안 검문이 덜한 안드로이드 뮤직(android_music) 전용 규격을 투입합니다.
    "extractor_args": {
        "youtube": {
            "player_client": [
                "android_music",
                "mweb"
            ],
            # 오디오 싱크 밀림과 DASH 포맷 충돌을 제거합니다.
            "skip": [
                "dash",
                "hls"
            ]
        }
    },

    # 🌟 [헤더 동기화] 위의 player_client(android)와 실제 접속 환경 데이터를 완벽하게 일치시킵니다.
    "http_headers": {
        "User-Agent": (
            "Mozilla/5.0 (Linux; Android 14; Pixel 8) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Mobile Safari/537.36"
        ),
        "Accept-Language": (
            "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7"
        ),
        "Accept": "*/*"
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

    # 🌟 [핵심 누락 보완]: 일반 검색어일 때 유튜브 보안 필터를 통과하기 위한 검색용 임시 설정입니다.
    if not query.startswith(("http://", "https://")):
        opts["extractor_args"] = {
            "youtube": {
                "player_client": ["web", "ios", "mweb"],
                "skip": ["dash", "hls"]
            }
        }
        opts["http_headers"] = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
            "Accept": "*/*",
            "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7",
        }

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

    # ytsearch 결과 처리
    if info.get("_type") == "playlist":

        entries = info.get("entries")

        if entries is None:
            LOGGER.error("Entries is None")
            raise MusicError("검색 결과를 찾지 못했어요.")

        # generator -> list
        entries = list(entries)
        LOGGER.info("Entries count: %s", len(entries))

        # 유효한 엔트리만 필터링
        valid_entries = [
            entry for entry in entries
            if (
                entry
                and (
                    entry.get("url")
                    or entry.get("webpage_url")
                    or entry.get("id")
                )
            )
        ]

        LOGGER.info("Valid entries count: %s", len(valid_entries))

        if not valid_entries:
            LOGGER.error("No valid entries found")
            raise MusicError("검색 결과를 찾지 못했어요.")

        # 🌟 [적용 완료]: 첫 번째 항목 [0]을 정확하게 추출합니다.
        first = valid_entries[0]

        # webpage_url 없으면 생성
        if not first.get("webpage_url"):
            video_id = first.get("id")
            if video_id:
                first["webpage_url"] = youtube_watch_url(video_id)

        LOGGER.info("Selected video: %s", first.get("title"))
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

    # 🌟 [안전장치 1]: 만약 extract_info가 리스트 형태로 결과를 반환했다면 첫 번째 항목을 꺼냅니다.
    if isinstance(info, list):
        if len(info) == 0:
            raise MusicError("검색 결과가 없어요.")
        info = info[0]

    # 🌟 [안전장치 2]: 최신 android_music 우회 규격은 직접적인 스트리밍 주소(url)를 넘겨주므로, 
    # 기존 webpage_url 필드가 비어있다면 url 필드를 최우선으로 가로채어 에러를 방지합니다.
    webpage_url = (
        info.get("webpage_url")
        or info.get("url")
        or info.get("original_url")
    )

    title = info.get("title")

    if not webpage_url or not title:
        raise MusicError("곡 정보를 읽을 수 없어요.")

    # 🌟 [최종 정상 주소 보정]: 만약 주소가 id 값으로만 되어 있다면 정상적인 watch 링크로 복원합니다.
    if webpage_url and not webpage_url.startswith(("http://", "https://")):
        webpage_url = youtube_watch_url(webpage_url)

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

            self.voice = await channel.connect(timeout=90.0, reconnect=True)

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
    # [최우선 배치]: 3초 제한 타임아웃(error code: 10062)을 원천 차단하기 위해 응답 대기부터 보냅니다.
    try:
        await interaction.response.defer()
    except Exception:
        pass

    try:
        state = get_state(interaction)

        # 음성 채널 연결
        await state.connect(interaction)

        # 오디오 데이터 추출 및 트랙 빌드 수행 (yt-dlp 연동 영역)
        try:
            track = await build_track(
                query,
                interaction.user
            )
        except MusicError as me:
            # 커스텀 검색 에러 발생 시 사용자에게 예쁜 안내 메시지 전송 후 종료
            await interaction.followup.send(str(me))
            return
        except Exception as exc:
            LOGGER.exception("Track building failed: %s", exc)
            await interaction.followup.send("유튜브 오디오 스트림을 가져오는 데 실패했습니다. 잠시 후 다시 시도해 주세요.")
            return

        # 재생 대기열(Queue)에 추가 및 재생 시도
        await state.enqueue(track)

        # 추가 완료 알림 임베드 구성
        embed = track_embed(
            "추가됨",
            track,
            discord.Color.blurple()
        )

        # 응답 완료 메시지 송출
        await interaction.followup.send(
            embed=embed
        )

    except Exception as e:
        LOGGER.exception("Play error: %s", e)
        # 예외 상황 발생 시 대기 상태(defer)를 해제하며 안전하게 에러를 전송합니다.
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