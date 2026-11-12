# JSON → SRT converter (AirCaps A5S / ElevenLabs Scribe)

Converts speaker-diarized, per-word timed transcription JSON into a clean, Subtitle-Edit-friendly `.srt`. Pure Python 3.8+ standard library - no third-party packages are needed at runtime (tkinter ships with the python.org Windows installer).

```sh
json_to_srt/
├── tests/test_core.py    # unit tests (python -m unittest discover -s tests)
├── json_to_srt.exe       # self-contained command line interface (Python does not need to be installed).
├── json_to_srt_core.py   # converter engine + CLI (single source of truth for all settings)
├── json_to_srt_gui.py    # Windows tkinter front-end
├── JSONtoSRT.exe         # self-contained GUI (Python does not need to be installed).
└── README.md
```

## Supported inputs (auto-detected)

| Format | Detected by | Notes |
|---|---|---|
| **AirCaps A5S Async v1** | `model: a5s-…` / `utterances[].words[]` with `start`/`end`/`speaker` | `utterances` are used (one speaker each) so overlapping speech is never interleaved word-by-word. No SDH events. |
| **ElevenLabs Scribe v3 - segments export** | `segments[].words[]` with `start_time`/`end_time`, `speaker{id,name}` | Spacing tokens (`" "`) are skipped; bracketed tokens like `[music]`, `[laughs]`, `[door slams]` are treated as SDH sound descriptions. |
| **ElevenLabs Scribe - raw API response** | top-level `words[]` with `type: word\|spacing\|audio_event`, `speaker_id` | `audio_event` entries are SDH. |

## Quick start

```bat
:: GUI (Windows) - double-click or:
python json_to_srt_gui.py

:: CLI - defaults = your Subtitle Edit profile, output next to the input as .srt
python json_to_srt_core.py "C:\caps\ElevenLabs.json"
python json_to_srt_core.py AirCaps.json -o out.srt --include-speaker-names 1 --speaker-name-map "speaker_1=George"
python json_to_srt_core.py ElevenLabs.json --include-sdh 0 --max-cps 20 --snap-to-frames 1
python json_to_srt_core.py in.json --settings "%LOCALAPPDATA%\JSONtoSRT\presets\Netflix.json"
python json_to_srt_core.py --help
```

Booleans on the CLI take `0|1` (also `true/false/yes/no`).

## Settings (all user-controllable in core, CLI and GUI)

Defaults mirror the following Subtitle Edit 3 *General* settings:

<img width="871" height="591" alt="Subtitle_Edit_Settings" src="https://github.com/user-attachments/assets/665c5102-73f9-4637-be68-aeed7e2c5a01" />
<br><br>

