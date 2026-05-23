# Python Discord Music Bot

`discord.py`, `yt-dlp`, `ffmpeg`로 만든 슬래시 명령어 기반 음악봇입니다.

## 기능

- `/재생 검색어 또는 URL`: 음성 채널에 들어가서 음악 재생
- `/대기열`: 대기열 확인
- `/건너뛰기`: 현재 곡 건너뛰기
- `/일시정지`, `/다시재생`: 일시정지와 재개
- `/현재곡`: 음악 플레이어 패널 만들기 또는 갱신
- `/정지`, `/나가기`: 재생 중지 후 음성 채널 나가기
- 음악 플레이어 패널: 현재곡을 계속 보여주고 아래 버튼으로 `재생`, `대기열`, `건너뛰기`, `일시정지`, `정지` 조작

## 준비

1. Python 3.11 이상을 설치합니다.
2. ffmpeg를 설치하고 `ffmpeg -version`이 실행되는지 확인합니다.
3. Discord Developer Portal에서 애플리케이션과 봇을 만듭니다.
4. OAuth2 URL Generator에서 `bot`, `applications.commands` scope를 선택합니다.
5. 봇 권한은 최소 `View Channels`, `Send Messages`, `Connect`, `Speak`를 선택합니다.
6. 생성된 초대 URL로 봇을 서버에 초대합니다.

음악 재생 소스의 이용 약관과 저작권을 지키는 범위에서 사용하세요.

## 설치

PowerShell 기준:

```powershell
py -3 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -U pip
pip install -r requirements.txt
```

현재 PC에서 `py`가 "No installed Python found!"를 반환한다면 Python을 먼저 설치한 뒤 위 명령을 다시 실행하세요.

## 설정

`.env.example`을 `.env`로 복사하고 값을 채웁니다.

```env
DISCORD_TOKEN=your-token
DISCORD_GUILD_ID=your-test-server-id
```

`DISCORD_GUILD_ID`는 선택 사항이지만 개발 중에는 넣는 편이 좋습니다. 넣으면 슬래시 명령어가 해당 서버에 빠르게 동기화됩니다. 비워두면 전역 명령어로 등록되며 Discord 반영까지 시간이 걸릴 수 있습니다.

ffmpeg가 PATH에 없으면 `.env`에 직접 경로를 넣습니다.

```env
FFMPEG_EXECUTABLE=C:\ffmpeg\bin\ffmpeg.exe
```

## 실행

```powershell
python bot.py
```

봇과 같은 음성 채널에 들어간 뒤 `/재생 아이유 좋은날`처럼 실행하면 됩니다.
음악이 시작되면 채널에 플레이어 패널이 생기고, `재생` 버튼을 누르면 검색어 또는 URL 입력창이 뜹니다.
`/현재곡`을 실행하면 플레이어 패널을 다시 만들거나 갱신할 수 있습니다.

## Render 배포

이 프로젝트는 별도 Git repo로 올린 뒤 Render에서 Background Worker로 생성하면 됩니다.

- Build Command: `pip install -r requirements.txt`
- Start Command: `python bot.py`
- Environment Variable: `DISCORD_TOKEN`

`render.yaml`도 포함되어 있어서 Render Blueprint로 연결할 수 있습니다. 토큰은 파일에 넣지 말고 Render Dashboard의 Environment에서 입력하세요.
