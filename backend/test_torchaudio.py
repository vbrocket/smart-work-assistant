import torchaudio
print("torchaudio version:", torchaudio.__version__)
try:
    torchaudio.set_audio_backend("soundfile")
    print("set_audio_backend OK")
except Exception as e:
    print("set_audio_backend failed:", e)

try:
    audio, sr = torchaudio.load("voices/male_ar.wav")
    print(f"torchaudio.load OK: shape={audio.shape}, sr={sr}")
except Exception as e:
    print("torchaudio.load FAILED:", e)
    import soundfile as sf
    import torch
    data, sr = sf.read("voices/male_ar.wav")
    audio = torch.from_numpy(data).unsqueeze(0).float()
    print(f"soundfile fallback OK: shape={audio.shape}, sr={sr}")
