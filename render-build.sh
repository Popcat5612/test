#!/usr/bin/env bash

apt-get update
apt-get install -y nodejs npm ffmpeg

echo "===== NODE ====="
node -v

echo "===== NPM ====="
npm -v

echo "===== FFMPEG ====="
ffmpeg -version

pip install -r requirements.txt