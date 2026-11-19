# Stream Clips Plan — long gaming streams → vertical clips

> Status: **PLANNED, no code written yet** (2026-11-19).
> Owner: gallih. Any agent (Claude Code, Pi, Codex) that works on this feature follows this file.
> Update the task checkboxes and the **Progress log** at the bottom in the same commit as the code.

## 1. Goal

Turn a long stream VOD (Twitch / YouTube, e.g. an 8-hour GTA VI stream) into ready-to-post
9:16 clips with:

- moments found from **chat activity + audio spikes**, not only sparse frames or a full transcript;
- **subtitles** with word timing (reuse existing captions);
- **face tracking** (reuse `framing.py`) and a new **facecam-over-gameplay `stack` layout**;
- one command: `autoclip stream <url>`.

**Done means:** a real ≥3-hour stream VOD with chat produces ≥5 clips end to end on gangnamPC
(GTX 1650 4 GB, 15 GB RAM), the quality gate passes, and every task below is checked.

### Out of scope (do not touch)

- Publish flow (`publish_export.py`, `*_publisher.py`), HTML title templates, release/tag workflows.
- Refactoring existing pipeline steps 1–6 or `framing.py` internals beyond the hooks listed here.
- Recording while the stream is live is **Phase 5, optional**. VODs and finished streams come first.

## 2. Guidelines every agent must follow

### Repo rules (from `AGENTS.md`, `CLAUDE.md`, `DESIGN.md`)
1. Read `docs/TESTING_ACCEPTANCE.md` before changing code.
2. Visual or UI decisions follow `DESIGN.md` (Calm Premium: near-monochrome, one restrained blue).
3. Every user-facing string goes into **all 8 locales** (`frontend/src/i18n/locales/{en,es,fr,ja,ko,pt,ru,zh}.json`).
   Backend `UserFacingError` messages follow the existing convention (Chinese source text, translated in the frontend).
4. Before claiming a task passes, run
   `python scripts/quality_gate.py --group backend --report /tmp/qg-<task>.json`
   (and `--group frontend` for UI tasks). Before closing a phase, run `--group all`.
5. Never tag, release, publish, or promote. Paid model calls need the owner's OK in the session.
6. Use isolated data directories in tests. Never commit user media, cookies, `.env`, or chat dumps.
7. Changing an event producer means updating `backend/core/event_catalog.json` and
   `docs/analytics/event_catalog.json` together.

### Owner's working rules (from the global `CLAUDE.md`)
1. **Tests first.** Write the failing test, then implement until it passes. Keep both in the same commit.
2. **State your assumptions** before any non-trivial task. If something conflicts, stop and ask.
3. **Scope discipline.** Touch only the files a task lists. Don't remove comments or "clean up" adjacent code.
4. **Simplest thing that works.** Use the stdlib, ffmpeg or an existing module before adding a dependency.
   New heavy dependencies are installed on demand into the data dir, the way OpenCV/Whisper already are
   (see `framing.start_install`), never added to `requirements.txt`.
5. Explicit error handling. A signal source that fails (chat missing, ffmpeg loudness error) **degrades**
   to the remaining signals, logs a warning, and never fails the whole job.
6. Commit messages use conventional commits, e.g. `feat(stream): chat rate signal`.
7. After a refactor, list any now-dead code and ask before deleting it.
8. Before ending a session, update `.agent-handoff.md` at the repo root.

### Branching
- Branch: `feat/stream-clips`, cut from the current working tree.
- ⚠️ The working tree has **uncommitted T2 `source_window` work** (37 files, e.g. `intelligence.py`, `jobs.py`,
  `studio.py`). Phase 2 depends on it. **Ask the owner** whether to commit it first (recommended:
  a separate commit `feat(studio): T2 source window`) before you start Phase 1. Never discard or rewrite it.

## 3. What already exists (reuse it, don't rebuild it)

