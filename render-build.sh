#!/usr/bin/env bash

apt-get update

curl -fsSL https://deb.nodesource.com/setup_22.x | bash -
apt-get install -y nodejs ffmpeg

echo "===== NODE ====="
node -v

echo "===== NPM ====="
npm -v

echo "===== FFMPEG ====="
ffmpeg -version

pip install -r requirements.txt