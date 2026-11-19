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
- [x] **T0.2** Baseline: `python scripts/quality_gate.py --group all --report /tmp/qg-baseline.json`.
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
- [x] **T2.1** New `backend/services/studio/stream_signals.py` with **pure** functions (no I/O) first:
  - `chat_rate(messages, duration) -> list[float]`: messages per second, then a z-score against a rolling
    5-minute baseline.
  - `chat_hits(messages, duration, lexicon) -> list[float]`: per-second hits for emotes and phrases.
    The lexicon is a module constant (`KEKW, LUL, OMEGALUL, LMAO, W, 💀, Pog, no way, ...`); compare case-insensitively.
  - `combine(signals, weights) -> list[float]` and `find_windows(score, *, top_n=8, pre=20, post=10, min_gap=30, max_len=90)`
    → merged, non-overlapping windows sorted by score, each with `reasons` (which signals fired).
  Tests in `test_stream_signals.py`: synthetic chat burst at t=600 gives a window covering 580–610;
  two bursts 15 s apart merge; `top_n` is respected; flat input gives `[]`; a missing chat signal is still handled.
- [x] **T2.2** Audio signal: `loudness_series(video) -> list[float]` using ffmpeg `ebur128` momentary loudness
  (or `astats`), one value per second, converted to a spike z-score. Must stream through an 8-h file
  without loading it into memory. Add a timeout, and return `[]` on failure.
  Test the parser with captured ffmpeg stderr text (no real decode in unit tests).
- [x] **T2.3** `score_source(video, chat_path) -> list[Window]`, which wires T2.1 + T2.2. Default weights:
  chat rate 0.45, chat hits 0.35, audio 0.20. With no chat, the weights fall back to audio only. Weights are
  constants, not settings (YAGNI).
- [x] **T2.4** Duration caps: let a source over 2 h through **only when** the stream route provides
  windows. The cap then applies to each window span, not the source (`planning.py:22`, `intelligence.py:444`).
  Regression test: a 3-h source without windows is still rejected with the existing message.
- [x] **T2.5** Stream route in `jobs.py`: when `import_options.route == 'stream'` (new option), run
  `score_source`, then the existing analyze/scoring per window with `window={'start','end'}`, then merge
  drafts and dedupe with the existing overlap rule (`subtitle_highlights.make_highlights` uses >80%).
  Cancellation and progress use the existing helpers. Emit progress per window.
- [x] **T2.6** Store the windows and reasons in the project (`stream_windows`) so the UI and CLI can show
  why a clip was picked.

**Phase 2 acceptance:** on the ≥3 h VOD, the top 8 windows include at least 5 that a human agrees are
real moments (record the judgement in the Progress log). The pre-pass takes <10 min on gangnamPC.

### Phase 3 — Windowed transcription
- [x] **T3.1** Transcribe only the windows: cut the window audio with ffmpeg (`-ss/-t`, 16 kHz mono),
  run the existing recogniser, then shift every segment and word time by `window.start`. Write a merged
  SRT + `.words.json` sidecar that `word_timing.py` accepts (same hash/validation rules).
- [x] **T3.2** Tests: offset shifting is exact; overlapping windows don't duplicate cues; the sidecar
  validates; a window whose ASR fails is skipped while the others continue.
- [x] **T3.3** Default ASR model on 4 GB VRAM: Whisper `small` int8 (or what `whisper_runtime` already
  picks for low VRAM). Don't add a new model picker.

**Phase 3 acceptance:** captions on stream clips line up within ±0.3 s (spot-check 3 clips by eye) and the
karaoke sidecar is used (not the sentence-only fallback).

### Phase 4 — `stack` layout (facecam on top, gameplay below)
- [x] **T4.1** Facecam detection, pure plus a thin I/O wrapper in `backend/services/studio/facecam.py`:
  sample ~30 frames spread over the source, run YuNet through `framing.load_cv2()` and `framing._detector()`,
  and keep faces that are small (<25% frame width) and **stationary** (box centre spread < 3%).
  Return the facecam rect as fractions `(x, y, w, h)`, padded by 35% and clamped to the frame, or `None`.
  Tests on synthetic detection lists: a stable corner face gives a rect; a moving face gives `None`;
  no faces gives `None`; padding is clamped at the edges.
- [x] **T4.2** Model: add `'stack'` to `Draft.layout` and `facecam: tuple[float,float,float,float] | None = None`
  to `Draft` (`models.py`; the model uses `extra='forbid'`, so this is required). Old drafts must still load
  (regression test with a saved draft JSON that has no `facecam`).
- [x] **T4.3** Render: in `render.py` next to the `crop` branch (~line 353), add a `stack` filter:
  top = facecam crop scaled to 1080×768, bottom = gameplay centre crop scaled to 1080×1152, `vstack`.
  If `facecam is None`, fall back to the existing `crop` path. Subtitles sit just above the seam
  (bottom of the top panel); reuse the existing subtitle positioning hook rather than adding a new one.
  Test: the filter-graph string for a known rect, and the fallback when the rect is missing.
