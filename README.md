# Image Compressor (Flask + Pillow)

Compress an image into a target size range — "under 100 KB", "between 100 and
200 KB", "under 2 MB" — which is what exam, government and job portals ask for.

Still a Flask app, as before. Same single-file shape, new UI and a working
compression engine.

## Run

```bash
pip install -r requirements.txt
python app.py                      # http://localhost:5000
```

On a server:

```bash
gunicorn app:app --workers 2 --threads 4 --timeout 120
```

`PORT` env var is honoured, so `python app.py` works on hosts that inject one.

## Deploy on Render

`render.yaml` is included, so **New + → Blueprint → pick this repo → Apply**
works. Or create a **Web Service** manually:

| Setting | Value |
| --- | --- |
| Runtime | Python |
| Build command | `pip install -r requirements.txt` |
| Start command | `gunicorn app:app --workers 2 --threads 4 --timeout 120` |
| Health check path | `/health` |

Free instance (512 MB) handles normal phone photos fine. Very large uploads
(40 MP+) are downscaled first, which keeps memory and time in check.

## API

`POST /compress` — `multipart/form-data`

| Field | Values |
| --- | --- |
| `image` | the file |
| `min_size` | number, default `0` |
| `max_size` | number, default `200` |
| `unit` | `KB` or `MB` |
| `format` | `auto`, `jpeg`, `webp`, `png` |

Returns JSON: `image_url` (a data URL, nothing is written to disk),
`original_size`, `compressed_size`, `saved_percent`, `width`, `height`,
`format`, `in_range`, `note`.

`GET /health` → `{"status": "ok"}`.

## What changed from the old version

**Bugs fixed**

- `original_size` was always wrong: `len(file.read())` ran *after* Pillow had
  read the file, so the pointer was at the end. The bytes are now read once and
  measured properly.
- PNG was never handled: `if JPEG or mode == RGB ... elif PNG` — the image had
  already been converted to RGB, so the PNG branch was dead code and every PNG
  came back as a JPEG data URL.
- The response always said `data:image/jpeg`, even for PNG output.
- Quality search started at 90 and could return a file *below* the minimum or
  wildly over the maximum; the loop also re-saved on every branch.
- Camera rotation (EXIF) was ignored, so phone photos came out sideways.
- No upload size limit, no format check — a 200 MB file or a `.txt` would
  either crash or produce a confusing error.
- Animated GIFs were silently flattened to the first frame.

**Now**

- Real size targeting: quality first, then resolution, until the file lands in
  the `[min, max]` window. If the target is impossible, you get the closest
  result plus a note instead of a lie.
- Output format of your choice: JPG, WEBP, PNG, or auto.
- PNG uses palette reduction (it has no quality knob) — photos should be sent
  as JPG/WEBP, and the UI says so.
- Animated GIF/WEBP keep their animation.
- EXIF stripped from the output, orientation applied first.
- Transparent images are flattened onto white only when the output is JPEG.
- If the original is already inside the requested range, it is returned
  untouched instead of being re-compressed into something bigger.
- Big inputs are capped at 12 MP internally (4 MP per frame for animations) so
  a 17 MB / 48 MP photo compresses in ~3–5 s instead of a minute.
- 40 MB upload cap, friendly JSON errors, `/health` endpoint.

## UI

Single card: drag-drop / click / paste, file chip with size and dimensions,
min–max–unit inputs, format picker, one-tap presets (20 KB → 1 MB), then a
side-by-side before/after with the saving percentage and a download button.
Dark mode follows the system setting. No external fonts, CSS or JS — the page
works with zero network requests beyond the app itself.
