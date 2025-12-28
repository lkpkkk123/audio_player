import asyncio
import json
import logging
import websockets
import numpy as np
import sounddevice as sd
from pydub import AudioSegment
from pydub.utils import mediainfo
import os
import queue
import threading
import http.server
import socketserver

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("AudioServer")

# Configuration
PORT = 8888
MEDIA_DIR = "media"
METADATA_CACHE_FILE = os.path.join(MEDIA_DIR, "metadata.json")

SAMPLE_RATE = 48000  # Many embedded/ALSA devices prefer 48k
CHANNELS = 2         

import base64

# Global state
mixer_queue = queue.Queue()
residual_audio_buffer = np.array([], dtype=np.float32)
file_samples = None
file_index = 0
file_lock = threading.Lock()
test_tone_active = False # Set to True for debugging audio output
test_tone_frames = 0
active_uploads = {} # Store {filename: [chunks]}

def load_metadata_cache():
    if os.path.exists(METADATA_CACHE_FILE):
        try:
            with open(METADATA_CACHE_FILE, 'r') as f:
                return json.load(f)
        except Exception as e:
            logger.error(f"Error loading metadata cache: {e}")
    return {}

def save_metadata_cache(cache):
    try:
        # Ensure directory exists before saving
        os.makedirs(os.path.dirname(METADATA_CACHE_FILE), exist_ok=True)
        with open(METADATA_CACHE_FILE, 'w') as f:
            json.dump(cache, f, indent=4)
    except Exception as e:
        logger.error(f"Error saving metadata cache: {e}")

def get_file_duration_sync(path):
    """Sync helper to get mediainfo duration."""
    try:
        info = mediainfo(path)
        return float(info.get('duration', 0.0))
    except Exception:
        return 0.0

# Playback State
VOLUME = 1.0
PAUSED = False
connected_clients = set()
event_queue = queue.Queue()

# Debug counters
callback_count = 0

def audio_callback(outdata, frames, time, status):
    global file_samples, file_index, test_tone_active, test_tone_frames, callback_count, residual_audio_buffer
    
    callback_count += 1
    
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
    samples_needed = frames
    stream_output = np.zeros(frames, dtype=np.float32)
    samples_filled = 0
    
    # 1a. Fill from residual buffer first
    if len(residual_audio_buffer) > 0:
        take = min(len(residual_audio_buffer), samples_needed)
        stream_output[:take] = residual_audio_buffer[:take]
        residual_audio_buffer = residual_audio_buffer[take:]
        samples_filled += take
        samples_needed -= take

    # 1b. Fill from queue if still needed
    try:
        while samples_needed > 0 and not mixer_queue.empty():
            data = mixer_queue.get_nowait()
            data = data.flatten()
            
            # Remove NaNs
            if np.any(np.isnan(data)):
                data = np.nan_to_num(data)
                
            take = min(len(data), samples_needed)
            stream_output[samples_filled:samples_filled+take] = data[:take]
            
            # If chunk was bigger than needed, save the rest
            if len(data) > take:
                residual_audio_buffer = np.concatenate((residual_audio_buffer, data[take:]))
            
            samples_filled += take
            samples_needed -= take
            
            # Debug: Log peak volume periodically
            if callback_count % 100 == 1:
                abs_max = np.max(np.abs(data))
                if abs_max > 0.001:
                    logger.info(f"[MIXER] Signal peak: {abs_max:.4f}, Buffer usage: {samples_filled}/{frames}, Residual: {len(residual_audio_buffer)}")
    except queue.Empty:
        pass
    
    # Mono to Stereo
    combined_data[:, 0] += stream_output
    combined_data[:, 1] += stream_output

    # 2. Add file playback data
    with file_lock:
        if file_samples is not None and not PAUSED:
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
                logger.info("File playback reached the end. Triggering 'ended' event.")
                event_queue.put({"type": "ended"})
    
    # Apply Volume
    combined_data *= VOLUME

    # Clip and output
    outdata[:] = np.clip(combined_data, -1.0, 1.0)