| Need | Existing code |
|---|---|
| URL download (yt-dlp, cookies, retries, platform subtitles) | `backend/services/studio/jobs.py` → `download()`, `_fetch_platform_subtitles()` |
| Time-range analysis (single window, absolute timestamps) | `source_window` in `api/v1/studio.py:247`, `jobs.py:856/903`, `intelligence.analyze(window=)` |
| Vision highlight scan (gameplay-aware prompt, ≤60 frames) | `backend/services/studio/intelligence.py` → `analyze()`, `select_highlights()` |
| Transcript scoring | `backend/pipeline/step1…step5`, `pipeline/clip_finder.py` |
| ASR (Whisper / SenseVoice / cloud) + word timing sidecar | `utils/speech_recognizer.py`, `services/whisper_runtime.py`, `utils/word_timing.py` |
| Face detection (YuNet ONNX), speaker tracking, smoothed crop | `backend/services/studio/framing.py` (`load_cv2`, `_detector`, `auto_frame`, `layout_filter`) |
| Sentence-boundary snapping | `backend/services/studio/boundaries.py` |
| Loudness -14 LUFS two-pass | `backend/services/studio/audio.py` |
| Render graph per layout | `backend/services/studio/render.py` (~line 300–360) |
| Draft model (`layout: fit/crop/blur/window`) | `backend/services/studio/models.py:170` |
| CLI subcommands | `backend/cli.py` (`sub.add_parser`, `cmd_*`) |
| MCP tools | `backend/mcp_server.py` |

Known limits that this plan removes:
- `planning.py:22 VISUAL_MAX_SOURCE_SEC = 7200` and `intelligence.py:444` reject sources over 2 h.
- 60 frames over 8 h is one frame every 8 minutes, so the vision scan can't find stream moments.
- Full-stream Whisper on a GTX 1650 takes hours, so transcribe candidate windows only.

## 4. Architecture

```
URL ─► download() ──► raw/input.mp4
            └──────► raw/chat.jsonl          (T1: new, optional)
                          │
raw/input.mp4 ─► stream_signals.score() ◄────┘   (T2: new module)
                     │  per-second score = chat rate z + emote/keyword hits + loudness spike
                     ▼
              find_windows() → [ {start, end, score, reasons} … ]  (top N, merged, padded)
                     │
      for each window: existing analyze(window=…) / windowed ASR + transcript scoring
                     ▼
              drafts (absolute timestamps) ─► layout 'stack' (T4) or existing crop
                     ▼
              render.py ─► 1080×1920 MP4, captions, -14 LUFS
```

Decision: `source_window` stays a **single** window. The stream route loops over windows and calls the
existing single-window code once per window, then merges drafts. Don't change the T2 API to a list.

## 5. Tasks

Legend: `[ ]` todo · `[~]` in progress (put the agent and date next to it) · `[x]` done (add the commit SHA).

### Phase 0 — Setup
- [x] **T0.1** Resolve the uncommitted T2 work with the owner (see Branching). Create `feat/stream-clips`.
- [~] **T0.2** Baseline: `python scripts/quality_gate.py --group all --report /tmp/qg-baseline.json`.
  Record pass/fail counts in the Progress log so later failures can be told apart from existing ones.
- [ ] **T0.3** Get test media: one short (10–20 min) Twitch or YouTube VOD **with chat**, and one ≥3 h VOD.
  Store them outside the repo (`~/autoclip-testdata/`) and note the URLs in the Progress log (no media in git).

### Phase 1 — Chat ingest
- [x] **T1.1** Chat format + parser. New `backend/services/studio/stream_chat.py`:
  `load_chat(path) -> list[tuple[float, str]]` (seconds from video start, message text).
  Canonical file: `raw/chat.jsonl`, one `{"t": 12.3, "text": "KEKW"}` per line.
  Tests: `backend/tests/test_stream_chat.py` covering valid lines, a malformed line skipped,
  unsorted input sorted, negative or non-finite `t` dropped, empty file → `[]`.
- [x] **T1.2** YouTube chat. In `jobs.download()`, when the info dict says `was_live` / `live_status` is
  `was_live` or `post_live`, also request yt-dlp subtitle `live_chat` (no new dependency). Convert
  `input.live_chat.json` to `chat.jsonl` using `videoOffsetTimeMsec`. Failure = warning only.
  Test with a small fixture JSON (no network) in `test_stream_chat.py`.
- [x] **T1.3** Twitch chat **spike first** (≤1 h). Check whether yt-dlp `2026.8.19` still exposes Twitch
  `rechat`. If it does, use it as in T1.2. If not, add `chat-downloader` as an **on-demand install**
  following the `framing.start_install` pattern. Write the finding in the Progress log before you build it.
- [x] **T1.4** Record `chat_source` (`youtube` / `twitch` / `none`) and message count in `source_meta`.

**Phase 1 acceptance:** downloading the T0.3 short VOD produces `raw/chat.jsonl` with a plausible message count;
a VOD without chat still downloads and imports exactly as before (regression test).

