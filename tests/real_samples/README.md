# Real device samples

Drop real files here to verify detection and timestamps against actual devices
(for example clips straight from Ray-Ban Meta glasses and from an iPhone).
Media files in this folder are ignored by git.

For each file add `<filename>.expected.json`:

```json
{"device": "RAYBAN", "capture_time": "2026-07-17 19:00:49", "time_source": "Keys:CreationDate"}
```

Optional keys: `tz_mode` ("auto" | "system" | "manual") and `tz_zone`.
Then run `python -m pytest tests/test_real_samples.py -v`.

To see everything Takeline reads from a file:

```
python -m takeline inspect "tests/real_samples/video-277_singular_display.MOV"
```
