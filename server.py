import asyncio
import json
import logging
import websockets
import numpy as np
import sounddevice as sd
from pydub import AudioSegment
import os
import queue
import threading
import http.server
import socketserver

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("AudioServer")

# Configuration
PORT = 8765
MEDIA_DIR = "media"
SAMPLE_RATE = 48000  # Many embedded/ALSA devices prefer 48k
CHANNELS = 2         

# Global state
mixer_queue = queue.Queue()
file_samples = None
file_index = 0
file_lock = threading.Lock()
test_tone_active = False # Set to True for debugging audio output
test_tone_frames = 0

def audio_callback(outdata, frames, time, status):
    global file_samples, file_index, test_tone_active, test_tone_frames
    
    if status:
        if not status.output_underflow:
            logger.warning(f"Audio status warning: {status}")
    
    # Initialize output data with zeros
    combined_data = np.zeros((frames, CHANNELS), dtype=np.float32)
    
    # Optional: Startup Test Tone (1kHz sine wave for 2 seconds)
    if test_tone_active:
        t = (np.arange(frames) + test_tone_frames) / SAMPLE_RATE
        # 1kHz tone, low volume (0.2)
        tone = 0.2 * np.sin(2 * np.pi * 1000 * t).astype(np.float32)
        # Broadcast to both channels
        combined_data[:, 0] += tone
        combined_data[:, 1] += tone
        test_tone_frames += frames
        if test_tone_frames > SAMPLE_RATE * 2: # Stop after 2 seconds
            test_tone_active = False
            logger.info("Startup test tone finished")

    # 1. Add real-time streaming data
    try:
        while not mixer_queue.empty(): # Drain if there's multiple chunks
            data = mixer_queue.get_nowait()
            data = data.flatten()
            chunk_len = min(len(data), frames)
            # Add to both channels
            combined_data[:chunk_len, 0] += data[:chunk_len]
            combined_data[:chunk_len, 1] += data[:chunk_len]
    except queue.Empty:
        pass

    # 2. Add file playback data
    with file_lock:
        if file_samples is not None:
            remaining = len(file_samples) - file_index
            if remaining > 0:
                chunk_size = min(remaining, frames)
                chunk = file_samples[file_index:file_index+chunk_size]
                combined_data[:chunk_size, 0] += chunk
                combined_data[:chunk_size, 1] += chunk
                file_index += chunk_size
            else:
                file_samples = None
                file_index = 0
    
    # Clip and output
    outdata[:] = np.clip(combined_data, -1.0, 1.0)

async def play_audio_file(file_path):
    """Load an audio file into the global mixer."""
    global file_samples, file_index
    try:
        if not os.path.exists(file_path):
            logger.error(f"File not found: {file_path}")
            return

        logger.info(f"Loading/Mixing file: {file_path}")
        audio = AudioSegment.from_file(file_path)
        # Convert to our playback settings
        audio = audio.set_frame_rate(SAMPLE_RATE).set_channels(1).set_sample_width(2)
        
        # Get samples as numpy array
        samples = np.array(audio.get_array_of_samples()).astype(np.float32) / 32768.0
        
        with file_lock:
            file_samples = samples
            file_index = 0
            
        logger.info(f"Playback triggered: {len(samples)} samples @ {SAMPLE_RATE}Hz")
    except Exception as e:
        logger.exception(f"Error loading file: {e}")

