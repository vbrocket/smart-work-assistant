import asyncio, edge_tts, subprocess, os

async def gen():
    text = "بسم الله الرحمن الرحيم. مرحبا بكم في مساعد العمل الذكي."
    mp3 = "/tmp/ref_ar.mp3"
    wav = "/workspace/pwa-idea/backend/voices/male_ar.wav"
    comm = edge_tts.Communicate(text, voice="ar-SA-HamedNeural")
    await comm.save(mp3)
    subprocess.run(["ffmpeg", "-y", "-i", mp3, "-ar", "24000", "-ac", "1", wav],
                   capture_output=True)
    sz = os.path.getsize(wav)
    print(f"Created {wav} -- {sz} bytes")
    os.unlink(mp3)

asyncio.run(gen())