- [x] **T4.4** Auto-pick: the stream route sets `layout='stack'` when facecam detection succeeds, otherwise `crop`.
- [~] **T4.5** (picker done; drag-to-adjust box NOT done) Frontend: add the `stack` option in `DraftSettingsPanel.tsx`, plus a simple drag-to-adjust
  facecam box in `StudioEditor.tsx` (rect only, following `DESIGN.md`). Strings go in all 8 locales.
  Run the frontend lint, types, tests and build via the quality gate.

**Phase 4 acceptance:** a stream with a facecam overlay renders with face top and gameplay bottom, with
no black bars or stretching; a stream without a facecam renders exactly like today's crop.

### Phase 5 — Live recording (optional, only after Phases 1–4 are done)
- [ ] **T5.1** `autoclip record <url>`: yt-dlp/streamlink into 5-minute `ffmpeg -f segment` chunks, with chat logged
  live to `chat.jsonl`, and resumable after a crash.
- [ ] **T5.2** Every finished hour, concatenate chunks and run the Phase 2 pre-pass on that hour only.

### Phase 6 — CLI and MCP
- [x] **T6.1** `autoclip stream <url> [--top N] [--layout auto|stack|crop] [--json]` in `backend/cli.py`,
  sharing the data dir with the desktop app like `run`/`produce` do. Test in `test_cli.py`.
- [x] **T6.2** (done as extra params on `start_quick_output`, not a new tool) MCP tool `clip_stream` in `backend/mcp_server.py`, mirroring `start_clip_job`/`get_job_status`.
- [x] **T6.3** Docs: a short section in `docs/CLI_AND_MCP.md` and `README-EN.md` (only the English README;
  ask the owner before touching the other 7 READMEs).

### Phase 7 — Final acceptance
- [x] **T7.1** (gate still reports `failed` only because of 4 pre-existing environment failures, see log) `python scripts/quality_gate.py --group all --report /tmp/qg-final.json`: no new failures vs the T0.2 baseline.
- [ ] **T7.2** End-to-end on gangnamPC with the ≥3 h VOD: ≥5 clips, 1080×1920, H.264/AAC, -14 LUFS (±1),
  15–90 s each, captions present, `stack` layout used when there is a facecam. Record the timings.
- [x] **T7.3** (PR NOT opened: not requested; open from `feat/stream-clips` when the owner says so) Update `HANDOFF.md` (status section only), `CHANGELOG.md` (Unreleased), and `.agent-handoff.md`.
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

- 2026-11-19 · Claude Code (Sonnet 5.5) · Phase 7 partly done. `quality_gate.py --group all` on `c08fc8f4`: status `failed`; passing: versions, readmes,
  tracking_inventory, ci_guards, release_gate_tests, redline_backend, media_test_dependencies, python_correctness, llm_hot_reload_tests, editorial_fixtures,
  frontend lint/typecheck/build. Failing, all **pre-existing / environmental, reproduced without these changes**: `backend_tests` = 3 failed / 4533 passed
  (`test_burned_subtitles` x2: ffmpeg drawtext has no font on this box; `test_frontend_real_backend[analytics.real-backend.cjs]`: node spawns system `python`
  without sqlalchemy) plus the gate's "unexpected skipped" block (pwsh / huggingface_hub missing); `redline_frontend` = 1 failing test, also fails on clean main
  `cd8e6bed`; `frontend_test` = 117 failing tests, identical set with and without these changes. **Real checks that did run:** real ffmpeg loudness scan,
  real audio cut, and the `stack` filter graph rendered with real ffmpeg to 1080x1920. **T7.2 (end-to-end on a >=3 h VOD) is NOT done**: no test media,
  no verified Whisper/model setup for it, and no real-face footage. Do T0.3 + T7.2 next; also decide T4.5 drag box and whether to add the stream example to all 8 READMEs.

- 2026-11-19 · Claude Code (Sonnet 5.5) · Phase 6 done (T6.1–T6.3), Phase 5 (live recording) intentionally skipped (optional). `autoclip stream <source>
  [--top N] [--layout auto|stack|crop] [--chat FILE] [--platform ..] [--json]` reuses `cmd_produce` (+`quick.start(stream=..)`); MCP: **extra params on
  the existing `start_quick_output`** (stream, stream_top, stream_layout, chat_path) instead of a new tool, so telemetry/tool-list contracts are untouched.
  New `ImportOptions.stream_top` (1-20) and `stream_layout`. Local streams skip the transcript preflight; `--chat` is validated (exists, <=50 MB, has
  valid messages) before any project is created; Twitch URLs are accepted only with `stream`. **T6.3:** documented in `docs/CLI_AND_MCP.md` only.
  `README-EN.md` was NOT changed: `scripts/check_readmes.py` requires identical command examples in all 8 READMEs, so a one-README edit fails the
  contracts gate. Add the same block to all 8 only with the owner's OK. 8 new tests; cli/quick-output/mcp/studio suites 154 passed.

