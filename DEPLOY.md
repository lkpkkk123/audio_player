# Embedded Audio Player Deployment Guide

This document outlines the dependencies and system requirements needed to deploy the audio player on an embedded Linux board.

## 1. System Dependencies (Apt-get)

The Python libraries require certain system-level libraries for audio processing and compilation. Run the following on your target board:

```bash
sudo apt-get update
sudo apt-get install -y \
    python3-pip \
    python3-venv \
    libportaudio2 \
    libasound2-dev \
    ffmpeg
```

*   `libportaudio2`: Required by `sounddevice`.
*   `ffmpeg`: Required by `pydub` to decode MP3/OGG files.
*   `libasound2-dev`: Required if you need to recompile any audio extensions.

## 2. Python Requirements

I have generated a `requirements.txt` file in the project root. You can install all dependencies using:

```bash
# Create a virtual environment
python3 -m venv venv
source venv/bin/activate

# Install dependencies
pip install -r requirements.txt
```

### requirements.txt contents (Updated):
```text
numpy
pydub
sounddevice
websockets
# Only installs if Python is 3.13 or newer
audioop-lts; python_version >= '3.13'
```

## 3. Important Notes for Embedded Environments

*   **Audio Permissions**: Ensure your user is in the `audio` group:
    `sudo usermod -aG audio $USER` (Then log out and back in).
*   **Web Microphone Access**: Modern browsers require **HTTPS** to access the microphone unless you are using `localhost`. If you access the board via IP, the "Talk" button may not work unless you:
    *   Deploy an SSL certificate.
    *   Or, in Chrome, go to `chrome://flags/#unsafely-treat-insecure-origin-as-secure` and add your board's IP.

## 4. 安装命令
apt-get install -y     python3-pip     python3-venv     libportaudio2     libasound2-dev     ffmpeg
apt install rsync
apt-get install python3-numpy
apt-get install python3-pydub
apt-get install python3-sounddevice
apt-get install python3-websockets (可选，估计要用pip安装，用这个装上ws有问题)
apt-get install libportaudio2
apt-get install python3-cffi

pip3 install --upgrade websockets （apt装ws有问题，需要用这个命令升级后正常）
pip3 install sounddevice
pip3 install sounddevice

## 5. 浏览器麦克风音频采集设置
- 浏览器由于安全机制，默认之允许localhost访问麦克风，如果要访问ip地址，需要设置
- 在浏览器里输入 chrome://flags/#unsafely-treat-insecure-origin-as-secure
- 将选项改为已启用，添加音频服务板卡的ip地址如：http://192.168.8.237，然后重启浏览器