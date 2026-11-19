"""Find a streamer's facecam overlay and build the 'stack' layout (facecam on top, gameplay below).

The overlay sits in one place for the whole stream, while faces inside the game move. So: sample
frames across the source, detect faces (YuNet, via framing), and keep the small face that stays put
in most frames. The result is a crop rect (fractions of the frame) with the top panel's aspect.
"""
from __future__ import annotations

import logging
import statistics
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

SAMPLES = 30
MIN_SCORE = .6
MAX_FACE_W = .25        # wider than this fraction of the frame is a close-up in the game, not an overlay
MIN_FACE_W = .02
CENTRE_TOL = .03        # centres closer than this, in frame fractions, are the same spot
SIZE_TOL = .3           # and a similar size
MIN_SHARE = .4          # a facecam shows in at least this share of the sampled frames
PAD = 2.8               # crop width as a multiple of the face width
FACE_Y = .45            # where the face sits in the crop, from the top
TOP_SHARE = .4          # the top panel takes this share of the output height
FRAME_WIDTH = 640


@dataclass(frozen=True)
class Box:
    cx: float
    cy: float
    w: float
    h: float
    score: float = 1.0


def _near(a: Box, b: Box) -> bool:
    return abs(a.cx - b.cx) <= CENTRE_TOL and abs(a.cy - b.cy) <= CENTRE_TOL and abs(a.w - b.w) <= SIZE_TOL * max(a.w, b.w)


def pick_facecam(frames: list[list[Box]], aspect: float, *, panel: tuple[int, int] = (1080, 768)) -> tuple[float, float, float, float] | None:
    """Crop rect (x, y, w, h) as fractions of the frame, or None when no overlay is stationary enough.

    `aspect` is frame width / height; the rect gets the top panel's aspect so it is not stretched.
    """
    if not frames:
        return None
    usable = [[b for b in boxes if b.score >= MIN_SCORE and MIN_FACE_W <= b.w <= MAX_FACE_W] for boxes in frames]
    best: list[Box] = []
    for boxes in usable:
        for anchor in boxes:
            seen = [next(b for b in other if _near(anchor, b)) for other in usable if any(_near(anchor, b) for b in other)]
            if len(seen) > len(best):
                best = seen
    if len(best) < max(3, MIN_SHARE * len(frames)):
        return None
    cx, cy = statistics.median(b.cx for b in best), statistics.median(b.cy for b in best)
    face_w = statistics.median(b.w for b in best)
    ratio = aspect * panel[1] / panel[0]            # crop height / crop width, in frame fractions
    cw = min(1.0, face_w * PAD)
    ch = cw * ratio
    if ch > 1.0:
        ch, cw = 1.0, 1.0 / ratio
    x = min(max(cx - cw / 2, 0.0), 1.0 - cw)
    y = min(max(cy - ch * FACE_Y, 0.0), 1.0 - ch)
    return (round(x, 4), round(y, 4), round(cw, 4), round(ch, 4))


def stack_filter(rect: tuple[float, float, float, float], crop_x: float, w: int, h: int) -> str:
    """`[0:v]…[base]`: facecam panel on top, gameplay (centre crop, shiftable by crop_x) below."""
    top = round(h * TOP_SHARE / 2) * 2
    bottom = h - top
    x, y, rw, rh = rect
    cam = (f"[c]crop=iw*{rw:.5f}:ih*{rh:.5f}:iw*{x:.5f}:ih*{y:.5f},scale={w}:{top}:force_original_aspect_ratio=increase,"
           f"crop={w}:{top},setsar=1[cam]")
    game = (f"[g]scale={w}:{bottom}:force_original_aspect_ratio=increase,"
            f"crop={w}:{bottom}:x='(iw-ow)*{crop_x:.5f}':y='(ih-oh)/2',setsar=1[game]")
    return f"[0:v]split=2[c][g];{cam};{game};[cam][game]vstack=inputs=2[base]"


def _grab(video: Path, at: float, out: Path) -> bool:
    from backend.utils.ffmpeg_utils import get_ffmpeg_path
    cmd = [get_ffmpeg_path(), '-hide_banner', '-loglevel', 'error', '-ss', f'{at:.2f}', '-i', str(video), '-frames:v', '1',
           '-vf', f'scale={FRAME_WIDTH}:-2', '-q:v', '3', '-y', str(out)]
    try:
        subprocess.run(cmd, check=True, capture_output=True, timeout=60)
    except (subprocess.SubprocessError, OSError):
        return False
    return out.exists()


def detect_boxes(video: Path, duration: float, samples: int = SAMPLES) -> tuple[list[list[Box]], float]:
    """Face boxes per sampled frame, plus the frame aspect. Needs OpenCV (installed on demand by framing)."""
    from backend.services.studio import framing
    cv2 = framing.load_cv2()
    if cv2 is None:
        return [], 16 / 9
    frames: list[list[Box]] = []
    aspect = 16 / 9
    with tempfile.TemporaryDirectory(prefix='ac-facecam-') as tmp:
        for i in range(samples):
            at = duration * (i + .5) / samples
            path = Path(tmp) / f'{i}.jpg'
            if not _grab(video, at, path):
                continue
            image = cv2.imread(str(path))
            if image is None:
                continue
            height, width = image.shape[:2]
            aspect = width / height
            with framing._detector_lock:
                _, faces = framing._detector(cv2, width, height, MIN_SCORE).detect(image)
                rows = faces.copy() if faces is not None else []
            frames.append([Box((float(f[0]) + float(f[2]) / 2) / width, (float(f[1]) + float(f[3]) / 2) / height,
                               float(f[2]) / width, float(f[3]) / height, float(f[14]) if len(f) > 14 else 1.0) for f in rows])
    return frames, aspect


def detect_facecam(video: Path, duration: float) -> tuple[float, float, float, float] | None:
    frames, aspect = detect_boxes(video, duration)
    return pick_facecam(frames, aspect)
