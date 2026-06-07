#!/usr/bin/env bash
set -o errexit

echo "===== 환경 확인 ====="
python --version
pip --version
node --version
ffmpeg -version

echo "===== 파이썬 패키지 설치 시작 ====="
pip install --upgrade pip
pip install -r requirements.txt

echo "===== 설치 최종 확인 ====="
python -c "import yt_dlp; print('yt-dlp', yt_dlp.version.__version__)"
ffmpeg -version
