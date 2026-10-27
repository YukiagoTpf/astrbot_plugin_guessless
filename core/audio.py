"""听歌猜番的音频：ffmpeg 解码、去掉开头的静音、统一响度，再切成越来越长的片段当 QQ 语音发。

AstrBot 官方 Docker 镜像自带 ffmpeg；发出去时 AstrBot 转成 base64 交给 NapCat，NapCat 再转成 QQ 的 silk。
"""

import asyncio
import base64
import io
import shutil
import wave

CLIPS = (2, 4, 7, 12, 20)  # 每喊一次「提示」放的长度（秒），都从头放
RATE = 24000


def available() -> bool:
    return shutil.which("ffmpeg") is not None


async def decode(data: bytes) -> bytes:
    """整首歌 → 单声道 16 位 PCM：从第一个有声音的地方开始，最长 CLIPS[-1] 秒。
    片头常有一两秒空白；各首歌音量差得多，QQ 语音又会压缩，统一响度后小声的也听得清。"""
    proc = await asyncio.create_subprocess_exec(
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-i", "pipe:0",
        "-af", "silenceremove=start_periods=1:start_threshold=-45dB,loudnorm=I=-16:TP=-1.5",
        "-t", str(CLIPS[-1]), "-ac", "1", "-ar", str(RATE), "-f", "s16le", "pipe:1",
        stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    out, err = await asyncio.wait_for(proc.communicate(data), 60)
    if proc.returncode or len(out) < CLIPS[0] * RATE * 2:
        raise RuntimeError(f"ffmpeg 解码失败：{err.decode(errors='ignore').strip()[-200:]}")
    return out


def clip(pcm: bytes, seconds: float) -> str:
    """前 seconds 秒包成 WAV，返回 base64。"""
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(pcm[: int(seconds * RATE) * 2])
    return base64.b64encode(buf.getvalue()).decode()
