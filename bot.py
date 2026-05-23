from __future__ import annotations

import asyncio
import logging
import os
import shutil
from collections import deque
from dataclasses import dataclass
from typing import Deque
from urllib.parse import parse_qs, urlparse

import discord
from discord import app_commands
from discord.ext import commands
from dotenv import load_dotenv
import yt_dlp


load_dotenv()

LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
logging.basicConfig(
    level=LOG_LEVEL,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
LOGGER = logging.getLogger("music-bot")

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
DISCORD_GUILD_ID = os.getenv("DISCORD_GUILD_ID")
FFMPEG_EXECUTABLE = os.getenv("FFMPEG_EXECUTABLE") or shutil.which("ffmpeg") or "ffmpeg"

FFMPEG_BEFORE_OPTIONS = "-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5"
FFMPEG_OPTIONS = "-vn -loglevel warning"

YTDL_OPTIONS = {
    "default_search": "ytsearch",
    "format": "bestaudio/best",
    "noplaylist": True,
    "quiet": True,
    "socket_timeout": 15,
    "source_address": "0.0.0.0",
}

AUTOPLAY_YTDL_OPTIONS = {
    **YTDL_OPTIONS,
    "extract_flat": "in_playlist",
    "noplaylist": False,
    "playlistend": 12,
}


class MusicError(Exception):
    """User-facing music command error."""


@dataclass(slots=True)
class Track:
    title: str
    webpage_url: str
    duration: int | None
    requester_id: int
    requester_name: str
    thumbnail: str | None = None
    source_id: str | None = None
    autoplay: bool = False

    @property
    def requester_mention(self) -> str:
        if self.autoplay:
            return "자동재생"
        return f"<@{self.requester_id}>"


def format_duration(seconds: int | None) -> str:
    if seconds is None:
        return "알 수 없음"

    minutes, sec = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{sec:02d}"
    return f"{minutes}:{sec:02d}"


def parse_volume_percent(value: str) -> int:
    value = value.strip().removesuffix("%").strip()
    try:
        percent = int(value)
    except ValueError as exc:
        raise MusicError("볼륨은 0부터 100 사이 숫자로 입력해 주세요.") from exc

    if percent < 0 or percent > 100:
        raise MusicError("볼륨은 0부터 100 사이로 설정할 수 있어요.")
    return percent


def normalize_query(query: str) -> str:
    query = query.strip()
    if query.startswith(("http://", "https://")):
        return query
    return f"ytsearch1:{query}"


def youtube_video_id_from_url(url: str | None) -> str | None:
    if not url:
        return None

    parsed = urlparse(url)
    if parsed.netloc.endswith("youtu.be"):
        return parsed.path.strip("/") or None

    if "youtube.com" in parsed.netloc:
        query = parse_qs(parsed.query)
        video_ids = query.get("v")
        if video_ids:
            return video_ids[0]

        if parsed.path.startswith("/shorts/"):
            return parsed.path.removeprefix("/shorts/").split("/", maxsplit=1)[0]

    return None


def youtube_watch_url(video_id: str) -> str:
    return f"https://www.youtube.com/watch?v={video_id}"


def extract_info(query: str) -> dict:
    with yt_dlp.YoutubeDL(YTDL_OPTIONS) as ydl:
        info = ydl.extract_info(normalize_query(query), download=False)

    if not info:
        raise MusicError("검색 결과를 찾지 못했어요.")

    if "entries" in info:
        entries = [entry for entry in info.get("entries", []) if entry]
        if not entries:
            raise MusicError("검색 결과를 찾지 못했어요.")
        return entries[0]

    return info


def extract_autoplay_info(seed: Track) -> dict | None:
    source_id = seed.source_id or youtube_video_id_from_url(seed.webpage_url)
    if not source_id:
        return None

    radio_url = f"{youtube_watch_url(source_id)}&list=RD{source_id}"
    with yt_dlp.YoutubeDL(AUTOPLAY_YTDL_OPTIONS) as ydl:
        info = ydl.extract_info(radio_url, download=False)

    if not info:
        return None

    entries = [entry for entry in info.get("entries", []) if entry]
    for entry in entries:
        entry_id = entry.get("id") or youtube_video_id_from_url(entry.get("url"))
        if not entry_id or entry_id == source_id:
            continue

        title = entry.get("title")
        webpage_url = entry.get("webpage_url") or youtube_watch_url(entry_id)
        if title and webpage_url:
            return {
                "id": entry_id,
                "title": title,
                "webpage_url": webpage_url,
                "duration": entry.get("duration"),
                "thumbnail": entry.get("thumbnail"),
            }

    return None


async def build_track(query: str, requester: discord.abc.User) -> Track:
    info = await asyncio.to_thread(extract_info, query)
    webpage_url = info.get("webpage_url") or info.get("original_url")
    title = info.get("title")

    if not webpage_url or not title:
        raise MusicError("곡 정보를 읽지 못했어요. 다른 검색어나 URL로 다시 시도해 주세요.")

    return Track(
        title=title,
        webpage_url=webpage_url,
        duration=info.get("duration"),
        requester_id=requester.id,
        requester_name=requester.display_name,
        thumbnail=info.get("thumbnail"),
        source_id=info.get("id") or youtube_video_id_from_url(webpage_url),
    )


async def build_autoplay_track(seed: Track, bot_user: discord.ClientUser | None) -> Track | None:
    info = await asyncio.to_thread(extract_autoplay_info, seed)
    if not info:
        return None

    requester_id = bot_user.id if bot_user is not None else seed.requester_id
    return Track(
        title=info["title"],
        webpage_url=info["webpage_url"],
        duration=info.get("duration"),
        requester_id=requester_id,
        requester_name="자동재생",
        thumbnail=info.get("thumbnail"),
        source_id=info.get("id") or youtube_video_id_from_url(info.get("webpage_url")),
        autoplay=True,
    )


async def resolve_stream_url(track: Track) -> str:
    info = await asyncio.to_thread(extract_info, track.webpage_url)
    stream_url = info.get("url")
    if not stream_url:
        raise MusicError("오디오 스트림 주소를 찾지 못했어요.")

    track.title = info.get("title") or track.title
    track.duration = info.get("duration") or track.duration
    track.thumbnail = info.get("thumbnail") or track.thumbnail
    track.source_id = info.get("id") or track.source_id
    return stream_url


def track_embed(title: str, track: Track, color: discord.Color) -> discord.Embed:
    embed = discord.Embed(
        title=title,
        description=f"[{track.title}]({track.webpage_url})",
        color=color,
    )
    embed.add_field(name="길이", value=format_duration(track.duration), inline=True)
    embed.add_field(name="요청", value=track.requester_mention, inline=True)
    if track.thumbnail:
        embed.set_thumbnail(url=track.thumbnail)
    return embed


class GuildMusicState:
    def __init__(self, bot: "MusicBot", guild_id: int):
        self.bot = bot
        self.guild_id = guild_id
        self.queue: Deque[Track] = deque()
        self.current: Track | None = None
        self.voice: discord.VoiceClient | None = None
        self.text_channel: discord.abc.Messageable | None = None
        self.control_message: discord.Message | None = None
        self.volume = 0.7
        self._lock = asyncio.Lock()
        self._idle_disconnect_task: asyncio.Task[None] | None = None
        self._generation = 0
        self._skip_requested = False

    async def connect_to_user_channel(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None:
            raise MusicError("서버 안에서만 사용할 수 있어요.")

        voice_state = getattr(interaction.user, "voice", None)
        voice_channel = getattr(voice_state, "channel", None)
        if voice_channel is None:
            raise MusicError("먼저 음성 채널에 들어가 주세요.")

        voice_client = interaction.guild.voice_client
        if voice_client is None:
            self.voice = await voice_channel.connect()
            return

        if voice_client.channel != voice_channel:
            await voice_client.move_to(voice_channel)

        self.voice = voice_client

    async def enqueue(
        self,
        track: Track,
        text_channel: discord.abc.Messageable | None,
    ) -> int:
        async with self._lock:
            self._cancel_idle_disconnect()
            self._set_text_channel_locked(text_channel)
            self.queue.append(track)
            position = len(self.queue)

            if not self._is_playing_or_paused():
                await self._play_next_locked()
                if self.current is track:
                    return 0

            return position

    async def skip(self) -> bool:
        async with self._lock:
            if not self._is_playing_or_paused() or self.voice is None:
                return False
            self._skip_requested = True
            self.voice.stop()
            return True

    async def pause(self) -> bool:
        async with self._lock:
            if self.voice is None or not self.voice.is_playing():
                return False
            self.voice.pause()
            return True

    async def resume(self) -> bool:
        async with self._lock:
            if self.voice is None or not self.voice.is_paused():
                return False
            self.voice.resume()
            return True

    async def set_volume(
        self,
        percent: int,
        text_channel: discord.abc.Messageable | None = None,
    ) -> int:
        if percent < 0 or percent > 100:
            raise MusicError("볼륨은 0부터 100 사이로 설정할 수 있어요.")

        async with self._lock:
            self._set_text_channel_locked(text_channel)
            self.volume = percent / 100

            if self.voice and isinstance(self.voice.source, discord.PCMVolumeTransformer):
                self.voice.source.volume = self.volume

            return self.volume_percent

    @property
    def volume_percent(self) -> int:
        return round(self.volume * 100)

    async def stop_and_leave(self) -> None:
        async with self._lock:
            self._generation += 1
            self.queue.clear()
            self.current = None
            self._skip_requested = False
            self._cancel_idle_disconnect()

            voice = self.voice
            self.voice = None
            if voice is None:
                return

            if voice.is_playing() or voice.is_paused():
                voice.stop()
            if voice.is_connected():
                await voice.disconnect(force=True)

    async def refresh_control_panel(
        self,
        text_channel: discord.abc.Messageable | None = None,
    ) -> None:
        async with self._lock:
            self._set_text_channel_locked(text_channel)
            await self._refresh_control_panel_locked()

    async def remember_control_panel_message(
        self,
        interaction: discord.Interaction,
    ) -> None:
        async with self._lock:
            self._set_text_channel_locked(interaction.channel)
            if isinstance(interaction.message, discord.Message):
                self.control_message = interaction.message

    async def queue_embed(self) -> discord.Embed:
        async with self._lock:
            embed = discord.Embed(title="대기열", color=discord.Color.green())

            if self.current is None and not self.queue:
                embed.description = "대기 중인 곡이 없어요."
                return embed

            if self.current is not None:
                embed.add_field(
                    name="지금 재생",
                    value=(
                        f"[{self.current.title}]({self.current.webpage_url}) "
                        f"({format_duration(self.current.duration)})"
                    ),
                    inline=False,
                )

            if self.queue:
                lines = []
                for index, track in enumerate(list(self.queue)[:10], start=1):
                    lines.append(
                        f"{index}. [{track.title}]({track.webpage_url}) "
                        f"({format_duration(track.duration)})"
                    )
                if len(self.queue) > 10:
                    lines.append(f"...그리고 {len(self.queue) - 10}곡 더")
                embed.add_field(name="다음 곡", value="\n".join(lines), inline=False)

            return embed

    async def now_playing_embed(self) -> discord.Embed | None:
        async with self._lock:
            if self.current is None:
                return None
            return track_embed("지금 재생 중", self.current, discord.Color.blurple())

    async def player_embed(self) -> discord.Embed:
        async with self._lock:
            return self._player_embed_locked()

    def _set_text_channel_locked(
        self,
        text_channel: discord.abc.Messageable | None,
    ) -> None:
        if text_channel is None:
            return

        if (
            self.control_message is not None
            and getattr(self.control_message.channel, "id", None) != getattr(text_channel, "id", None)
        ):
            self.control_message = None

        self.text_channel = text_channel

    def _player_embed_locked(self) -> discord.Embed:
        embed = discord.Embed(title="음악 플레이어", color=discord.Color.blurple())

        if self.voice and self.voice.is_paused():
            status = "일시정지"
        elif self.voice and self.voice.is_playing():
            status = "재생 중"
        else:
            status = "대기 중"

        if self.current is None:
            embed.description = (
                "**현재곡**\n\n"
                "재생 중인 곡이 없어요.\n\n"
                "`재생` 버튼으로 검색어 또는 URL을 입력해 주세요.\n\n"
                f"볼륨: `{self.volume_percent}%`"
            )
            if not self.queue and self.bot.user is not None:
                embed.set_thumbnail(url=self.bot.user.display_avatar.url)
        else:
            embed.description = (
                "**현재곡**\n\n"
                f"[{self.current.title}]({self.current.webpage_url})\n\n"
                f"길이: `{format_duration(self.current.duration)}`\n"
                f"요청: {self.current.requester_mention}\n"
                f"상태: `{status}`\n"
                f"볼륨: `{self.volume_percent}%`"
            )
            if self.current.thumbnail:
                embed.set_image(url=self.current.thumbnail)

        if self.queue:
            lines = []
            for index, track in enumerate(list(self.queue)[:5], start=1):
                lines.append(
                    f"{index}. [{track.title}]({track.webpage_url}) "
                    f"({format_duration(track.duration)})"
                )
            if len(self.queue) > 5:
                lines.append(f"...그리고 {len(self.queue) - 5}곡 더")
            queue_text = "\n".join(lines)
        else:
            queue_text = "대기 중인 곡이 없어요."

        embed.add_field(name="\u200b", value="\u200b", inline=False)
        embed.add_field(name="대기열", value=queue_text, inline=False)
        embed.set_footer(text="곡 신청, 대기열 확인, /현재곡 실행 때 패널이 갱신됩니다.")
        return embed

    async def _refresh_control_panel_locked(self) -> None:
        if self.text_channel is None and self.control_message is None:
            return

        embed = self._player_embed_locked()
        try:
            if self.control_message is None:
                if self.text_channel is None:
                    return
                self.control_message = await self.text_channel.send(
                    embed=embed,
                    view=MusicControlsView(),
                )
                return

            await self.control_message.edit(embed=embed, view=MusicControlsView())
        except discord.NotFound:
            self.control_message = None
            if self.text_channel is not None:
                self.control_message = await self.text_channel.send(
                    embed=embed,
                    view=MusicControlsView(),
                )
        except discord.DiscordException:
            LOGGER.exception("Failed to refresh music control panel")

    def _is_playing_or_paused(self) -> bool:
        return bool(self.voice and (self.voice.is_playing() or self.voice.is_paused()))

    async def _play_next_locked(
        self,
        *,
        autoplay_seed: Track | None = None,
        refresh_panel: bool = False,
    ) -> None:
        self.current = None

        if not self.queue and autoplay_seed is not None:
            try:
                autoplay_track = await build_autoplay_track(autoplay_seed, self.bot.user)
            except Exception:
                LOGGER.exception("Failed to load autoplay track")
                autoplay_track = None

            if autoplay_track is not None:
                self.queue.append(autoplay_track)

        while self.queue:
            track = self.queue.popleft()
            self.current = track

            try:
                stream_url = await resolve_stream_url(track)
            except Exception as exc:
                LOGGER.warning("Failed to resolve stream for %s: %s", track.webpage_url, exc)
                await self._send_text(f"`{track.title}` 재생 준비에 실패해서 건너뛰었어요.")
                self.current = None
                continue

            if self.voice is None or not self.voice.is_connected():
                await self._send_text("음성 채널 연결이 끊어져서 재생을 멈췄어요.")
                self.current = None
                self.queue.clear()
                return

            audio = discord.PCMVolumeTransformer(
                discord.FFmpegPCMAudio(
                    stream_url,
                    executable=FFMPEG_EXECUTABLE,
                    before_options=FFMPEG_BEFORE_OPTIONS,
                    options=FFMPEG_OPTIONS,
                ),
                volume=self.volume,
            )

            loop = asyncio.get_running_loop()
            generation = self._generation

            def after_play(error: Exception | None) -> None:
                loop.call_soon_threadsafe(
                    lambda: asyncio.create_task(self._after_track(error, generation))
                )

            self.voice.play(audio, after=after_play)
            await self._send_now_playing(track)
            if refresh_panel:
                await self._refresh_control_panel_locked()
            return

        self._schedule_idle_disconnect_locked()

    async def _after_track(self, error: Exception | None, generation: int) -> None:
        if error:
            LOGGER.warning("Voice player error: %s", error)
            await self._send_text("재생 중 오류가 발생했어요. 다음 곡으로 넘어갑니다.")

        async with self._lock:
            if generation != self._generation:
                return

            finished_track = self.current
            skip_requested = self._skip_requested
            self._skip_requested = False
            autoplay_seed = None if error or skip_requested else finished_track

            await self._play_next_locked(
                autoplay_seed=autoplay_seed,
                refresh_panel=True,
            )

    async def _send_now_playing(self, track: Track) -> None:
        return

    async def _send_text(
        self,
        content: str | None = None,
        *,
        embed: discord.Embed | None = None,
    ) -> None:
        if self.text_channel is None:
            return

        try:
            await self.text_channel.send(content=content, embed=embed)
        except discord.DiscordException:
            LOGGER.exception("Failed to send music status message")

    def _schedule_idle_disconnect_locked(self) -> None:
        self._cancel_idle_disconnect()
        self._idle_disconnect_task = asyncio.create_task(self._idle_disconnect())

    def _cancel_idle_disconnect(self) -> None:
        if self._idle_disconnect_task and not self._idle_disconnect_task.done():
            self._idle_disconnect_task.cancel()
        self._idle_disconnect_task = None

    async def _idle_disconnect(self) -> None:
        try:
            await asyncio.sleep(300)
            async with self._lock:
                if self.queue or self._is_playing_or_paused() or self.voice is None:
                    return
                voice = self.voice
                self.voice = None
                self.current = None
                if voice.is_connected():
                    await voice.disconnect(force=True)
        except asyncio.CancelledError:
            return


class MusicBot(commands.Bot):
    def __init__(self) -> None:
        intents = discord.Intents.default()
        super().__init__(command_prefix=commands.when_mentioned, intents=intents)
        self.states: dict[int, GuildMusicState] = {}
        self.controls_view: discord.ui.View | None = None

    async def setup_hook(self) -> None:
        self.controls_view = MusicControlsView()
        self.add_view(self.controls_view)
        LOGGER.info("Registered music control buttons")

        if DISCORD_GUILD_ID:
            try:
                guild = discord.Object(id=int(DISCORD_GUILD_ID))
            except ValueError:
                LOGGER.warning("DISCORD_GUILD_ID must be a number; syncing commands globally")
                synced = await self.tree.sync()
            else:
                self.tree.copy_global_to(guild=guild)
                synced = await self.tree.sync(guild=guild)
                LOGGER.info("Synced %s command(s) to guild %s", len(synced), DISCORD_GUILD_ID)
                return
        else:
            synced = await self.tree.sync()

        LOGGER.info("Synced %s global command(s)", len(synced))

    async def on_ready(self) -> None:
        assert self.user is not None
        LOGGER.info("Logged in as %s (%s)", self.user, self.user.id)

    def music_state(self, guild_id: int) -> GuildMusicState:
        state = self.states.get(guild_id)
        if state is None:
            state = GuildMusicState(self, guild_id)
            self.states[guild_id] = state
        return state


bot = MusicBot()


def get_state(interaction: discord.Interaction) -> GuildMusicState:
    if interaction.guild_id is None:
        raise MusicError("서버 안에서만 사용할 수 있어요.")
    return bot.music_state(interaction.guild_id)


async def enqueue_from_interaction(
    interaction: discord.Interaction,
    query: str,
) -> tuple[GuildMusicState, Track, int]:
    if interaction.channel is None:
        raise MusicError("명령을 실행한 채널을 찾지 못했어요.")

    state = get_state(interaction)
    await state.connect_to_user_channel(interaction)
    track = await build_track(query, interaction.user)
    position = await state.enqueue(track, interaction.channel)
    await state.refresh_control_panel(interaction.channel)
    return state, track, position


def enqueue_result_message(track: Track, position: int) -> str:
    if position == 0:
        return f"`{track.title}` 재생을 시작했어요."
    return f"`{track.title}` 대기열 {position}번에 추가했어요."


class PlayQueryModal(discord.ui.Modal, title="음악 재생"):
    query = discord.ui.TextInput(
        label="검색어 또는 YouTube URL",
        placeholder="예: 아이유 좋은날",
        max_length=200,
    )

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(thinking=True, ephemeral=True)

        try:
            _state, track, position = await enqueue_from_interaction(
                interaction,
                str(self.query.value),
            )
        except MusicError as exc:
            await interaction.followup.send(str(exc), ephemeral=True)
            return
        except Exception:
            LOGGER.exception("Unexpected play modal failure")
            await interaction.followup.send("곡을 불러오는 중 오류가 발생했어요.", ephemeral=True)
            return

        await interaction.followup.send(enqueue_result_message(track, position), ephemeral=True)


class VolumeModal(discord.ui.Modal, title="볼륨 설정"):
    percent = discord.ui.TextInput(
        label="볼륨",
        placeholder="0~100 사이 숫자",
        default="70",
        max_length=3,
    )

    def __init__(self, current_percent: int = 70) -> None:
        super().__init__()
        self.percent.default = str(current_percent)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(thinking=True, ephemeral=True)

        try:
            volume = parse_volume_percent(str(self.percent.value))
            state = get_state(interaction)
            volume = await state.set_volume(volume, interaction.channel)
        except MusicError as exc:
            await interaction.followup.send(str(exc), ephemeral=True)
            return
        except Exception:
            LOGGER.exception("Unexpected volume modal failure")
            await interaction.followup.send("볼륨을 변경하는 중 오류가 발생했어요.", ephemeral=True)
            return

        await interaction.followup.send(f"볼륨을 `{volume}%`로 설정했어요.", ephemeral=True)


async def send_interaction_error(
    interaction: discord.Interaction,
    message: str = "버튼 처리 중 오류가 발생했어요. 콘솔 로그를 확인해 주세요.",
) -> None:
    try:
        if interaction.response.is_done():
            await interaction.followup.send(message, ephemeral=True)
        else:
            await interaction.response.send_message(message, ephemeral=True)
    except discord.DiscordException:
        LOGGER.exception("Failed to send interaction error message")


class MusicControlsView(discord.ui.View):
    def __init__(self) -> None:
        super().__init__(timeout=None)

    async def on_error(
        self,
        interaction: discord.Interaction,
        error: Exception,
        item: discord.ui.Item,
    ) -> None:
        LOGGER.error(
            "Music control failed for %s",
            item,
            exc_info=(type(error), error, error.__traceback__),
        )
        await send_interaction_error(interaction)

    @discord.ui.button(
        label="재생",
        style=discord.ButtonStyle.success,
        custom_id="music-controls:play",
    )
    async def play_button(
        self,
        interaction: discord.Interaction,
        _button: discord.ui.Button,
    ) -> None:
        try:
            state = get_state(interaction)
            await state.remember_control_panel_message(interaction)
            await interaction.response.send_modal(PlayQueryModal())
        except MusicError as exc:
            await interaction.response.send_message(str(exc), ephemeral=True)
        except Exception as exc:
            await self.on_error(interaction, exc, _button)

    @discord.ui.button(
        label="대기열",
        style=discord.ButtonStyle.primary,
        custom_id="music-controls:queue",
    )
    async def queue_button(
        self,
        interaction: discord.Interaction,
        _button: discord.ui.Button,
    ) -> None:
        await interaction.response.defer(thinking=True, ephemeral=True)

        try:
            state = get_state(interaction)
            await state.remember_control_panel_message(interaction)
            await state.refresh_control_panel(interaction.channel)
            embed = await state.queue_embed()
        except MusicError as exc:
            await interaction.followup.send(str(exc), ephemeral=True)
            return
        except Exception as exc:
            await self.on_error(interaction, exc, _button)
            return

        await interaction.followup.send(embed=embed, ephemeral=True)

    @discord.ui.button(
        label="건너뛰기",
        style=discord.ButtonStyle.secondary,
        custom_id="music-controls:skip",
    )
    async def skip_button(
        self,
        interaction: discord.Interaction,
        _button: discord.ui.Button,
    ) -> None:
        await interaction.response.defer(thinking=True, ephemeral=True)

        try:
            state = get_state(interaction)
            await state.remember_control_panel_message(interaction)
            skipped = await state.skip()
        except MusicError as exc:
            await interaction.followup.send(str(exc), ephemeral=True)
            return
        except Exception as exc:
            await self.on_error(interaction, exc, _button)
            return

        message = "현재 곡을 건너뛰었어요." if skipped else "건너뛸 곡이 없어요."
        await interaction.followup.send(message, ephemeral=True)

    @discord.ui.button(
        label="일시정지",
        style=discord.ButtonStyle.secondary,
        custom_id="music-controls:pause",
    )
    async def pause_button(
        self,
        interaction: discord.Interaction,
        _button: discord.ui.Button,
    ) -> None:
        await interaction.response.defer(thinking=True, ephemeral=True)

        try:
            state = get_state(interaction)
            await state.remember_control_panel_message(interaction)
            paused = await state.pause()
            if paused:
                message = "일시정지했어요."
            else:
                resumed = await state.resume()
                message = "다시 재생합니다." if resumed else "일시정지할 곡이 없어요."
        except MusicError as exc:
            await interaction.followup.send(str(exc), ephemeral=True)
            return
        except Exception as exc:
            await self.on_error(interaction, exc, _button)
            return

        await interaction.followup.send(message, ephemeral=True)

    @discord.ui.button(
        label="정지",
        style=discord.ButtonStyle.danger,
        custom_id="music-controls:stop",
    )
    async def stop_button(
        self,
        interaction: discord.Interaction,
        _button: discord.ui.Button,
    ) -> None:
        await interaction.response.defer(thinking=True, ephemeral=True)

        try:
            state = get_state(interaction)
            await state.remember_control_panel_message(interaction)
            await state.stop_and_leave()
        except MusicError as exc:
            await interaction.followup.send(str(exc), ephemeral=True)
            return
        except Exception as exc:
            await self.on_error(interaction, exc, _button)
            return

        await interaction.followup.send("재생을 멈추고 음성 채널에서 나갔어요.", ephemeral=True)

    @discord.ui.button(
        label="볼륨",
        style=discord.ButtonStyle.primary,
        custom_id="music-controls:volume",
        row=1,
    )
    async def volume_button(
        self,
        interaction: discord.Interaction,
        _button: discord.ui.Button,
    ) -> None:
        try:
            state = get_state(interaction)
            await state.remember_control_panel_message(interaction)
            await interaction.response.send_modal(VolumeModal(state.volume_percent))
        except MusicError as exc:
            await interaction.response.send_message(str(exc), ephemeral=True)
        except Exception as exc:
            await self.on_error(interaction, exc, _button)


@bot.tree.command(name="재생", description="검색어나 URL을 대기열에 추가하고 재생합니다.")
@app_commands.rename(query="검색어")
@app_commands.describe(query="YouTube URL 또는 검색어")
async def play(interaction: discord.Interaction, query: str) -> None:
    await interaction.response.defer(thinking=True, ephemeral=True)

    try:
        _state, track, position = await enqueue_from_interaction(interaction, query)
    except MusicError as exc:
        await interaction.followup.send(str(exc), ephemeral=True)
        return
    except Exception:
        LOGGER.exception("Unexpected play command failure")
        await interaction.followup.send("곡을 불러오는 중 오류가 발생했어요.", ephemeral=True)
        return

    await interaction.followup.send(enqueue_result_message(track, position), ephemeral=True)


@bot.tree.command(name="건너뛰기", description="현재 곡을 건너뜁니다.")
async def skip(interaction: discord.Interaction) -> None:
    state = get_state(interaction)
    skipped = await state.skip()
    message = "현재 곡을 건너뛰었어요." if skipped else "건너뛸 곡이 없어요."
    await interaction.response.send_message(message)


@bot.tree.command(name="일시정지", description="현재 곡을 일시정지합니다.")
async def pause(interaction: discord.Interaction) -> None:
    state = get_state(interaction)
    paused = await state.pause()
    message = "일시정지했어요." if paused else "일시정지할 곡이 없어요."
    await interaction.response.send_message(message)


@bot.tree.command(name="다시재생", description="일시정지한 곡을 다시 재생합니다.")
async def resume(interaction: discord.Interaction) -> None:
    state = get_state(interaction)
    resumed = await state.resume()
    message = "다시 재생합니다." if resumed else "다시 재생할 곡이 없어요."
    await interaction.response.send_message(message)


@bot.tree.command(name="볼륨", description="음악봇 볼륨을 0부터 100 사이로 설정합니다.")
@app_commands.rename(percent="크기")
@app_commands.describe(percent="0부터 100 사이 볼륨")
async def volume(
    interaction: discord.Interaction,
    percent: app_commands.Range[int, 0, 100],
) -> None:
    state = get_state(interaction)
    volume_percent = await state.set_volume(percent, interaction.channel)
    await interaction.response.send_message(f"볼륨을 `{volume_percent}%`로 설정했어요.")


@bot.tree.command(name="정지", description="대기열을 비우고 음성 채널에서 나갑니다.")
async def stop(interaction: discord.Interaction) -> None:
    state = get_state(interaction)
    await state.stop_and_leave()
    await interaction.response.send_message("재생을 멈추고 음성 채널에서 나갔어요.")


@bot.tree.command(name="나가기", description="음성 채널에서 나갑니다.")
async def leave(interaction: discord.Interaction) -> None:
    state = get_state(interaction)
    await state.stop_and_leave()
    await interaction.response.send_message("음성 채널에서 나갔어요.")


@bot.tree.command(name="대기열", description="현재 대기열을 보여줍니다.")
async def show_queue(interaction: discord.Interaction) -> None:
    state = get_state(interaction)
    await state.refresh_control_panel(interaction.channel)
    embed = await state.queue_embed()
    await interaction.response.send_message(embed=embed)


@bot.tree.command(name="현재곡", description="음악 플레이어 패널을 보여주거나 갱신합니다.")
async def now_playing(interaction: discord.Interaction) -> None:
    state = get_state(interaction)
    await state.refresh_control_panel(interaction.channel)
    await interaction.response.send_message("음악 플레이어 패널을 갱신했어요.", ephemeral=True)


@bot.tree.command(name="도움말", description="음악봇 명령어를 보여줍니다.")
async def help_command(interaction: discord.Interaction) -> None:
    embed = discord.Embed(title="명령어", color=discord.Color.blurple())
    embed.add_field(name="/재생", value="검색어나 URL을 재생 대기열에 추가합니다.", inline=False)
    embed.add_field(name="/건너뛰기", value="현재 곡을 건너뜁니다.", inline=False)
    embed.add_field(name="/일시정지, /다시재생", value="재생을 일시정지하거나 다시 시작합니다.", inline=False)
    embed.add_field(name="/볼륨", value="음악봇 볼륨을 0부터 100 사이로 설정합니다.", inline=False)
    embed.add_field(name="/대기열", value="대기열을 확인합니다.", inline=False)
    embed.add_field(name="/현재곡", value="현재 곡을 확인합니다.", inline=False)
    embed.add_field(name="/정지, /나가기", value="재생을 멈추고 음성 채널에서 나갑니다.", inline=False)
    await interaction.response.send_message(embed=embed, ephemeral=True)


def main() -> None:
    if not DISCORD_TOKEN:
        raise RuntimeError("DISCORD_TOKEN is missing. Copy .env.example to .env and set it.")
    bot.run(DISCORD_TOKEN)


if __name__ == "__main__":
    main()