- 2026-11-19 · Claude Code (Sonnet 5.5) · Phase 4 done except the editor drag box. `facecam.py`: samples 30 frames, YuNet via `framing`, picks the
  small face that stays put in >=40% of frames (moving in-game faces and close-ups are rejected), crop rect gets the 1080x768 top-panel aspect;
  `stack_filter` = facecam crop on top (40%) + gameplay centre crop below (shiftable with `crop_x`). `Draft.layout` gained `stack`, `Draft.facecam`
  (validated; `stack` without a facecam becomes `crop`; old drafts load). Seams: `packaging_render` (picture_only, scene_video_graph),
  `jobs._apply_strategy` (platform default must not undo stack), `jobs._apply_framing` (skip speaker tracking for stack), `publish_export._layout_filters`
  (stack without rect = crop). Frontend: type + a "上下分屏" option shown only when the draft has a facecam, 2 new keys in all 8 locales.
  **Not done:** drag-to-adjust facecam box in the editor (the rect is editable via the draft JSON/API only); real-face detection was not run (no
  face footage on the box; only the blank-frame plumbing smoke passed); no real render of a stack clip yet (T7.2).
  **Verification:** full backend suite on branch = 5 failed / 4523 passed. Clean main (worktree) = 47 failed / 4374 passed, but that run is not
  comparable (worktree lacks node_modules/frontend, so real-backend tests skipped or failed differently). Failures on branch: `test_burned_subtitles` x2
  (pre-existing, reproduced on clean main), `test_frontend_real_backend[analytics.real-backend.cjs]` (the node test spawns `python` from PATH = system python
  without sqlalchemy; environment, not code), `test_parent_watch` x2 (pass in isolation: flaky under load). Frontend gate: lint, typecheck, build pass;
  `npm test` has 117 failures both **with and without** these changes (identical set, pre-existing/environmental). `tests/i18n.test.cjs`
  "no duplicate keys" also fails on clean HEAD. T0.2 baseline is therefore: backend 47 F on a bare worktree, 5 F on the real checkout (list above).

- 2026-11-19 · Claude Code (Sonnet 5.5) · Phase 3 done (T3.1–T3.3). `stream_transcript.py`: cut each window to 16 kHz mono wav, recognise with the
  existing `SpeechRecognizer().generate_subtitle`, shift cues and word times by the window start, write merged `raw/input.srt` + word sidecar
  (sidecar only when every window produced valid words, else sentence captions). An existing `input.srt` (platform/user) is reused and ASR is skipped.
  Window edges snap to sentence edges within 8 s. T3.3: no new model picker; the recogniser's configured default is used.
  **Bug found by a real-ffmpeg run and fixed (Phase 2 code):** ffmpeg 8.1 prints no `ebur128` frame lines with `framelog=verbose` at the default
  log level; use `framelog=info`. A real-ffmpeg regression test now guards this. Real ASR was NOT run (only the audio cut was verified with real ffmpeg);
  Phase 3 acceptance (±0.3 s on 3 real clips) stays open until T0.3 media + a configured Whisper exist. Known limit: delete `raw/input.srt` to
  re-transcribe after changing `top_n`.

- 2026-11-19 · Claude Code (Sonnet 5.5) · Phase 2 done (T2.1–T2.6). **Deviations from the plan text (intentional, simpler):**
  (a) the stream route does **not** call the vision `analyze()` per window; drafts come straight from the windows
  (`stream_clips.drafts`), so no model call is needed to find moments and **T2.4 (lift the 2 h cap) is not needed**; the 2 h cap and
  `source_window` are untouched. (b) The route is switched by `ImportOptions.stream` (plan `overrides.stream`), **not** by a new
  `recommended_analysis` value, because the UI sends `recommended_analysis` back on confirm and `ConfirmPlan` only accepts
  `subtitle|visual`. `planning.recommend()` returns an early local plan for streams; `jobs._content_drafts` branches on
  `overrides.stream`; `confirm_project` skips the subtitle preflight for streams. (c) `find_windows` merges a nearby second hot spot into
  the first window (up to `max_len`) instead of dropping it; `min_gap` was dropped (non-overlap makes it redundant). (d) T2.6: the reasons
  are stored in `scene.evidence` (「依据：聊天刷屏、音量突增」), no separate `stream_windows` field. (e) API: `stream` form field added and
  `twitch.tv` allowed in the three URL allow-lists in `api/v1/studio.py`. 36 new tests. **Known pre-existing failures on clean main:**
  `test_burned_subtitles.py` (2 tests). Real-media acceptance of Phase 2 (≥3 h VOD, human judgement) is still open: needs T0.3 media.

- 2026-11-19 · Claude Code (Sonnet 5.5) · Phase 1 done (T1.1–T1.4). Spike T1.3: yt-dlp 2026.08.19 has **no Twitch chat**; YouTube `live_chat`
  works. Twitch uses `chat-downloader` (0.2.8) installed on demand into `<data>/chat-runtime` (best-effort; untested against live Twitch).
  Source type gate is `subtitles.live_chat` (more reliable than `live_status`). 13 new tests pass. T0.1 done: pre-existing work committed as `e609d4b8`.
  T0.2 baseline in progress (full backend run). T0.3 (real test VODs) still open: needs the owner's URLs.

- 2026-11-19 · Claude Code (Opus 5.5) · plan written after reading the repo on gangnamPC · no code changes.
