import wave
import math
import struct
import os

def create_test_wav(filename, duration=2.0, freq=440.0):
    sample_rate = 44100.0
    num_samples = int(duration * sample_rate)
    
    with wave.open(filename, 'w') as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(sample_rate)
        
        for i in range(num_samples):
            value = int(32767.0 * math.sin(2.0 * math.pi * freq * (i / sample_rate)))
            data = struct.pack('<h', value)
            wav_file.writeframesraw(data)

os.makedirs("media", exist_ok=True)
create_test_wav("media/test_tone.wav")
print("Created media/test_tone.wav")