async def play_audio_file(file_path):
    """Load an audio file into the global mixer."""
    global file_samples, file_index, PAUSED
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
            PAUSED = False
            
        logger.info(f"Playback triggered: {len(samples)} samples @ {SAMPLE_RATE}Hz")
    except Exception as e:
        logger.exception(f"Error loading file: {e}")

async def play_pcm_file(file_path):
    """Load a raw PCM s16le mono file (48k) into the global mixer."""
    global file_samples, file_index, PAUSED
    try:
        if not os.path.exists(file_path):
            logger.error(f"File not found: {file_path}")
            return

        logger.info(f"Loading raw PCM: {file_path}")
        # Read raw binary data
        with open(file_path, "rb") as f:
            raw_data = f.read()
        
        # Convert s16le (Signed 16-bit Little Endian) to float32
        samples = np.frombuffer(raw_data, dtype=np.int16).astype(np.float32) / 32768.0
        
        with file_lock:
            file_samples = samples
            file_index = 0
            PAUSED = False
            
        logger.info(f"PCM Playback triggered: {len(samples)} samples @ {SAMPLE_RATE}Hz")
    except Exception as e:
        logger.exception(f"Error loading PCM file: {e}")

async def get_file_list():
    """Helper to generate a list of file dictionaries with metadata, using a cache for duration."""
    if not os.path.exists(MEDIA_DIR):
        return []
    
    cache = load_metadata_cache()
    files_data = []
    # Ignore the metadata file itself
    valid_files = sorted([f for f in os.listdir(MEDIA_DIR) 
                         if os.path.isfile(os.path.join(MEDIA_DIR, f)) and f != os.path.basename(METADATA_CACHE_FILE)])
    
    cache_updated = False
    current_cache_keys = set()
    
    for f in valid_files:
        path = os.path.join(MEDIA_DIR, f)
        try:
            stat = os.stat(path)
            size = stat.st_size
            mtime = stat.st_mtime
        except Exception:
            continue
            
        # Use filename + size + mtime as key to detect changes
        cache_key = f"{f}_{size}_{mtime}"
        current_cache_keys.add(cache_key)
        
        duration = 0.0
        if cache_key in cache:
            duration = cache[cache_key].get("duration", 0.0)
        else:
            if f.lower().endswith(('.mp3', '.wav', '.ogg', '.flac')):
                logger.info(f"Cache miss for {f}, fetching mediainfo")
                # Use to_thread if python 3.9+, else run_in_executor
                try:
                    duration = await asyncio.to_thread(get_file_duration_sync, path)
                except AttributeError:
                    duration = await asyncio.get_event_loop().run_in_executor(None, get_file_duration_sync, path)
            
            cache[cache_key] = {
                "duration": duration,
                "name": f,
                "size": size,
                "mtime": mtime
            }
            cache_updated = True
            
        files_data.append({
            "name": f,
            "size": size,
            "mtime": mtime,
            "duration": duration
        })
        
    # Cleanup old entries from cache that are no longer present
    original_cache_size = len(cache)
    cache = {k: v for k, v in cache.items() if k in current_cache_keys}
    if len(cache) != original_cache_size:
        cache_updated = True
        
    if cache_updated:
        save_metadata_cache(cache)
        
    return files_data

