"""
Image Compressor — Flask + Pillow.

Compress an image into a target size range (e.g. "between 100 KB and 200 KB"),
which is what most exam / government / job portals ask for.

Everything is in this one file: the API and the UI. Nothing is written to disk,
the compressed image comes back as a data URL.

Run locally:      python app.py            -> http://localhost:5000
Run on a server:  gunicorn app:app
"""

import base64
import io
import os

from flask import Flask, request, jsonify, render_template_string
from PIL import Image, ImageOps, ImageSequence

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 40 * 1024 * 1024  # 40 MB upload cap

ALLOWED_FORMATS = {"JPEG", "PNG", "WEBP", "GIF", "BMP", "TIFF"}
MIME = {"JPEG": "image/jpeg", "PNG": "image/png", "WEBP": "image/webp", "GIF": "image/gif"}
OUTPUT_FORMATS = {"auto", "jpeg", "webp", "png"}
MAX_PIXELS = 12_000_000      # bigger inputs are scaled down first, keeps encodes fast
ANIM_MAX_PIXELS = 4_000_000  # same idea for every frame of an animation
QUALITY_LADDER = (95, 88, 80, 72, 64, 56, 48, 40, 32, 24, 16, 8)
PNG_COLORS = (256, 200, 128, 96, 64, 32, 16, 8, 4, 2)


# --------------------------------------------------------------------------- #
#  helpers
# --------------------------------------------------------------------------- #

def human_size(num_bytes):
    if num_bytes < 1024:
        return "{} B".format(num_bytes)
    if num_bytes < 1024 * 1024:
        return "{:.1f} KB".format(num_bytes / 1024)
    return "{:.2f} MB".format(num_bytes / (1024 * 1024))


def encode(img, fmt, param):
    """Encode a single frame. `param` is a quality (JPEG/WEBP) or a colour
    count (PNG). Cheap settings for big images, nicer settings for small ones -
    the byte budget is what matters, not the encoder flags."""
    buf = io.BytesIO()
    pixels = img.width * img.height
    if fmt == "JPEG":
        kwargs = dict(quality=param)
        if pixels <= 4_000_000:
            kwargs.update(optimize=True, progressive=True)
        img.convert("RGB").save(buf, "JPEG", **kwargs)
    elif fmt == "WEBP":
        img.save(buf, "WEBP", quality=param, method=2 if pixels <= 4_000_000 else 1)
    elif fmt == "PNG":
        img.quantize(colors=param, method=Image.Quantize.FASTOCTREE).save(
            buf, "PNG", optimize=True
        )
    else:
        raise ValueError("Unsupported output format: " + fmt)
    return buf.getvalue()


def flatten(img):
    """JPEG has no alpha channel - put transparent pixels on white."""
    if img.mode in ("RGBA", "LA", "P"):
        img = img.convert("RGBA")
        canvas = Image.new("RGB", img.size, (255, 255, 255))
        canvas.paste(img, mask=img.split()[-1])
        return canvas
    if img.mode != "RGB":
        return img.convert("RGB")
    return img


def resize_to(img, width, height):
    return img.resize((max(1, width), max(1, height)), Image.LANCZOS)


def search_still(img, fmt, max_bytes):
    """Highest quality (or colour count) that still fits in max_bytes.
    File size only grows with quality, so the first hit walking downwards is
    already the best one; two extra probes tighten it a little."""
    if fmt == "PNG":
        for colors in PNG_COLORS:
            data = encode(img, "PNG", colors)
            if len(data) <= max_bytes:
                return data, colors, img
        return None, None, img

    previous = None
    for quality in QUALITY_LADDER:
        data = encode(img, fmt, quality)
        if len(data) <= max_bytes:
            if previous is None:
                return data, quality, img
            low, high = quality, previous
            best_data, best_q = data, quality
            for _ in range(2):
                mid = (low + high) // 2
                if mid in (low, high):
                    break
                probe = encode(img, fmt, mid)
                if len(probe) <= max_bytes:
                    low, best_data, best_q = mid, probe, mid
                else:
                    high = mid
            return best_data, best_q, img
        previous = quality
    return None, None, img


def lift_to_min(img, fmt, min_bytes, max_bytes):
    """Result landed under the minimum the user asked for - some portals reject
    files that are too small. Grow it back into the window."""
    if min_bytes <= 0:
        return None
    for factor in (1.05, 1.1, 1.2, 1.3, 1.45, 1.6, 1.8, 2.0):
        width, height = int(img.width * factor), int(img.height * factor)
        if width * height > MAX_PIXELS:
            break
        bigger = resize_to(img, width, height)
        data, _, out = search_still(bigger, fmt, max_bytes)
        if data and len(data) >= min_bytes:
            return data, out
    return None


