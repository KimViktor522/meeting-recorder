"""Аудио без внешнего ffmpeg: ресемплинг и кодирование через PyAV (идёт вместе с faster-whisper)."""
import wave

import numpy as np

RATE = 16000


def to_16k_mono(src):
    """Любой WAV/аудиофайл -> int16 numpy, 16 кГц, моно."""
    import av
    res = av.AudioResampler(format="s16", layout="mono", rate=RATE)
    parts = []
    with av.open(str(src)) as c:
        for frame in c.decode(audio=0):
            parts += [f.to_ndarray().reshape(-1) for f in res.resample(frame)]
        parts += [f.to_ndarray().reshape(-1) for f in res.resample(None)]
    return np.concatenate(parts) if parts else np.zeros(0, dtype=np.int16)


def save_wav(path, samples):
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1), w.setsampwidth(2), w.setframerate(RATE)
        w.writeframes(samples.astype(np.int16).tobytes())


def read_pcm(path):
    with wave.open(str(path), "rb") as w:
        return np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16)


def load_wav(path):
    """WAV 16 кГц моно -> float32 для whisper."""
    with wave.open(str(path), "rb") as w:
        return np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).astype(np.float32) / 32768.0


def write_m4a(path, left, right=None, bitrate=48000):
    """m4a (AAC). Стерео: левый канал — микрофон («я»), правый — системный звук. right=None — моно (импорт)."""
    import av
    mono = right is None
    n = len(left) if mono else max(len(left), len(right))
    if mono:
        st = np.ascontiguousarray(left, dtype=np.int16)
    else:
        st = np.zeros(n * 2, dtype=np.int16)
        st[0:len(left) * 2:2] = left
        st[1:len(right) * 2:2] = right
    layout, ch = ("mono", 1) if mono else ("stereo", 2)
    with av.open(str(path), "w", format="mp4") as out:
        stream = out.add_stream("aac", rate=RATE)
        stream.layout = layout
        stream.bit_rate = bitrate
        step = 1024
        for i in range(0, n, step):
            chunk = st[i * ch:(i + step) * ch].reshape(1, -1)
            frame = av.AudioFrame.from_ndarray(chunk, format="s16", layout=layout)
            frame.sample_rate, frame.pts = RATE, i
            for packet in stream.encode(frame):
                out.mux(packet)
        for packet in stream.encode(None):
            out.mux(packet)


def duration_seconds(path):
    """Длительность медиафайла (с) или None."""
    import av
    with av.open(str(path)) as c:
        return c.duration / 1_000_000 if c.duration else None