async def handle_client(websocket):
    global VOLUME, PAUSED
    logger.info(f"Handshake started with: {websocket.remote_address}")
    # websocket.remote_address is available after handshake
    connected_clients.add(websocket)
    try:
        async for message in websocket:
            if isinstance(message, str):
                try:
                    data = json.loads(message)
                    cmd = data.get("type")
                    
                    if cmd == "play":
                        filename = data.get("file")
                        # Auto-detect .pcm extension
                        if filename.lower().endswith('.pcm'):
                            await play_pcm_file(os.path.join(MEDIA_DIR, filename))
                        else:
                            await play_audio_file(os.path.join(MEDIA_DIR, filename))
                    elif cmd == "play_pcm":
                        filename = data.get("file")
                        await play_pcm_file(os.path.join(MEDIA_DIR, filename))
                    elif cmd == "stop":
                        with file_lock:
                            global file_samples
                            file_samples = None
                            PAUSED = False
                        while not mixer_queue.empty():
                            mixer_queue.get()
                        logger.info("Playback stopped")
                    elif cmd == "list":
                        files_data = await get_file_list()
                        await websocket.send(json.dumps({"type": "list", "files": files_data}))
                    elif cmd == "set_volume":
                        vol = float(data.get("volume", 1.0))
                        VOLUME = max(0.0, min(1.0, vol))
                    elif cmd == "pause":
                        PAUSED = True
                    elif cmd == "resume":
                        PAUSED = False
                    elif cmd == "delete":
                        filename = data.get("file")
                        file_path = os.path.join(MEDIA_DIR, filename)
                        if os.path.exists(file_path):
                            os.remove(file_path)
                            logger.info(f"Deleted file: {filename}, broadcasting new list")
                            files_data = await get_file_list()
                            event_queue.put({"type": "list", "files": files_data})
                    elif cmd == "upload_start":
                        filename = data.get("filename")
                        active_uploads[filename] = []
                        logger.info(f"Started chunked upload for: {filename}")
                    
                    elif cmd == "upload_chunk":
                        filename = data.get("filename")
                        chunk_b64 = data.get("data")
                        if filename in active_uploads:
                            active_uploads[filename].append(base64.b64decode(chunk_b64))
                    
                    elif cmd == "upload_end":
                        filename = data.get("filename")
                        if filename in active_uploads:
                            file_content = b"".join(active_uploads[filename])
                            file_path = os.path.join(MEDIA_DIR, filename)
                            with open(file_path, "wb") as f:
                                f.write(file_content)
                            del active_uploads[filename]
                            logger.info(f"Completed upload: {filename}, broadcasting new list")
                            files_data = await get_file_list()
                            event_queue.put({"type": "list", "files": files_data})
                
                except json.JSONDecodeError:
                    logger.error("Received invalid JSON")
            
            elif isinstance(message, bytes):
                audio_data = np.frombuffer(message, dtype=np.float32)
                if audio_data.size > 0:
                    # Put raw mono data into queue
                    #logger.info(f"Received {audio_data.size} samples")
                    mixer_queue.put(audio_data)

    except websockets.exceptions.ConnectionClosed:
        logger.info(f"Client disconnected: {websocket.remote_address}")
    finally:
        connected_clients.remove(websocket)

async def broadcast_events():
    while True:
        try:
            # Non-blocking get from queue
            event = event_queue.get_nowait()
            if connected_clients:
                message = json.dumps(event)
                to_remove = set()
                for ws in connected_clients:
                    try:
                        await ws.send(message)
                    except websockets.exceptions.ConnectionClosed:
                        to_remove.add(ws)
                    except Exception as e:
                        logger.error(f"Error broadcasting to client: {e}")
                
                connected_clients.difference_update(to_remove)
        except queue.Empty:
            await asyncio.sleep(0.1)
        except Exception as e:
            logger.error(f"Error in broadcast loop: {e}")
            await asyncio.sleep(0.1)

async def main():
    if not os.path.exists(MEDIA_DIR):
        os.makedirs(MEDIA_DIR)
    
    os.system("amixer -c0 cset name='OUT1 Switch' 1")# 执行这个命令后，音频输出口才能有输出
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
        socketserver.ThreadingTCPServer.allow_reuse_address = True
        try:
            with socketserver.ThreadingTCPServer(("", 80), Handler) as httpd:
                logger.info(f"Serving HTTP on port 80 (dir: {public_dir})")
                httpd.serve_forever()
        except Exception as e:
            logger.error(f"HTTP Server error: {e}")

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
    
    # Start broadcast loop
    asyncio.create_task(broadcast_events())
    
    try:
        async with websockets.serve(handle_client, "0.0.0.0", PORT):
            logger.info("WebSocket server is running and waiting for connections")
            await asyncio.Future()
    except Exception as e:
        logger.error(f"Failed to start WebSocket server: {e}")

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