def compress_still(img, fmt, min_bytes, max_bytes):
    """Trade quality first, then resolution, until the file fits the window."""
    work = img
    for _ in range(6):
        data, _param, out = search_still(work, fmt, max_bytes)
        if data:
            if len(data) < min_bytes:
                lifted = lift_to_min(work, fmt, min_bytes, max_bytes)
                if lifted:
                    return lifted[0], lifted[1], "Min size tak pahunchane ke liye thoda bada kiya gaya."
            return data, out, None

        # Even the lowest quality is too big. Measure that floor and jump
        # straight to the resolution that should fit (bytes scale with area).
        floor = encode(work, fmt, 2 if fmt == "PNG" else QUALITY_LADDER[-1])
        if len(floor) <= max_bytes:
            return floor, work, None
        ratio = (max_bytes / float(len(floor))) ** 0.5
        scale = max(0.2, min(0.85, ratio * 0.95))
        width, height = int(work.width * scale), int(work.height * scale)
        if width < 32 or height < 32:
            break
        work = resize_to(work, width, height)

    fallback = encode(work, fmt, 2 if fmt == "PNG" else QUALITY_LADDER[-1])
    return fallback, work, "Target bahut chhota hai - sabse chhoti possible image bheji gayi hai."



def frames_of(img):
    frames, durations = [], []
    for frame in ImageSequence.Iterator(img):
        durations.append(frame.info.get("duration", 100))
        frames.append(frame.convert("RGBA"))
    return frames, durations


def encode_anim(frames, durations, fmt, scale, quality=80, loop=0):
    width = max(1, int(frames[0].width * scale))
    height = max(1, int(frames[0].height * scale))
    out_frames = []
    for frame in frames:
        resized = frame if scale == 1 else resize_to(frame, width, height)
        if fmt == "GIF":
            resized = resized.convert("P", palette=Image.ADAPTIVE, colors=256)
        out_frames.append(resized)

    buf = io.BytesIO()
    kwargs = dict(
        save_all=True,
        append_images=out_frames[1:],
        duration=durations,
        loop=loop,
        optimize=True,
    )
    if fmt == "WEBP":
        kwargs.update(quality=quality, method=4)
    out_frames[0].save(buf, fmt, **kwargs)
    return buf.getvalue(), width, height


def compress_anim(frames, durations, fmt, max_bytes, loop=0):
    """Animated files: scale the whole animation down until it fits."""
    first = frames[0]
    if first.width * first.height > ANIM_MAX_PIXELS:
        factor = (ANIM_MAX_PIXELS / float(first.width * first.height)) ** 0.5
        width, height = int(first.width * factor), int(first.height * factor)
        frames = [resize_to(frame, width, height) for frame in frames]

    best = None
    scale = 1.0
    while scale >= 0.12:
        data, w, h = encode_anim(frames, durations, fmt, scale, loop=loop)
        if len(data) <= max_bytes:
            return data, w, h, None
        best = (data, w, h)
        scale -= 0.08
    data, w, h = encode_anim(frames, durations, fmt, 0.1, loop=loop)
    return data, w, h, "target too small for this animation"


def pick_format(img, requested):
    if requested in ("jpeg", "webp", "png"):
        return requested.upper()
    source = (img.format or "").upper()
    if getattr(img, "is_animated", False):
        return "GIF" if source == "GIF" else "WEBP"
    if source == "PNG":
        return "PNG"
    if source == "WEBP":
        return "WEBP"
    return "JPEG"


# --------------------------------------------------------------------------- #
#  API
# --------------------------------------------------------------------------- #