| Tab | Setting (CLI flag) | Default | Meaning |
|---|---|---|---|
| Rules | `single_line_max_length` | 43 | max characters per line (prefixes count, see below) |
| Rules | `optimal_cps` | 15.0 | cues are lengthened into free gaps toward this reading speed |
| Rules | `max_cps` | 25.0 | hard reading-speed ceiling (extension first, warning if impossible) |
| Rules | `max_wpm` | 300 | words per minute ceiling, handled like max CPS |
| Rules | `min_duration_ms` / `max_duration_ms` | 1000 / 8000 | enforced (extend end → pull start earlier / split + cap) |
| Rules | `min_gap_ms` | 24 | minimum gap between cues; overlaps are repaired |
| Rules | `max_lines` | 2 | lines per cue |
| Rules | `unbreak_shorter_than` | 43 | text at or below this length stays on one line |
| Rules | `dialog_style` | `dash_both_space` | `dash_both_space`, `dash_both_nospace`, `dash_second_space`, `dash_second_nospace`, `none` (Subtitle Edit names) |
| Rules | `cps_includes_spaces` | on | CPS counting mode |
| Rules | `frame_rate` / `snap_to_frames` | 23.976 / off | frame rate is stored; snapping rounds times to frame boundaries when enabled |
| Rules | `encoding` | `utf-8-sig` | UTF-8 **with BOM** (also utf-8, utf-16, cp1252, latin-1). Line endings are CRLF. |
| Speakers | `include_speaker_names` | off | prefix lines with speaker labels |
| Speakers | `speaker_label_mode` | `speaker_change` | label only when the speaker changes (notebook behaviour) or on every cue |
| Speakers | `speaker_label_format` | `{name}: ` | label template |
| Speakers | `speaker_name_map` | - | `speaker_0=George, speaker_1=Selena` (ids or source names) |
| Speakers | `dashes_with_labels` | off | labels replace dashes unless this is on (`- George: …`) |
| Speakers | `include_speaker_in_length` | on | dash/label prefixes count toward the 43-char limit |
| SDH | `include_sdh` | on | keep `[sound descriptions]` |
| SDH | `sdh_mode` | `inline` | `inline`: events adjacent to speech stay in the speaker's text (`Sorry. [laughs] I didn't…`); `separate`: every event is its own cue |
| SDH | `inline_event_max_ms` | 2000 | clamp long events (4 s of `[laughs]`) when shown with speech |
| SDH | `sdh_uppercase` | off | `[MUSIC]` |
| SDH | `sdh_group_gap_ms` | 1000 | consecutive stand-alone events closer than this share a cue |
| Segmentation | `extra_abbreviations` | - | comma-separated additions (`Lt., Cmdr.`); a file `abbreviations.txt` (one per line) in the settings folder is merged too |
| Segmentation | `merge_short_sentences` / `sentence_merge_max_gap_ms` | on / 700 | a following sentence of the same speaker joins the previous cue if *the whole sentence* fits |
| Segmentation | `pause_break_ms` | 1500 | silence inside a sentence longer than this starts a new cue (0 = off) |
| Segmentation | `dialog_merge_max_gap_ms` | 1000 | a new speaker may share the previous cue only if they start within this gap |
| Segmentation | `min_chunk_chars` | 20 | pieces shorter than this are penalised when a sentence must be split (orphan avoidance) |
| Segmentation | `max_speakers_per_cue` | 2 | never more speakers than this per cue, each on their own line |
| Timing | `extend_to_optimal_cps` / `max_end_extension_ms` | on / 1500 | linger limit after the last word (min duration always wins) |

### Where settings live
* Windows: `%LOCALAPPDATA%\JSONtoSRT\settings.json`, presets in `%LOCALAPPDATA%\JSONtoSRT\presets\<name>.json`
* Linux: `~/.config/JSONtoSRT/…`, macOS: `~/Library/Application Support/JSONtoSRT/…`
* Preset files are plain settings JSON and can be passed to the CLI with `--settings`.
* The GUI saves settings on Convert and on close. **Reset to Defaults** asks for confirmation and
  does not touch presets. "Open settings folder" opens the directory in Explorer.

## How it works (ported notebook logic and what changed)

Pipeline: **parse → sentence units → cue chunks → dialog assembly → timing → render**.

1. **Parsing** - words are grouped into speaker *turns* (AirCaps `utterances`, ElevenLabs `segments`), turns are sorted by start time and same-speaker neighbours are merged. Missing/non-monotonic word times are repaired. SDH tokens are flagged (`type == audio_event` or `^[...]$` / `^(...)$`).
2. **Sentence ends** - port of the notebook's `is_valid_sentence_end` (terminal `.?!` + optional closing quotes/brackets, title abbreviation list, `(?:[a-z]{1,3}\.){2,}` acronym rule such as `U.S.`, `A.N.`, `Ph.D.`). Extended with: single initials (`J.`), ellipsis handling (`well...` breaks only before a capital), numeric abbreviations that only count before a digit (`No. 5` vs. `No. Stop it.`), decimals, a much longer built-in list, and user extensions (setting + `abbreviations.txt`). The notebook additionally called NLTK Punkt; that dependency was dropped in favour of these rules so the tool has no third-party requirements.
3. **Units** - like the notebook's `get_srt_segments` step 1: break on sentence end or speaker change. Added: break on long pauses (`pause_break_ms`, the piece is flagged as a *continuation*), and event grouping. Excluding SDH filters the tokens *before* segmentation, so sentences re-join cleanly (no empty cues, double spaces or orphan dashes - verified on the ElevenLabs sample).
4. **Chunking** - the notebook used greedy fill (`can_fit`: max duration, line width, line count) and an orphan fix-up that moved words backwards (`MIN_SPLIT_SENTENCE_LENGTH`). This is replaced by a small dynamic programme: first the minimum number of cues that satisfy the same `can_fit` rules, then - among those - the split with the best balance *and* break points (sentence end ≫ comma/semicolon/dash > before a conjunction/preposition > pause in speech; breaking after articles/prepositions/auxiliaries is penalised; pieces under `min_chunk_chars` are penalised).
5. **Dialog cues** - new. A different speaker who starts within `dialog_merge_max_gap_ms` of the previous cue joins it as a second line when both fit (each speaker always on their own line, max 2 speakers). If that requires splitting the newcomer's sentence, the split is only accepted when the sentence needed several cues anyway or the remainder is substantial. A look-ahead prefers ending a sentence on a single line when the next speaker could share that cue. Same-speaker short sentences are merged when the whole sentence fits (`Yes. Yes, that's a great idea.`).
6. **Dashes** - per `dialog_style`. A line gets its dash only if the cue has two speakers **and** that line starts a sentence; a line continuing a sentence from the previous cue gets none:
   ```
   - It's got to come from somewhere else.
   - Yeah, that one's also fun

   because the lows are going to suck, right?
   - So it hits you on both sides.
   ```