async def handle_client(websocket):
    logger.info(f"Client connected: {websocket.remote_address}")
    
    try:
        async for message in websocket:
            if isinstance(message, str):
                try:
                    data = json.loads(message)
                    cmd = data.get("type")
                    
                    if cmd == "play":
                        filename = data.get("file")
                        await play_audio_file(os.path.join(MEDIA_DIR, filename))
                    elif cmd == "stop":
                        with file_lock:
                            global file_samples
                            file_samples = None
                        while not mixer_queue.empty():
                            mixer_queue.get()
                        logger.info("Playback stopped")
                    elif cmd == "list":
                        files = [f for f in os.listdir(MEDIA_DIR) if os.path.isfile(os.path.join(MEDIA_DIR, f))]
                        await websocket.send(json.dumps({"type": "list", "files": files}))
                    elif cmd == "delete":
                        filename = data.get("file")
                        file_path = os.path.join(MEDIA_DIR, filename)
                        if os.path.exists(file_path):
                            os.remove(file_path)
                            logger.info(f"Deleted file: {filename}")
                            files = [f for f in os.listdir(MEDIA_DIR) if os.path.isfile(os.path.join(MEDIA_DIR, f))]
                            await websocket.send(json.dumps({"type": "list", "files": files}))
                    elif cmd == "upload":
                        filename = data.get("filename")
                        content_b64 = data.get("data")
                        import base64
                        try:
                            file_content = base64.b64decode(content_b64)
                            file_path = os.path.join(MEDIA_DIR, filename)
                            with open(file_path, "wb") as f:
                                f.write(file_content)
                            logger.info(f"Uploaded file: {filename}")
                            files = [f for f in os.listdir(MEDIA_DIR) if os.path.isfile(os.path.join(MEDIA_DIR, f))]
                            await websocket.send(json.dumps({"type": "list", "files": files}))
                        except Exception as e:
                            logger.error(f"Upload failed: {e}")
                
                except json.JSONDecodeError:
                    logger.error("Received invalid JSON")
            
            elif isinstance(message, bytes):
                audio_data = np.frombuffer(message, dtype=np.float32)
                if audio_data.size > 0:
                    # Put raw mono data into queue
                    mixer_queue.put(audio_data)

    except websockets.exceptions.ConnectionClosed:
        logger.info(f"Client disconnected: {websocket.remote_address}")

async def main():
    if not os.path.exists(MEDIA_DIR):
        os.makedirs(MEDIA_DIR)
    
    # --- Device Selection Logic ---
    devices = sd.query_devices()
    logger.info("--- Audio Devices List ---")
    logger.info(f"\n{devices}")
    
    target_device = None
    # Priority: 1. Manual override? (Not implemented) 
    # 2. Try to find "Analog" or "Speaker" or "pulse" 
    # 3. Fallback to default
    
    for i, dev in enumerate(devices):
        name = dev['name'].lower()
        if dev['max_output_channels'] > 0:
            if "analog" in name or "speaker" in name or "headphone" in name:
                target_device = i
                logger.info(f"Auto-selected Analog Device: {dev['name']} (Index: {i})")
                break
            elif "pulse" in name and target_device is None:
                target_device = i # Secondary choice
    
    if target_device is None:
        target_device = sd.default.device[1] # Use system default output
        logger.info(f"Using System Default Output: {devices[target_device]['name']} (Index: {target_device})")

    # Start HTTP server
    def run_http():
        public_dir = os.path.join(os.getcwd(), "public")
        class Handler(http.server.SimpleHTTPRequestHandler):
            def __init__(self, *args, **kwargs):
                super().__init__(*args, directory=public_dir, **kwargs)

        socketserver.TCPServer.allow_reuse_address = True
        with socketserver.TCPServer(("", 8000), Handler) as httpd:
            logger.info("Serving HTTP on port 8000")
            httpd.serve_forever()

    http_thread = threading.Thread(target=run_http, daemon=True)
    http_thread.start()
        
    # Start global audio stream on selected device
    global_stream = sd.OutputStream(
        device=target_device,
        samplerate=SAMPLE_RATE,
        channels=CHANNELS,
        dtype='float32',
        latency='high',
        callback=audio_callback
    )
    global_stream.start()
    logger.info(f"Global audio stream started on device {target_device}")

    logger.info(f"Starting WebSocket server on ws://0.0.0.0:{PORT}")
    async with websockets.serve(handle_client, "0.0.0.0", PORT):
        await asyncio.Future()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
