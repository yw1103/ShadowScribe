"""Audio normalisation via ffmpeg.

Every downstream stage assumes **16 kHz mono 16-bit PCM**. Doing this once, up
front, keeps the ASR and voiceprint code free of format branching and means a
phone can upload m4a/opus/aac/amr without the server caring.
"""

from __future__ import annotations

import json
import logging
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger(__name__)

TARGET_SAMPLE_RATE = 16_000
FFMPEG = shutil.which("ffmpeg") or "ffmpeg"
FFPROBE = shutil.which("ffprobe") or "ffprobe"


class AudioError(RuntimeError):
    pass


@dataclass(slots=True)
class AudioInfo:
    path: Path
    duration_ms: int
    sample_rate: int
    channels: int


def ffmpeg_available() -> bool:
    return shutil.which("ffmpeg") is not None


def probe(path: Path) -> AudioInfo:
    """Read container/stream facts. Falls back to a decode when ffprobe is absent."""
    cmd = [
        FFPROBE,
        "-v",
        "error",
        "-select_streams",
        "a:0",
        "-show_entries",
        "stream=sample_rate,channels,duration",
        "-show_entries",
        "format=duration",
        "-of",
        "json",
        str(path),
    ]
    try:
        raw = subprocess.run(cmd, capture_output=True, text=True, timeout=120, check=True).stdout
        data = json.loads(raw or "{}")
        stream = (data.get("streams") or [{}])[0]
        duration = float(stream.get("duration") or data.get("format", {}).get("duration") or 0.0)
        return AudioInfo(
            path=path,
            duration_ms=int(duration * 1000),
            sample_rate=int(stream.get("sample_rate") or 0),
            channels=int(stream.get("channels") or 0),
        )
    except Exception as exc:  # ffprobe missing or unparsable container
        log.debug("ffprobe failed for %s (%s); falling back to decode-only", path, exc)
        return AudioInfo(path=path, duration_ms=0, sample_rate=0, channels=0)


def normalize(src: Path, dst: Path, *, sample_rate: int = TARGET_SAMPLE_RATE) -> AudioInfo:
    """Decode any container/container-of-codecs to mono PCM WAV at ``sample_rate``."""
    dst.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        FFMPEG,
        "-hide_banner",
        "-loglevel",
        "error",
        "-nostdin",
        "-y",
        "-i",
        str(src),
        "-vn",
        "-sn",
        "-dn",
        "-ac",
        "1",
        "-ar",
        str(sample_rate),
        "-c:a",
        "pcm_s16le",
        "-f",
        "wav",
        str(dst),
    ]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=3600)
    except FileNotFoundError as exc:
        raise AudioError(
            "ffmpeg not found on PATH. Install it (`apt-get install -y ffmpeg`) "
            "or run the official container image."
        ) from exc
    except subprocess.TimeoutExpired as exc:
        raise AudioError(f"ffmpeg timed out after 3600s on {src.name}") from exc

    if proc.returncode != 0:
        tail = (proc.stderr or "").strip().splitlines()[-6:]
        raise AudioError(f"ffmpeg failed on {src.name}: " + " / ".join(tail))

    info = probe(dst)
    if info.duration_ms == 0:
        # ffprobe unavailable — approximate from the WAV header we just wrote.
        info.duration_ms = _wav_duration_ms(dst, sample_rate)
    info.sample_rate = sample_rate
    info.channels = 1
    info.path = dst
    return info


def to_wav_bytes(path: Path, *, sample_rate: int = TARGET_SAMPLE_RATE) -> bytes:
    """Decode to raw PCM (no container) for embedding models that want raw samples."""
    cmd = [
        FFMPEG,
        "-hide_banner",
        "-loglevel",
        "error",
        "-nostdin",
        "-i",
        str(path),
        "-vn",
        "-ac",
        "1",
        "-ar",
        str(sample_rate),
        "-f",
        "s16le",
        "-",
    ]
    proc = subprocess.run(cmd, capture_output=True, timeout=1800)
    if proc.returncode != 0:
        raise AudioError(f"ffmpeg pcm decode failed on {path.name}")
    return proc.stdout


def _wav_duration_ms(path: Path, sample_rate: int) -> int:
    try:
        size = path.stat().st_size - 44  # canonical 44-byte PCM WAV header
        return max(0, int(size / (sample_rate * 2) * 1000))
    except OSError:
        return 0
