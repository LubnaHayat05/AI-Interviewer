from piper.voice import PiperVoice
import numpy as np
import soundfile as sf
import tempfile, os

# Load the voice model
voice = PiperVoice.load("en_US-amy-medium.onnx")

def speak_text_with_piper(text):
    if not voice:
        print("⚠️ Piper voice not loaded")
        return None
    try:
        # synthesize returns a generator of samples
        audio_gen = voice.synthesize(text)
        
        # convert generator to numpy array
        audio_array = np.array(list(audio_gen), dtype=np.float32)
        
        if audio_array.size == 0:
            print("⚠️ Synthesize returned empty audio")
            return None

        # Write WAV
        temp_file = os.path.join(tempfile.gettempdir(), "audio_gen.wav")
        sf.write(temp_file, audio_array, samplerate=voice.sample_rate)
        print(f"✅ Audio saved to {temp_file}")
        return temp_file
    except Exception as e:
        print(f"⚠️ TTS failed for '{text}': {e}")
        return None

# Test
speak_text_with_piper("Hello, this is a test of the Piper TTS system.")