@app.route("/compress", methods=["POST"])
def compress():
    try:
        upload = request.files.get("image")
        if upload is None or not upload.filename:
            return jsonify(error="Koi image nahi mili. Pehle image choose karein."), 400

        raw = upload.read()
        if not raw:
            return jsonify(error="File khali hai."), 400

        try:
            img = Image.open(io.BytesIO(raw))
            img.format = img.format or ""
        except Exception:
            return jsonify(error="Ye valid image file nahi hai."), 400

        if img.format.upper() not in ALLOWED_FORMATS:
            return jsonify(
                error="{} format support nahi hai. JPEG, PNG, WEBP, GIF, BMP ya TIFF bhejein.".format(
                    img.format or "Ye"
                )
            ), 400

        unit = (request.form.get("unit") or "KB").upper()
        multiplier = 1024 if unit == "KB" else 1024 * 1024
        try:
            min_size = max(0.0, float(request.form.get("min_size") or 0))
            max_size = float(request.form.get("max_size") or 200)
        except ValueError:
            return jsonify(error="Size values numbers honi chahiye."), 400

        if max_size <= 0:
            return jsonify(error="Max size 0 se bada hona chahiye."), 400
        if min_size >= max_size:
            return jsonify(error="Min size, max size se chhota hona chahiye."), 400

        min_bytes = int(min_size * multiplier)
        max_bytes = int(max_size * multiplier)

        requested = (request.form.get("format") or "auto").lower()
        if requested not in OUTPUT_FORMATS:
            requested = "auto"
        fmt = pick_format(img, requested)

        note = None
        animated = bool(getattr(img, "is_animated", False))
        loop = img.info.get("loop", 0) if animated else 0
        original_bytes = len(raw)

        # Nothing to do: the file is already inside the window and we are not
        # being asked to change its format. Hand back the untouched original.
        source_format = (img.format or "").upper()
        if min_bytes <= original_bytes <= max_bytes and fmt == source_format:
            return jsonify(
                image_url="data:{};base64,{}".format(
                    MIME.get(source_format, "image/jpeg"),
                    base64.b64encode(raw).decode("ascii"),
                ),
                format=source_format.lower(),
                extension={"JPEG": "jpg", "PNG": "png", "WEBP": "webp", "GIF": "gif"}.get(
                    source_format, "jpg"
                ),
                original_size=human_size(original_bytes),
                compressed_size=human_size(original_bytes),
                original_bytes=original_bytes,
                compressed_bytes=original_bytes,
                saved_percent=0.0,
                width=img.width,
                height=img.height,
                in_range=True,
                note="Original image pehle se is range me hai, isliye kuch change nahi kiya.",
            )

        if animated and fmt == "PNG":
            fmt = "WEBP"
            note = "Animated file ko PNG me save nahi kiya ja sakta, WEBP use kiya."

        if animated:
            frames, durations = frames_of(img)
            data, width, height, anim_note = compress_anim(
                frames, durations, fmt, max_bytes, loop=loop
            )
            note = note or anim_note
            if len(data) < min_bytes:
                note = note or "Animation min size tak nahi pahunch payi."
        else:
            img = ImageOps.exif_transpose(img)  # honour camera rotation
            if img.width * img.height > MAX_PIXELS:
                factor = (MAX_PIXELS / float(img.width * img.height)) ** 0.5
                img = resize_to(img, int(img.width * factor), int(img.height * factor))
            if fmt == "JPEG":
                img = flatten(img)
            data, out_img, still_note = compress_still(img, fmt, min_bytes, max_bytes)
            width, height = out_img.size
            note = note or still_note

        compressed_bytes = len(data)
        saved = 0.0
        if original_bytes:
            saved = (1 - compressed_bytes / original_bytes) * 100

        return jsonify(
            image_url="data:{};base64,{}".format(
                MIME.get(fmt, "image/jpeg"),
                base64.b64encode(data).decode("ascii"),
            ),
            format=fmt.lower(),
            extension={"JPEG": "jpg", "PNG": "png", "WEBP": "webp", "GIF": "gif"}.get(fmt, "jpg"),
            original_size=human_size(original_bytes),
            compressed_size=human_size(compressed_bytes),
            original_bytes=original_bytes,
            compressed_bytes=compressed_bytes,
            saved_percent=round(saved, 1),
            width=width,
            height=height,
            in_range=min_bytes <= compressed_bytes <= max_bytes,
            note=note,
        )

    except Exception as exc:  # pragma: no cover - last-resort guard
        return jsonify(error="Compression fail hui: {}".format(exc)), 500


@app.errorhandler(413)
def too_large(_):
    return jsonify(error="File 40 MB se badi hai. Chhoti image try karein."), 413


@app.route("/health")
def health():
    return jsonify(status="ok")


@app.route("/")
def index():
    return render_template_string(HTML_TEMPLATE)


# --------------------------------------------------------------------------- #
#  UI
# --------------------------------------------------------------------------- #

