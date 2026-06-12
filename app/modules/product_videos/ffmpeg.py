"""FFmpeg adapter — probe + transcode original uploads to HLS.

The adapter shells out to the system ``ffmpeg`` / ``ffprobe`` binaries.
We don't use a Python wrapper library on purpose — the binary args we
issue are stable, well-documented, and easy to inspect in ops logs.

Output layout (all relative to the per-video directory):

    poster.jpg                      — single still at ~1s
    hls/master.m3u8                 — multi-variant playlist
    hls/720p/index.m3u8 + segments
    hls/480p/index.m3u8 + segments

Codec choices:
- H.264 high@4.0 video / AAC LC audio. Universally supported across
  iOS Safari, Chrome, Android stock browsers — no need for
  HLS.js to fall back to DASH.
- fMP4 segments (HLS v7) so iOS native plays the same files as
  HLS.js on desktop. 4-second segments — short enough for low
  start-up latency, long enough that segment count stays manageable.

Failures are surfaced as :class:`FFmpegFailure` with stderr trimmed
to the last ~4kB so the admin sees the meaningful tail without
shipping a megabyte of debug spam to the DB.
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
from dataclasses import dataclass
from pathlib import Path

from app.core.logging import get_logger

_log = get_logger("hypershop.product_videos.ffmpeg")


class FFmpegFailure(Exception):
    """Raised when ffmpeg/ffprobe exits non-zero or output is missing."""


@dataclass(frozen=True, slots=True)
class ProbeResult:
    duration_seconds: int
    width: int
    height: int


def ffmpeg_available() -> tuple[bool, bool]:
    """Return ``(has_ffmpeg, has_ffprobe)`` based on PATH lookup.

    Cheap probe used at job-pickup time so we degrade with a clean
    error rather than failing inside an exec.
    """
    return shutil.which("ffmpeg") is not None, shutil.which("ffprobe") is not None


async def probe(input_path: Path) -> ProbeResult:
    """Run ffprobe; return duration/dimensions of the first video stream."""
    proc = await asyncio.create_subprocess_exec(
        "ffprobe",
        "-v", "error",
        "-select_streams", "v:0",
        "-show_entries", "stream=width,height:format=duration",
        "-of", "json",
        str(input_path),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    out, err = await proc.communicate()
    if proc.returncode != 0:
        raise FFmpegFailure(
            f"ffprobe exited {proc.returncode}: "
            f"{(err or b'').decode(errors='replace')[-2000:]}"
        )
    try:
        data = json.loads(out.decode())
    except json.JSONDecodeError as e:
        raise FFmpegFailure(f"ffprobe output unparsable: {e}") from e

    streams = data.get("streams") or []
    if not streams:
        raise FFmpegFailure("No video stream found in upload.")
    s0 = streams[0]
    width = int(s0.get("width") or 0)
    height = int(s0.get("height") or 0)
    duration_raw = (data.get("format") or {}).get("duration") or "0"
    try:
        duration = int(round(float(duration_raw)))
    except ValueError as e:
        raise FFmpegFailure(f"unparsable duration: {duration_raw}") from e

    if width <= 0 or height <= 0 or duration <= 0:
        raise FFmpegFailure(
            f"invalid probe values w={width} h={height} d={duration}",
        )
    return ProbeResult(
        duration_seconds=duration,
        width=width,
        height=height,
    )


async def make_poster(*, input_path: Path, output_path: Path) -> None:
    """Capture a single JPEG at ~2s into the file.

    Second-2 vs second-0/1: most product videos open with a brand bug
    or empty frame in the first second; second-2 catches the action
    once the camera/subject has settled but before the customer scrolls
    past the player.
    """
    output_path.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        "ffmpeg",
        "-nostdin",
        "-loglevel", "error",
        "-y",
        "-ss", "2",
        "-i", str(input_path),
        "-frames:v", "1",
        "-q:v", "3",
        str(output_path),
    ]
    await _run(cmd, label="poster")
    if not output_path.is_file() or output_path.stat().st_size == 0:
        raise FFmpegFailure("Poster file missing after ffmpeg exit 0.")


async def transcode_to_hls(
    *,
    input_path: Path,
    hls_dir: Path,
    max_height: int = 720,
) -> Path:
    """Produce a multi-variant HLS bundle. Returns the master playlist path.

    We render two variants — 720p and 360p — sharing the same segment
    cadence so the master playlist is small. If the source is smaller
    than 720p we render only the lower variant (no upscaling).
    """
    hls_dir.mkdir(parents=True, exist_ok=True)
    v720 = hls_dir / "720p"
    v360 = hls_dir / "360p"
    v720.mkdir(parents=True, exist_ok=True)
    v360.mkdir(parents=True, exist_ok=True)

    # Bitrate budget reference (60s upper bound):
    #   720p ≈ 1100 kbps × 60s ÷ 8 ≈ 8.25 MB
    #   360p ≈  350 kbps × 60s ÷ 8 ≈ 2.62 MB
    #   audio ≈ 96 kbps mono × 60s ÷ 8 ≈ 0.72 MB
    # 360p stays inside the 2–5 MB-per-variant target. 720p is over
    # at 60s but stays sharp on customer phones; HLS.js picks the
    # right variant via capLevelToPlayerSize so a 320 px tile only
    # downloads the 360p ladder.
    base_args = [
        "ffmpeg",
        "-nostdin",
        "-loglevel", "error",
        "-y",
        "-i", str(input_path),
        "-c:v", "libx264",
        "-profile:v", "high",
        "-level", "4.0",
        "-pix_fmt", "yuv420p",
        "-preset", "veryfast",
        # Audio: mono @ 96k. Product videos are dialog/voiceover or
        # music — neither needs stereo to be intelligible, and mono
        # halves the audio bitrate budget.
        "-c:a", "aac",
        "-ac", "1",
        "-ar", "44100",
        "-b:a", "96k",
        "-hls_time", "4",
        "-hls_playlist_type", "vod",
        "-hls_segment_type", "fmp4",
        "-hls_flags", "independent_segments",
        "-movflags", "+faststart",
    ]

    # 720p variant — only when source can support it (no upscaling).
    if max_height >= 720:
        await _run(
            [
                *base_args,
                "-vf", "scale=-2:720",
                "-b:v", "1100k",
                "-maxrate", "1300k",
                "-bufsize", "2200k",
                "-hls_segment_filename", str(v720 / "seg%05d.m4s"),
                str(v720 / "index.m3u8"),
            ],
            label="hls_720p",
        )

    # 360p variant — always emitted (cheap fallback for slow networks
    # + the only variant guaranteed to fit the 2–5 MB target at 60 s).
    await _run(
        [
            *base_args,
            "-vf", "scale=-2:360",
            "-b:v", "350k",
            "-maxrate", "450k",
            "-bufsize", "700k",
            "-hls_segment_filename", str(v360 / "seg%05d.m4s"),
            str(v360 / "index.m3u8"),
        ],
        label="hls_360p",
    )

    # Hand-write the master so the variant order + bandwidth values
    # are deterministic (ffmpeg's -master_pl_name path is fiddly when
    # invoked once-per-variant).
    master = hls_dir / "master.m3u8"
    lines = ["#EXTM3U", "#EXT-X-VERSION:7"]
    if (v720 / "index.m3u8").is_file():
        lines.append(
            '#EXT-X-STREAM-INF:BANDWIDTH=1300000,'
            'RESOLUTION=1280x720,CODECS="avc1.640028,mp4a.40.2"',
        )
        lines.append("720p/index.m3u8")
    lines.append(
        '#EXT-X-STREAM-INF:BANDWIDTH=450000,'
        'RESOLUTION=640x360,CODECS="avc1.64001e,mp4a.40.2"',
    )
    lines.append("360p/index.m3u8")
    master.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return master


async def _run(cmd: list[str], *, label: str) -> None:
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env={**os.environ, "LC_ALL": "C"},
    )
    _, err = await proc.communicate()
    if proc.returncode != 0:
        tail = (err or b"").decode(errors="replace")[-4000:]
        _log.warning(
            "ffmpeg_step_failed",
            label=label,
            returncode=proc.returncode,
        )
        raise FFmpegFailure(f"ffmpeg[{label}] exit {proc.returncode}: {tail}")
