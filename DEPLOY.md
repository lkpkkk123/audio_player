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

### requirements.txt contents:
```text
audioop-lts==0.2.2  # Critical for Python 3.13+
numpy==2.3.5
pydub==0.25.1
sounddevice==0.5.3
websockets==15.0.1
```

## 3. Important Notes for Embedded Environments

*   **Audio Permissions**: Ensure your user is in the `audio` group:
    `sudo usermod -aG audio $USER` (Then log out and back in).
*   **Web Microphone Access**: Modern browsers require **HTTPS** to access the microphone unless you are using `localhost`. If you access the board via IP, the "Talk" button may not work unless you:
    *   Deploy an SSL certificate.
    *   Or, in Chrome, go to `chrome://flags/#unsafely-treat-insecure-origin-as-secure` and add your board's IP.