HTML_TEMPLATE = r"""
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<meta name="theme-color" content="#0f172a">
<meta name="description" content="Compress an image into a target size range - under 50 KB, under 100 KB, or any KB / MB window you need.">
<title>Image Compressor — fit any size limit</title>
<style>
  :root {
    --bg: #f4f6fb;
    --panel: #ffffff;
    --ink: #0f172a;
    --ink-soft: #5a6478;
    --line: #e3e8f0;
    --brand: #4f46e5;
    --brand-ink: #ffffff;
    --brand-soft: #eef0ff;
    --good: #0f9d6e;
    --good-soft: #e6f7f0;
    --warn: #b45309;
    --warn-soft: #fdf3e3;
    --bad: #d92d20;
    --bad-soft: #fdeceb;
    --radius: 16px;
    --shadow: 0 1px 2px rgba(15, 23, 42, .06), 0 12px 32px rgba(15, 23, 42, .07);
  }

  @media (prefers-color-scheme: dark) {
    :root {
      --bg: #0b1120;
      --panel: #131c31;
      --ink: #eef2ff;
      --ink-soft: #96a0b8;
      --line: #26314b;
      --brand: #7c7cf7;
      --brand-ink: #0b1120;
      --brand-soft: #1c2440;
      --good: #34d399;
      --good-soft: #102a24;
      --warn: #fbbf24;
      --warn-soft: #2a2213;
      --bad: #f87171;
      --bad-soft: #2b1618;
      --shadow: 0 1px 2px rgba(0, 0, 0, .4), 0 16px 40px rgba(0, 0, 0, .35);
    }
  }

  * { box-sizing: border-box; margin: 0; padding: 0; }

  body {
    background: var(--bg);
    color: var(--ink);
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
    line-height: 1.5;
    min-height: 100vh;
    padding: 32px 18px 56px;
    -webkit-font-smoothing: antialiased;
  }

  .wrap { max-width: 880px; margin: 0 auto; }

  header { margin-bottom: 22px; }

  h1 {
    font-size: 1.65rem;
    letter-spacing: -0.02em;
    display: flex;
    align-items: center;
    gap: 10px;
  }

  .mark {
    width: 34px; height: 34px;
    border-radius: 10px;
    background: linear-gradient(135deg, var(--brand), #22d3ee);
    display: grid; place-items: center;
    flex: 0 0 auto;
  }
  .mark svg { width: 20px; height: 20px; }

  .lede { color: var(--ink-soft); font-size: .94rem; margin-top: 6px; }

  .card {
    background: var(--panel);
    border: 1px solid var(--line);
    border-radius: var(--radius);
    box-shadow: var(--shadow);
    padding: 22px;
  }

  .drop {
    border: 2px dashed var(--line);
    border-radius: 14px;
    padding: 34px 20px;
    text-align: center;
    cursor: pointer;
    transition: border-color .18s, background .18s, transform .18s;
    background: transparent;
  }
  .drop:hover { border-color: var(--brand); background: var(--brand-soft); }
  .drop.over { border-color: var(--brand); background: var(--brand-soft); transform: scale(1.005); }
  .drop:focus-visible { outline: 2px solid var(--brand); outline-offset: 3px; }

  .drop .icon {
    width: 46px; height: 46px; margin: 0 auto 12px;
    border-radius: 12px; background: var(--brand-soft);
    display: grid; place-items: center;
  }
  .drop .icon svg { width: 24px; height: 24px; stroke: var(--brand); }

  .drop strong { display: block; font-size: 1rem; font-weight: 600; }
  .drop small { color: var(--ink-soft); font-size: .82rem; }

  .file-chip {
    display: none;
    align-items: center;
    gap: 12px;
    margin-top: 14px;
    padding: 12px 14px;
    border: 1px solid var(--line);
    border-radius: 12px;
    background: var(--bg);
  }
  .file-chip.show { display: flex; }
  .file-chip .thumb {
    width: 42px; height: 42px; border-radius: 8px; object-fit: cover;
    background: var(--line); flex: 0 0 auto;
  }
  .file-chip .meta { min-width: 0; flex: 1; }
  .file-chip .name {
    font-size: .88rem; font-weight: 600;
    white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
  }
  .file-chip .sub { font-size: .78rem; color: var(--ink-soft); }

  .x {
    border: 0; background: transparent; color: var(--ink-soft);
    font-size: 1.1rem; cursor: pointer; padding: 6px 8px; border-radius: 8px;
    line-height: 1;
  }
  .x:hover { background: var(--line); color: var(--ink); }

  .grid {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
    gap: 14px;
    margin-top: 20px;
  }

  .field label {
    display: block; font-size: .78rem; font-weight: 600;
    color: var(--ink-soft); margin-bottom: 6px;
    text-transform: uppercase; letter-spacing: .04em;
  }

  input[type="number"], select {
    width: 100%;
    padding: 11px 12px;
    border: 1px solid var(--line);
    border-radius: 10px;
    background: var(--bg);
    color: var(--ink);
    font-size: .95rem;
    font-family: inherit;
    transition: border-color .15s, box-shadow .15s;
  }
  input[type="number"]:focus, select:focus {
    outline: none;
    border-color: var(--brand);
    box-shadow: 0 0 0 3px var(--brand-soft);
  }

  .presets { display: flex; flex-wrap: wrap; gap: 8px; margin-top: 16px; }
  .chip {
    border: 1px solid var(--line);
    background: transparent;
    color: var(--ink-soft);
    border-radius: 999px;
    padding: 7px 13px;
    font-size: .8rem;
    font-weight: 500;
    cursor: pointer;
    font-family: inherit;
    transition: all .15s;
  }
  .chip:hover { border-color: var(--brand); color: var(--brand); }
  .chip.active { background: var(--brand); border-color: var(--brand); color: var(--brand-ink); }

  .actions { display: flex; gap: 10px; margin-top: 20px; }

  .btn {
    border: 0;
    border-radius: 11px;
    padding: 13px 20px;
    font-size: .95rem;
    font-weight: 600;
    font-family: inherit;
    cursor: pointer;
    transition: transform .12s, opacity .15s, background .15s;
    text-decoration: none;
    display: inline-flex;
    align-items: center;
    justify-content: center;
    text-align: center;
  }
  .btn:active { transform: scale(.99); }
  .btn-primary { background: var(--brand); color: var(--brand-ink); flex: 1; }
  .btn-primary:hover { opacity: .92; }
  .btn-primary:disabled { opacity: .5; cursor: not-allowed; }
  .btn-ghost { background: transparent; color: var(--ink-soft); border: 1px solid var(--line); }
  .btn-ghost:hover { color: var(--ink); border-color: var(--ink-soft); }

  .msg { margin-top: 14px; font-size: .87rem; border-radius: 10px; padding: 11px 13px; display: none; }
  .msg.show { display: block; }
  .msg.error { background: var(--bad-soft); color: var(--bad); }
  .msg.warn { background: var(--warn-soft); color: var(--warn); }

  .result { display: none; margin-top: 20px; }
  .result.show { display: block; }

  .compare {
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 14px;
  }

  .pane {
    border: 1px solid var(--line);
    border-radius: 14px;
    overflow: hidden;
    background: var(--bg);
  }

  .pane .head {
    display: flex; justify-content: space-between; align-items: baseline;
    gap: 8px; padding: 10px 12px;
    font-size: .74rem; font-weight: 700; letter-spacing: .05em;
    text-transform: uppercase; color: var(--ink-soft);
  }
  .pane .head .size { font-family: ui-monospace, "SF Mono", Menlo, monospace; letter-spacing: 0; }
  .pane.after .head { color: var(--good); }

  .pane .shot {
    aspect-ratio: 4 / 3;
    display: grid; place-items: center;
    background-image:
      linear-gradient(45deg, var(--line) 25%, transparent 25%),
      linear-gradient(-45deg, var(--line) 25%, transparent 25%),
      linear-gradient(45deg, transparent 75%, var(--line) 75%),
      linear-gradient(-45deg, transparent 75%, var(--line) 75%);
    background-size: 16px 16px;
    background-position: 0 0, 0 8px, 8px -8px, -8px 0;
  }
  .pane .shot img { max-width: 100%; max-height: 100%; display: block; object-fit: contain; }
  .pane .foot {
    padding: 8px 12px; font-size: .76rem; color: var(--ink-soft);
    border-top: 1px solid var(--line);
    font-family: ui-monospace, "SF Mono", Menlo, monospace;
  }

  .summary {
    display: flex; flex-wrap: wrap; align-items: center; gap: 10px;
    margin-top: 14px;
  }

  .badge {
    display: inline-flex; align-items: center; gap: 6px;
    border-radius: 999px; padding: 6px 12px;
    font-size: .82rem; font-weight: 700;
  }
  .badge.good { background: var(--good-soft); color: var(--good); }
  .badge.warn { background: var(--warn-soft); color: var(--warn); }

  .summary .spacer { flex: 1; }
  .summary .btn { flex: 0 0 auto; padding: 12px 24px; }

  .saved-line { font-size: .84rem; color: var(--ink-soft); }

  .spin {
    display: inline-block;
    width: 15px; height: 15px;
    border: 2px solid currentColor;
    border-right-color: transparent;
    border-radius: 50%;
    animation: spin .7s linear infinite;
    vertical-align: -2px;
    margin-right: 8px;
  }
  @keyframes spin { to { transform: rotate(360deg); } }

  footer {
    text-align: center; color: var(--ink-soft);
    font-size: .78rem; margin-top: 22px; line-height: 1.7;
  }
  footer code {
    background: var(--panel); border: 1px solid var(--line);
    padding: 1px 6px; border-radius: 6px;
    font-size: .76rem;
  }

  @media (max-width: 560px) {
    body { padding: 20px 12px 40px; }
    .card { padding: 16px; }
    .compare { grid-template-columns: 1fr; }
    h1 { font-size: 1.35rem; }
  }

  @media (prefers-reduced-motion: reduce) {
    * { animation: none !important; transition: none !important; }
  }
</style>
</head>
<body>
<div class="wrap">
  <header>
    <h1>
      <span class="mark" aria-hidden="true">
        <svg viewBox="0 0 24 24" fill="none" stroke="#fff" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
          <rect x="3" y="3" width="18" height="18" rx="3"></rect>
          <circle cx="8.5" cy="8.5" r="1.5"></circle>
          <path d="m21 15-4.5-4.5L7 20"></path>
        </svg>
      </span>
      Image Compressor
    </h1>
    <p class="lede">Apni image ko kisi bhi size limit ke andar laayein — 20 KB ho ya 2 MB, format aur quality aap chunein.</p>
  </header>

  <div class="card">
    <div class="drop" id="drop" tabindex="0" role="button"
         aria-label="Image choose karne ke liye click karein ya drag karein">
      <div class="icon" aria-hidden="true">
        <svg viewBox="0 0 24 24" fill="none" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
          <path d="M12 16V4"></path><path d="m7 9 5-5 5 5"></path>
          <path d="M20 16v2a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2v-2"></path>
        </svg>
      </div>
      <strong>Image drop karein ya click karein</strong>
      <small>JPG, PNG, WEBP, GIF, BMP, TIFF — 40 MB tak · paste bhi kar sakte hain</small>
      <input type="file" id="file" accept="image/*" hidden>
    </div>

    <div class="file-chip" id="chip">
      <img class="thumb" id="thumb" alt="">
      <div class="meta">
        <div class="name" id="fileName"></div>
        <div class="sub" id="fileSub"></div>
      </div>
      <button class="x" id="clear" type="button" aria-label="File hataayein">&times;</button>
    </div>

    <div class="grid">
      <div class="field">
        <label for="minSize">Min size</label>
        <input type="number" id="minSize" value="0" min="0" step="1">
      </div>
      <div class="field">
        <label for="maxSize">Max size</label>
        <input type="number" id="maxSize" value="100" min="1" step="1">
      </div>
      <div class="field">
        <label for="unit">Unit</label>
        <select id="unit">
          <option value="KB" selected>KB</option>
          <option value="MB">MB</option>
        </select>
      </div>
      <div class="field">
        <label for="format">Output format</label>
        <select id="format">
          <option value="auto" selected>Auto (jaisa hai waisa)</option>
          <option value="jpeg">JPG — photos ke liye best</option>
          <option value="webp">WEBP — sabse chhoti</option>
          <option value="png">PNG — transparency</option>
        </select>
      </div>
    </div>

    <div class="presets" id="presets">
      <button type="button" class="chip" data-max="20">Under 20 KB</button>
      <button type="button" class="chip" data-max="50">Under 50 KB</button>
      <button type="button" class="chip active" data-max="100">Under 100 KB</button>
      <button type="button" class="chip" data-max="200">Under 200 KB</button>
      <button type="button" class="chip" data-max="500">Under 500 KB</button>
      <button type="button" class="chip" data-max="1024" data-unit="KB">Under 1 MB</button>
    </div>

    <div class="actions">
      <button class="btn btn-primary" id="go" type="button">Compress</button>
      <button class="btn btn-ghost" id="reset" type="button">Reset</button>
    </div>

    <div class="msg" id="msg" role="alert"></div>

    <div class="result" id="result">
      <div class="compare">
        <div class="pane before">
          <div class="head"><span>Original</span><span class="size" id="origSize">—</span></div>
          <div class="shot"><img id="origImg" alt="Original image"></div>
          <div class="foot" id="origMeta">—</div>
        </div>
        <div class="pane after">
          <div class="head"><span>Compressed</span><span class="size" id="newSize">—</span></div>
          <div class="shot"><img id="newImg" alt="Compressed image"></div>
          <div class="foot" id="newMeta">—</div>
        </div>
      </div>

      <div class="summary">
        <span class="badge good" id="savedBadge">—</span>
        <span class="saved-line" id="savedLine"></span>
        <span class="spacer"></span>
        <a class="btn btn-primary" id="download" download>Download</a>
      </div>
    </div>
  </div>

  <footer>
    Sab kuch is server par process hota hai aur turant delete ho jaata hai — koi file save nahi hoti.<br>
    Target size na mile to image thodi chhoti (resize) kar di jaati hai.
  </footer>
</div>

<script>
(function () {
  "use strict";

  var drop      = document.getElementById("drop");
  var fileInput = document.getElementById("file");
  var chip      = document.getElementById("chip");
  var thumb     = document.getElementById("thumb");
  var fileName  = document.getElementById("fileName");
  var fileSub   = document.getElementById("fileSub");
  var clearBtn  = document.getElementById("clear");
  var minSize   = document.getElementById("minSize");
  var maxSize   = document.getElementById("maxSize");
  var unit      = document.getElementById("unit");
  var format    = document.getElementById("format");
  var presets   = document.getElementById("presets");
  var goBtn     = document.getElementById("go");
  var resetBtn  = document.getElementById("reset");
  var msg       = document.getElementById("msg");
  var result    = document.getElementById("result");
  var origImg   = document.getElementById("origImg");
  var newImg    = document.getElementById("newImg");
  var origSize  = document.getElementById("origSize");
  var newSize   = document.getElementById("newSize");
  var origMeta  = document.getElementById("origMeta");
  var newMeta   = document.getElementById("newMeta");
  var savedBadge= document.getElementById("savedBadge");
  var savedLine = document.getElementById("savedLine");
  var download  = document.getElementById("download");

  var currentFile = null;
  var currentUrl = null;
  var lastResult = null;

  function human(bytes) {
    if (bytes < 1024) return bytes + " B";
    if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + " KB";
    return (bytes / (1024 * 1024)).toFixed(2) + " MB";
  }

  function showMsg(text, kind) {
    msg.textContent = text;
    msg.className = "msg show " + (kind || "error");
  }

  function clearMsg() {
    msg.className = "msg";
    msg.textContent = "";
  }

  function setBusy(busy) {
    goBtn.disabled = busy;
    goBtn.innerHTML = busy ? '<span class="spin"></span>Compress ho raha hai…' : "Compress";
  }

  function clearPresetHighlight() {
    Array.prototype.forEach.call(presets.querySelectorAll(".chip"), function (c) {
      c.classList.remove("active");
    });
  }

  function loadFile(file) {
    if (!file) return;
    if (!/^image\//.test(file.type)) {
      showMsg("Ye image file nahi lag rahi. JPG, PNG, WEBP, GIF, BMP ya TIFF chunein.", "error");
      return;
    }
    clearMsg();
    result.classList.remove("show");

    if (currentUrl) URL.revokeObjectURL(currentUrl);
    currentFile = file;
    currentUrl = URL.createObjectURL(file);

    thumb.src = currentUrl;
    fileName.textContent = file.name;
    chip.classList.add("show");

    var img = new Image();
    img.onload = function () {
      fileSub.textContent = human(file.size) + " · " + img.naturalWidth + " × " + img.naturalHeight + " px";
      origImg.src = currentUrl;
      origSize.textContent = human(file.size);
      origMeta.textContent = img.naturalWidth + " × " + img.naturalHeight + " px";
    };
    img.onerror = function () {
      fileSub.textContent = human(file.size);
    };
    img.src = currentUrl;
  }

  function resetAll() {
    currentFile = null;
    lastResult = null;
    fileInput.value = "";
    chip.classList.remove("show");
    result.classList.remove("show");
    clearMsg();
    if (currentUrl) { URL.revokeObjectURL(currentUrl); currentUrl = null; }
    minSize.value = "0";
    maxSize.value = "100";
    unit.value = "KB";
    format.value = "auto";
    clearPresetHighlight();
    presets.querySelector('[data-max="100"]').classList.add("active");
  }

  /* ---------- picking a file ---------- */

  drop.addEventListener("click", function () { fileInput.click(); });
  drop.addEventListener("keydown", function (e) {
    if (e.key === "Enter" || e.key === " ") { e.preventDefault(); fileInput.click(); }
  });
  fileInput.addEventListener("change", function () { loadFile(fileInput.files[0]); });

  ["dragenter", "dragover"].forEach(function (evt) {
    drop.addEventListener(evt, function (e) {
      e.preventDefault();
      drop.classList.add("over");
    });
  });
  ["dragleave", "drop"].forEach(function (evt) {
    drop.addEventListener(evt, function (e) {
      e.preventDefault();
      drop.classList.remove("over");
    });
  });
  drop.addEventListener("drop", function (e) {
    if (e.dataTransfer && e.dataTransfer.files.length) loadFile(e.dataTransfer.files[0]);
  });

  window.addEventListener("paste", function (e) {
    var items = (e.clipboardData || {}).items || [];
    for (var i = 0; i < items.length; i++) {
      if (items[i].type.indexOf("image/") === 0) {
        var file = items[i].getAsFile();
        if (file) { loadFile(file); break; }
      }
    }
  });

  clearBtn.addEventListener("click", function (e) {
    e.stopPropagation();
    resetAll();
  });

  /* ---------- presets ---------- */

  presets.addEventListener("click", function (e) {
    var chipBtn = e.target.closest(".chip");
    if (!chipBtn) return;
    clearPresetHighlight();
    chipBtn.classList.add("active");
    minSize.value = "0";
    unit.value = chipBtn.getAttribute("data-unit") || "KB";
    maxSize.value = chipBtn.getAttribute("data-max");
    clearMsg();
  });

  [minSize, maxSize, unit, format].forEach(function (el) {
    el.addEventListener("input", clearPresetHighlight);
  });

  /* ---------- compress ---------- */

  goBtn.addEventListener("click", function () {
    if (!currentFile) {
      showMsg("Pehle koi image choose karein.", "error");
      drop.focus();
      return;
    }
    if (!maxSize.value || parseFloat(maxSize.value) <= 0) {
      showMsg("Max size 0 se bada daalein.", "error");
      maxSize.focus();
      return;
    }
    if (parseFloat(minSize.value || 0) >= parseFloat(maxSize.value)) {
      showMsg("Min size, max size se chhota hona chahiye.", "error");
      minSize.focus();
      return;
    }

    clearMsg();
    setBusy(true);

    var body = new FormData();
    body.append("image", currentFile);
    body.append("min_size", minSize.value || "0");
    body.append("max_size", maxSize.value);
    body.append("unit", unit.value);
    body.append("format", format.value);

    fetch("/compress", { method: "POST", body: body })
      .then(function (res) {
        return res.json().then(function (data) { return { ok: res.ok, data: data }; });
      })
      .then(function (out) {
        if (!out.ok) {
          showMsg(out.data.error || "Compression fail hui.", "error");
          return;
        }
        render(out.data);
      })
      .catch(function () {
        showMsg("Server se connect nahi ho paya. Kya app chal raha hai?", "error");
      })
      .then(function () { setBusy(false); });
  });

  function render(data) {
    lastResult = data;

    newImg.src = data.image_url;
    newSize.textContent = data.compressed_size;
    newMeta.textContent = data.width + " × " + data.height + " px · " + data.format.toUpperCase();

    var saved = data.saved_percent;
    savedBadge.textContent = saved > 0 ? saved.toFixed(0) + "% chhoti" : "size badh gaya";
    savedBadge.className = "badge " + (saved > 0 ? "good" : "warn");

    savedLine.textContent = data.original_size + " → " + data.compressed_size;

    var base = (currentFile.name || "image").replace(/\.[^.]+$/, "");
    download.href = data.image_url;
    download.download = base + "-compressed." + data.extension;

    result.classList.add("show");
    result.scrollIntoView({ behavior: "smooth", block: "nearest" });

    if (data.note) {
      showMsg(data.note, "warn");
    } else if (!data.in_range) {
      showMsg("Requested range exactly nahi mil payi — ye sabse kareeb size hai.", "warn");
    } else {
      clearMsg();
    }
  }

  resetBtn.addEventListener("click", resetAll);

  resetAll();
})();
</script>
</body>
</html>
"""


if __name__ == "__main__":
    app.run(
        host="0.0.0.0",
        port=int(os.environ.get("PORT", 5000)),
        debug=os.environ.get("FLASK_DEBUG") == "1",
    )