### Phase 2 — Moment pre-pass (core)
- [ ] **T2.1** New `backend/services/studio/stream_signals.py` with **pure** functions (no I/O) first:
  - `chat_rate(messages, duration) -> list[float]`: messages per second, then a z-score against a rolling
    5-minute baseline.
  - `chat_hits(messages, duration, lexicon) -> list[float]`: per-second hits for emotes and phrases.
    The lexicon is a module constant (`KEKW, LUL, OMEGALUL, LMAO, W, 💀, Pog, no way, ...`); compare case-insensitively.
  - `combine(signals, weights) -> list[float]` and `find_windows(score, *, top_n=8, pre=20, post=10, min_gap=30, max_len=90)`
    → merged, non-overlapping windows sorted by score, each with `reasons` (which signals fired).
  Tests in `test_stream_signals.py`: synthetic chat burst at t=600 gives a window covering 580–610;
  two bursts 15 s apart merge; `top_n` is respected; flat input gives `[]`; a missing chat signal is still handled.
- [ ] **T2.2** Audio signal: `loudness_series(video) -> list[float]` using ffmpeg `ebur128` momentary loudness
  (or `astats`), one value per second, converted to a spike z-score. Must stream through an 8-h file
  without loading it into memory. Add a timeout, and return `[]` on failure.
  Test the parser with captured ffmpeg stderr text (no real decode in unit tests).
- [ ] **T2.3** `score_source(video, chat_path) -> list[Window]`, which wires T2.1 + T2.2. Default weights:
  chat rate 0.45, chat hits 0.35, audio 0.20. With no chat, the weights fall back to audio only. Weights are
  constants, not settings (YAGNI).
- [ ] **T2.4** Duration caps: let a source over 2 h through **only when** the stream route provides
  windows. The cap then applies to each window span, not the source (`planning.py:22`, `intelligence.py:444`).
  Regression test: a 3-h source without windows is still rejected with the existing message.
- [ ] **T2.5** Stream route in `jobs.py`: when `import_options.route == 'stream'` (new option), run
  `score_source`, then the existing analyze/scoring per window with `window={'start','end'}`, then merge
  drafts and dedupe with the existing overlap rule (`subtitle_highlights.make_highlights` uses >80%).
  Cancellation and progress use the existing helpers. Emit progress per window.
- [ ] **T2.6** Store the windows and reasons in the project (`stream_windows`) so the UI and CLI can show
  why a clip was picked.

**Phase 2 acceptance:** on the ≥3 h VOD, the top 8 windows include at least 5 that a human agrees are
real moments (record the judgement in the Progress log). The pre-pass takes <10 min on gangnamPC.

### Phase 3 — Windowed transcription
- [ ] **T3.1** Transcribe only the windows: cut the window audio with ffmpeg (`-ss/-t`, 16 kHz mono),
  run the existing recogniser, then shift every segment and word time by `window.start`. Write a merged
  SRT + `.words.json` sidecar that `word_timing.py` accepts (same hash/validation rules).
- [ ] **T3.2** Tests: offset shifting is exact; overlapping windows don't duplicate cues; the sidecar
  validates; a window whose ASR fails is skipped while the others continue.
- [ ] **T3.3** Default ASR model on 4 GB VRAM: Whisper `small` int8 (or what `whisper_runtime` already
  picks for low VRAM). Don't add a new model picker.

**Phase 3 acceptance:** captions on stream clips line up within ±0.3 s (spot-check 3 clips by eye) and the
karaoke sidecar is used (not the sentence-only fallback).

### Phase 4 — `stack` layout (facecam on top, gameplay below)
- [ ] **T4.1** Facecam detection, pure plus a thin I/O wrapper in `backend/services/studio/facecam.py`:
  sample ~30 frames spread over the source, run YuNet through `framing.load_cv2()` and `framing._detector()`,
  and keep faces that are small (<25% frame width) and **stationary** (box centre spread < 3%).
  Return the facecam rect as fractions `(x, y, w, h)`, padded by 35% and clamped to the frame, or `None`.
  Tests on synthetic detection lists: a stable corner face gives a rect; a moving face gives `None`;
  no faces gives `None`; padding is clamped at the edges.
- [ ] **T4.2** Model: add `'stack'` to `Draft.layout` and `facecam: tuple[float,float,float,float] | None = None`
  to `Draft` (`models.py`; the model uses `extra='forbid'`, so this is required). Old drafts must still load
  (regression test with a saved draft JSON that has no `facecam`).
