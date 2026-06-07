#!/usr/bin/env bash
# 에러 발생 시 빌드를 즉시 중단합니다.
set -o errexit

echo "===== 환경 확인 ====="
node -v
npm -v

echo "===== FFmpeg 수동 설치 시작 ====="
# 루트 권한 없이 실행할 수 있도록 압축된 static FFmpeg를 다운로드합니다.
mkdir -p ffmpeg_bin
cd ffmpeg_bin
curl -sL https://johnvansickle.com | tar -xJ --strip-components=1

# 압축 해제된 ffmpeg 실행 파일을 시스템 PATH 환경변수에 등록합니다.
export PATH="$PATH:$(pwd)"
cd ..

echo "===== 파이썬 패키지 설치 시작 ====="
pip install --upgrade pip
pip install -r requirements.txt

echo "===== 설치 최종 확인 ====="
ffmpeg -version
