from faster_whisper import WhisperModel
import time
start_time = time.time()

def format_time(seconds):
    """Convert seconds to SRT time format"""
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = int(seconds % 60)
    millisecs = int((seconds % 1) * 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{millisecs:03d}"

def write_srt(segments, output_path):
    """Convert segments to SRT format"""
    with open(output_path, 'w', encoding='utf-8') as f:
        for i, segment in enumerate(segments, 1):
            print(segment)
            start_time = format_time(segment.start)
            end_time = format_time(segment.end)
            text = segment.text.strip()
            
            f.write(f"{i}\n")
            f.write(f"{start_time} --> {end_time}\n")
            f.write(f"{text}\n\n")


model_size = "large-v3-turbo"

# Run on GPU with FP16
model = WhisperModel(model_size, device="cuda", compute_type="float16")

# or run on GPU with INT8
# model = WhisperModel(model_size, device="cuda", compute_type="int8_float16")
# or run on CPU with INT8
# model = WhisperModel(model_size, device="cpu", compute_type="int8")

segments, info = model.transcribe("output.wav", beam_size=5, word_timestamps=True)

print("Detected language '%s' with probability %f" % (info.language, info.language_probability))

write_srt(segments, "output.srt")
duration = time.time() - start_time
print(f"It takes {duration} seconds")