7. **Line balancing** - the notebook binary-searched the narrowest width keeping the same line count. Here the minimum line count is kept and the break is chosen by cost = |len − target| summed over lines plus penalties/bonuses (sentence end −20, clause punctuation −2, before conjunction/preposition −1.5, after a no-break word +3). Balance dominates, as requested: `played on Broadway, on / television, and in films.` Text ≤ `unbreak_shorter_than` stays on one line.
8. **Timing** - new (the notebook wrote raw word times): max duration cap; speech-first repair of stand-alone SDH cues (clipped around speech, attached as an extra line, or dropped with a warning); overlap repair between consecutive cues (push later / trim / split the overlap); extension to min duration and to max CPS/WPM (into the gap before the next cue), then toward optimal CPS limited by `max_end_extension_ms`; if still short, the start is pulled earlier; optional frame snapping; statistics. Splitting cannot lower CPS when speech is genuinely fast, so CPS/WPM violations that cannot be fixed by extension are *reported* (counts in the log) rather than fragmenting the cue.
9. **Rendering** - SRT with `-->` sanitising (from the notebook's `write_srt`), chosen encoding, CRLF.

## Tests & headless GUI check

```sh
python -m unittest discover -s tests          # 19 tests
```
The GUI was exercised under Xvfb (widgets for every setting, preset save/load, settings persistence, a full conversion through the worker thread).

## Building a Windows .exe (included in repository)

```bat
pip install pyinstaller
pyinstaller --noconfirm --onefile --windowed --name JSONtoSRT json_to_srt_gui.py
:: CLI build:
pyinstaller --noconfirm --onefile --console --name json_to_srt json_to_srt_core.py
```
- `JSONtoSRT.exe` is the self-contained GUI (tkinter is bundled automatically). You can drop a `.json` file onto the exe - it is pre-filled as the input.
- `json_to_srt.exe` is the command line interface.

## Known limitations
* Diarization errors in the source (speaker flips mid-sentence) are reproduced; the notebook's punctuation-based speaker re-alignment needed its own punctuation model and was not ported.
* English-centric heuristics for conjunction/preposition break points and abbreviations (extensible).
* Zero-length or duplicated words from ASR glitches can still yield very short cues when there is no room to extend them; they are counted in the log.