- [ ] **T4.3** Render: in `render.py` next to the `crop` branch (~line 353), add a `stack` filter:
  top = facecam crop scaled to 1080×768, bottom = gameplay centre crop scaled to 1080×1152, `vstack`.
  If `facecam is None`, fall back to the existing `crop` path. Subtitles sit just above the seam
  (bottom of the top panel); reuse the existing subtitle positioning hook rather than adding a new one.
  Test: the filter-graph string for a known rect, and the fallback when the rect is missing.
- [ ] **T4.4** Auto-pick: the stream route sets `layout='stack'` when facecam detection succeeds, otherwise `crop`.
- [ ] **T4.5** Frontend: add the `stack` option in `DraftSettingsPanel.tsx`, plus a simple drag-to-adjust
  facecam box in `StudioEditor.tsx` (rect only, following `DESIGN.md`). Strings go in all 8 locales.
  Run the frontend lint, types, tests and build via the quality gate.

**Phase 4 acceptance:** a stream with a facecam overlay renders with face top and gameplay bottom, with
no black bars or stretching; a stream without a facecam renders exactly like today's crop.

### Phase 5 — Live recording (optional, only after Phases 1–4 are done)
- [ ] **T5.1** `autoclip record <url>`: yt-dlp/streamlink into 5-minute `ffmpeg -f segment` chunks, with chat logged
  live to `chat.jsonl`, and resumable after a crash.
- [ ] **T5.2** Every finished hour, concatenate chunks and run the Phase 2 pre-pass on that hour only.

### Phase 6 — CLI and MCP
- [ ] **T6.1** `autoclip stream <url> [--top N] [--layout auto|stack|crop] [--json]` in `backend/cli.py`,
  sharing the data dir with the desktop app like `run`/`produce` do. Test in `test_cli.py`.
- [ ] **T6.2** MCP tool `clip_stream` in `backend/mcp_server.py`, mirroring `start_clip_job`/`get_job_status`.
- [ ] **T6.3** Docs: a short section in `docs/CLI_AND_MCP.md` and `README-EN.md` (only the English README;
  ask the owner before touching the other 7 READMEs).

### Phase 7 — Final acceptance
- [ ] **T7.1** `python scripts/quality_gate.py --group all --report /tmp/qg-final.json`: no new failures vs the T0.2 baseline.
- [ ] **T7.2** End-to-end on gangnamPC with the ≥3 h VOD: ≥5 clips, 1080×1920, H.264/AAC, -14 LUFS (±1),
  15–90 s each, captions present, `stack` layout used when there is a facecam. Record the timings.
- [ ] **T7.3** Update `HANDOFF.md` (status section only), `CHANGELOG.md` (Unreleased), and `.agent-handoff.md`.
  Open a PR from `feat/stream-clips`. **Don't merge, tag or release.**

## 6. Risks / pitfalls

- **Disk:** an 8-h 1080p VOD is about 15–25 GB; gangnamPC has about 87 GB free. Add a stream-route option to
  download at ≤720p, and a disk-space precheck with a clear error.
- **Rights:** clips of other people's streams need the streamer's permission or a clipper programme.
  Don't add any feature that strips watermarks or overlays.
- **Chat offsets:** chat timestamps are relative to the stream start, and a VOD can have a trimmed start.
  Align using the VOD offset field, not wall-clock time.
- **Vision cost:** N windows means N vision calls. Keep `top_n` default 8, and never auto-raise it.
- **4 GB VRAM:** don't run Whisper and OpenCV detection in parallel on the GPU. Run them in sequence.

## 7. Progress log

Newest first. One line per session: date · agent · tasks touched · result · commit SHA.

- 2026-11-19 · Claude Code (Sonnet 5.5) · Phase 1 done (T1.1–T1.4). Spike T1.3: yt-dlp 2026.08.19 has **no Twitch chat**; YouTube `live_chat`
  works. Twitch uses `chat-downloader` (0.2.8) installed on demand into `<data>/chat-runtime` (best-effort; untested against live Twitch).
  Source type gate is `subtitles.live_chat` (more reliable than `live_status`). 13 new tests pass. T0.1 done: pre-existing work committed as `e609d4b8`.
  T0.2 baseline in progress (full backend run). T0.3 (real test VODs) still open: needs the owner's URLs.

- 2026-11-19 · Claude Code (Opus 5.5) · plan written after reading the repo on gangnamPC · no code changes.
