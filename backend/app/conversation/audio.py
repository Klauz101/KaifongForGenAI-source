"""Audio helpers: convert browser audio (webm/opus) to a 16 kHz mono WAV that speech services accept."""

import asyncio
import io
import shutil
import wave

RATE = 16000
MAX_AUDIO_BYTES = 6_000_000  # ~60 s of browser opus is far smaller; guards memory and ffmpeg time
FFMPEG_TIMEOUT_S = 30


class AudioConversionError(RuntimeError):
    pass


def find_ffmpeg() -> str | None:
    """A system ffmpeg, else the one bundled by the optional `imageio-ffmpeg` package."""
    found = shutil.which("ffmpeg")
    if found:
        return found
    try:
        import imageio_ffmpeg

        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:  # noqa: BLE001 - optional package missing or no binary for this platform
        return None


def pcm_to_wav(pcm: bytes, rate: int = RATE) -> bytes:
    """Wrap raw 16-bit mono PCM in a WAV container whose header lengths are CORRECT.

    (ffmpeg writing WAV to a pipe cannot seek back to fill in the sizes: it leaves a bogus RIFF size and a
    tiny data size, so strict recognisers hear almost nothing. That broke real speech recognition.)
    """
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(pcm)
    return buf.getvalue()


async def to_wav_16k(audio: bytes) -> bytes:
    """Return 16 kHz mono PCM WAV. Raw audio is passed through memory only, never written to disk."""
    if len(audio) > MAX_AUDIO_BYTES:
        raise AudioConversionError("The recording is too long. Please speak in shorter turns.")
    ffmpeg = find_ffmpeg()
    if ffmpeg is None:
        raise AudioConversionError(
            "ffmpeg is not installed, and it is required to read browser audio. Install ffmpeg, or "
            "`pip install imageio-ffmpeg` (bundles one), then restart the server."
        )
    proc = await asyncio.create_subprocess_exec(
        ffmpeg,
        "-hide_banner",
        "-loglevel",
        "error",
        "-i",
        "pipe:0",
        "-ac",
        "1",
        "-ar",
        str(RATE),
        "-f",
        "s16le",
        "-acodec",
        "pcm_s16le",
        "pipe:1",  # raw PCM, wrapped below
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        pcm, err = await asyncio.wait_for(proc.communicate(audio), FFMPEG_TIMEOUT_S)
    except TimeoutError:
        proc.kill()
        raise AudioConversionError("Converting the recording took too long.") from None
    if proc.returncode != 0 or not pcm:
        raise AudioConversionError(
            f"ffmpeg could not read the recording: {err.decode(errors='replace')[:200]}"
        )
    return pcm_to_wav(pcm)
