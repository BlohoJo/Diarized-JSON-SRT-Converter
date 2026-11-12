# JSON → SRT Converter (AirCaps A5S / ElevenLabs Scribe)

Converts transcription JSON - with per-word timestamps and speaker identification - into a clean, industry-standard `.srt` subtitle file. Fully compatible with [Subtitle Edit](https://www.nikse.dk/subtitleedit) and other subtitle editors.

Built with pure Python 3.8+ standard library - no third-party packages are needed at runtime. The `tkinter` GUI toolkit ships with the standard [python.org](https://www.python.org/downloads/) Windows installer.

### Features at a Glance

- **Auto-detects** AirCaps A5S Async v1 and ElevenLabs Scribe v3 JSON formats
- **Dialog dashes** handled correctly - continuation lines don't get dashes, new speakers do
- **Abbreviation-safe** sentence detection - "Dr.", "U.S.", "Ph.D.", decimals, and initials never cause false sentence breaks
- **Balanced line splits** - avoids short "orphan" lines dangling below a long line
- **SDH support** - include or exclude sound descriptions (`[music]`, `[laughs]`) with one toggle
- **Reading speed enforcement** - adjusts cue durations to stay within comfortable CPS and WPM limits
- **Speaker labels** - optionally show who's speaking, with customizable names
- **37 configurable settings** with defaults matching Subtitle Edit 3, plus saveable presets
- **Windows GUI** and command-line interface, both included

```
json_to_srt/
├── json_to_srt_core.py    # converter engine + command-line interface
├── json_to_srt_gui.py     # Windows GUI front-end
├── tests/test_core.py     # unit tests
├── README.md
│
│   After building with PyInstaller (see below):
├── json_to_srt.exe        # standalone CLI - no Python install needed
└── JSONtoSRT.exe          # standalone GUI - no Python install needed
```

---

## Supported Inputs (Auto-Detected)

The converter automatically identifies which format you're using - no manual selection needed.

| Format | How It's Detected | Notes |
|---|---|---|
| **AirCaps A5S Async v1** | `model: a5s-…` / `utterances[].words[]` with `start`/`end`/`speaker` | Uses `utterances` (one speaker each) so overlapping speech is never interleaved word-by-word. Does not contain SDH events. |
| **ElevenLabs Scribe v3 - segments export** | `segments[].words[]` with `start_time`/`end_time`, `speaker{id,name}` | Spacing tokens (`" "`) are skipped; bracketed tokens like `[music]`, `[laughs]`, `[door slams]` are treated as SDH sound descriptions. |
| **ElevenLabs Scribe - raw API response** | Top-level `words[]` with `type: word\|spacing\|audio_event`, `speaker_id` | `audio_event` entries are treated as SDH. |

---

## Quick Start

### GUI (Windows)

Run `python json_to_srt_gui.py` (or double-click `JSONtoSRT.exe` if you've [built it](#building-standalone-executables-optional)). Select your input `.json` file, choose an output location, adjust any settings you like, and click **Convert**.

You can also drag-and-drop a `.json` file onto the `.exe` - it will be pre-filled as the input.

### Command Line

```bat
:: Basic conversion - output is saved next to the input as .srt
python json_to_srt_core.py "C:\caps\ElevenLabs.json"

:: Specify output file and enable speaker names with a custom name map
python json_to_srt_core.py AirCaps.json -o out.srt --include-speaker-names 1 --speaker-name-map "speaker_1=George"

:: Exclude SDH sound descriptions and lower the max reading speed
python json_to_srt_core.py ElevenLabs.json --include-sdh 0 --max-cps 20 --snap-to-frames 1

:: Use a saved preset file for all settings
python json_to_srt_core.py in.json --settings "%LOCALAPPDATA%\JSONtoSRT\presets\Netflix.json"

:: Show all available options
python json_to_srt_core.py --help
```

Booleans on the CLI accept `0|1` (also `true/false/yes/no`).

---

## Settings

Every setting is user-controllable via the GUI, the command line, or a settings/preset file. Defaults match the **Subtitle Edit 3** *General* profile shown below:

<p align="center">
  <img width="871" height="591" alt="Subtitle Edit 3 - General Settings" src="Subtitle_Edit_Settings.png" />
</p>

### Rules

| Setting (CLI flag) | Default | Description |
|---|---|---|
| `single_line_max_length` | 43 | Maximum characters per subtitle line. Dashes and speaker labels count toward this limit. |
| `optimal_cps` | 15.0 | Target reading speed (characters per second). Cue durations are extended into available gaps to approach this comfortable pace. |
| `max_cps` | 25.0 | Hard reading-speed ceiling. Cues above this are extended where possible; remaining violations are reported in the conversion log. |
| `max_wpm` | 300 | Maximum words-per-minute ceiling. Handled the same way as max CPS. |
| `min_duration_ms` | 1000 | Minimum cue duration in milliseconds. Short cues are extended (end pushed later, then start pulled earlier) when room allows. |
| `max_duration_ms` | 8000 | Maximum cue duration in milliseconds. No subtitle stays on screen longer than this. |
| `min_gap_ms` | 24 | Minimum gap between consecutive cues in milliseconds. Overlaps are automatically repaired. |
| `max_lines` | 2 | Maximum number of text lines per cue. |
| `unbreak_shorter_than` | 43 | Text at or below this character count (including dashes/labels) is kept on a single line rather than split. |
| `dialog_style` | `dash_both_space` | How two-speaker cues are formatted with dashes. Options: `dash_both_space`, `dash_both_nospace`, `dash_second_space`, `dash_second_nospace`, `none`. See [Dialog Assembly](#4-dialog-assembly) for details. |
| `cps_includes_spaces` | on | Whether spaces are counted when measuring characters per second. |
| `frame_rate` | 23.976 | Video frame rate. Used only when *Snap to frames* is enabled. |
| `snap_to_frames` | off | Round cue start/end times to the nearest frame boundary at the specified frame rate. |
| `encoding` | `utf-8-sig` | Output file encoding. `utf-8-sig` = UTF-8 with BOM. Also supports `utf-8`, `utf-16`, `cp1252`, `latin-1`. Line endings are always CRLF. |

### Speakers

| Setting (CLI flag) | Default | Description |
|---|---|---|
| `include_speaker_names` | off | Prefix subtitle lines with speaker labels (e.g., `George: `). Off by default. |
| `speaker_label_mode` | `speaker_change` | `speaker_change`: show the label only when the speaker changes. `every_cue`: label every cue. |
| `speaker_label_format` | `{name}: ` | Template for speaker labels. `{name}` is replaced with the speaker's name or ID. |
| `speaker_name_map` | *(empty)* | Rename speakers from their default IDs. Example: `speaker_0=George, speaker_1=Selena`. Accepts speaker IDs or the original names from the transcription. |
| `dashes_with_labels` | off | When off, speaker labels replace dialog dashes. When on, both are shown together (`- George: …`). |
| `include_speaker_in_length` | on | Dashes and speaker label text count toward the maximum line length. |

### SDH (Subtitles for the Deaf and Hard of Hearing)

| Setting (CLI flag) | Default | Description |
|---|---|---|
| `include_sdh` | on | Include sound descriptions like `[music]`, `[laughs]`, `[door slams]` in the output. Turn this off to produce speech-only subtitles. |
| `sdh_mode` | `inline` | Controls where sound events appear. `inline`: events adjacent to speech stay in the speaker's subtitle line (e.g., `Sorry. [laughs] I didn't…`). `separate`: every sound event gets its own standalone cue. |
| `inline_event_max_ms` | 2000 | Maximum duration (ms) for an inline sound event. Events longer than this are clamped. |
| `sdh_uppercase` | off | Display sound descriptions in uppercase (e.g., `[MUSIC]` instead of `[music]`). |
| `sdh_group_gap_ms` | 1000 | Consecutive standalone sound events closer together than this (ms) are combined into a single cue. |

### Segmentation

| Setting (CLI flag) | Default | Description |
|---|---|---|
| `extra_abbreviations` | *(empty)* | Comma-separated list of additional abbreviations to recognize (e.g., `Lt., Cmdr., Sgt.`). A file named `abbreviations.txt` (one abbreviation per line) in the settings folder is also loaded automatically if present. |
| `merge_short_sentences` | on | A short sentence from the same speaker is combined with the previous cue if the entire sentence fits. Keeps quick exchanges like `Yes. Absolutely.` together. |
| `sentence_merge_max_gap_ms` | 700 | Maximum silence gap (ms) between sentences for merging to occur. |
| `pause_break_ms` | 1500 | A silence gap within a sentence longer than this (ms) forces a new cue, even mid-sentence. Set to `0` to disable. |
| `dialog_merge_max_gap_ms` | 1000 | A new speaker can share a cue with the previous speaker only if they start speaking within this many milliseconds. |
| `min_chunk_chars` | 20 | Minimum character count for a split piece. Shorter fragments are penalized to prevent orphan lines. |
| `max_speakers_per_cue` | 2 | Maximum number of speakers allowed in a single cue. Each speaker always gets their own line. |

### Timing

| Setting (CLI flag) | Default | Description |
|---|---|---|
| `extend_to_optimal_cps` | on | Extend cue end times toward the optimal reading speed when there is room before the next cue begins. |
| `max_end_extension_ms` | 1500 | Maximum amount (ms) that a cue's end time can be extended past the last spoken word. Minimum duration requirements always take priority over this limit. |

### Where Settings Are Stored

| Platform | Location |
|---|---|
| Windows | `%LOCALAPPDATA%\JSONtoSRT\settings.json` |
| Linux | `~/.config/JSONtoSRT/settings.json` |
| macOS | `~/Library/Application Support/JSONtoSRT/settings.json` |

- **Presets** are saved in a `presets` subfolder (e.g., `%LOCALAPPDATA%\JSONtoSRT\presets\Netflix.json`). Preset files are plain JSON and can be passed to the CLI with `--settings`.
- The GUI **saves your settings automatically** when you click **Convert** and when you close the window.
- **Reset to Defaults** restores all settings to their original values after a confirmation prompt. Your saved presets are not affected.
- The GUI includes an **Open settings folder** button for quick access to the settings directory.

---

## How It Works

The converter transforms raw per-word transcription data into polished subtitle cues through a multi-stage pipeline:

**Parse → Detect Sentences → Build Cues → Assemble Dialog → Apply Timing → Render SRT**

### 1. Parsing

The input JSON is read and words are grouped into speaker *turns* - stretches of speech from a single speaker. AirCaps provides these as `utterances` (already grouped by speaker), while ElevenLabs provides `segments` that are processed into the same structure.

- Turns are sorted by start time; consecutive turns from the same speaker are merged.
- Missing or out-of-order word timestamps are automatically repaired.
- SDH tokens (sound descriptions like `[music]`) are identified and flagged for separate handling.
- If SDH exclusion is enabled, sound description tokens are filtered out *before* any further processing, so sentences rejoin cleanly - no empty cues, double spaces, or orphaned dashes.

### 2. Sentence Detection

Accurate sentence boundary detection is critical for natural-feeling subtitles. The converter uses a rule-based system to identify where sentences actually end, while avoiding common false positives:

- **Standard punctuation** - periods (`.`), question marks (`?`), and exclamation marks (`!`), including when followed by closing quotes or brackets.
- **Abbreviation awareness** - a large built-in list of common abbreviations (`Dr.`, `Mr.`, `Mrs.`, `vs.`, `etc.`) prevents them from being mistaken for sentence endings. You can extend this list with the `extra_abbreviations` setting or by placing an `abbreviations.txt` file in the settings folder.
- **Acronyms and initials** - patterns like `U.S.`, `A.N.`, `Ph.D.`, and single initials (`J.`) are recognized and not treated as sentence breaks.
- **Numbers and decimals** - `3.14`, `No. 5`, and similar patterns are handled correctly.
- **Ellipses** - `well...` only breaks before a following capital letter (indicating a new sentence), not mid-thought.

### 3. Building Cues

Once sentence boundaries are identified, the text is divided into subtitle cues that respect all configured constraints (maximum line length, duration, line count, and reading speed).

Instead of a simple greedy approach that fills each cue to the brim, the converter uses a **cost-based optimization** that evaluates multiple possible breakpoints and selects the split that produces the most readable result:

- **Sentence boundaries** are strongly preferred as break points.
- **Clause punctuation** (commas, semicolons, em-dashes) is the next best option.
- **Natural phrasing** is respected - the converter prefers breaking *before* conjunctions and prepositions (`and`, `but`, `in`, `on`) rather than *after* them, so the next line starts with a connecting word rather than leaving it stranded at the end of the previous line.
- **Short fragments are penalized** - if a break would leave a piece shorter than `min_chunk_chars` (default 20 characters), the converter looks for a better alternative. This prevents the "orphan line" problem where just a word or two ends up isolated on its own line.

### 4. Dialog Assembly

When two speakers are talking close together in time, the converter combines them into a single two-line cue using **dialog dashes**:

```
- It's got to come from somewhere else.
- Yeah, that one's also fun because you know the lows are
```

The rules for dialog dashes follow standard subtitling conventions:

- A dash is added only when the cue contains **two speakers**, with each speaker on their own line.
- If a line **continues a sentence** that began in a previous cue, it receives **no dash** - only the new speaker's line gets one:

  ```
  also fun because you know the lows are going to suck, right?
  - So it hits you on both sides.
  ```

- Two different speakers **never share the same line**.
- Short consecutive sentences from the same speaker (e.g., `Yes. Yes, that's a great idea.`) are merged into a single cue when they fit.
- The dash formatting style is configurable via the `dialog_style` setting.

### 5. Line Balancing

When a subtitle line is too long for a single line, it is split across two lines in a **visually balanced** way. The converter avoids lopsided splits where one line is very long and the other is just a word or two:

**Before balancing:**
```
played on Broadway, on television, and in
films.
```

**After balancing:**
```
played on Broadway, on
television, and in films.
```

The balancing algorithm weighs line-length symmetry, punctuation placement, and natural phrasing to choose the most readable break point. Text shorter than `unbreak_shorter_than` (default 43 characters) is kept on a single line.

### 6. Timing Adjustments

Raw word timestamps from transcription services often need refinement for a comfortable viewing experience:

- **Minimum / maximum duration** - cues are extended or split to stay within the configured bounds (default: 1-8 seconds).
- **Reading speed enforcement** - cue durations are extended into available gaps to bring the reading speed down to `max_cps` first, then toward `optimal_cps` (limited by `max_end_extension_ms`). If a cue is still too fast after all possible extension, it is flagged in the conversion report rather than being split into unreadable fragments.
- **Overlap repair** - if two consecutive cues overlap in time, they are adjusted (pushed apart, trimmed, or split) while preserving the minimum gap.
- **SDH timing** - standalone sound-description events are timed so they don't steal display time from speech cues.
- **Frame snapping** (optional) - cue start and end times are rounded to the nearest frame boundary at the configured frame rate.

### 7. Rendering

The final cues are written as a standard `.srt` file:

- Output uses the configured encoding (default: UTF-8 with BOM) and CRLF line endings for maximum compatibility with Windows subtitle software.
- The `-->` timestamp separator is validated per the SRT specification.
- A **conversion report** (`.report.txt`) is generated alongside the `.srt` file, summarizing cue counts, any constraint violations that could not be automatically resolved, and other statistics.

---

## Sample Results

The `samples/` folder contains outputs generated from two sample inputs - both transcribed from the same source audio:

| File | Cues | Notes |
|---|---|---|
| `AirCaps_default.srt` | 471 | Standard conversion (AirCaps has no SDH events) |
| `AirCaps_speaker-names.srt` | 485 | Speaker labels shown on speaker changes |
| `ElevenLabs_sdh-on.srt` | 832 | 373 sound description events kept (inline + standalone) |
| `ElevenLabs_sdh-off.srt` | 651 | Sound descriptions removed - no empty cues or leftover artifacts |
| `ElevenLabs_sdh-on_speaker-names.srt` | 856 | SDH on, with speaker labels |
| `ElevenLabs_sdh-off_speaker-names.srt` | 676 | SDH off, with speaker labels |

Each `.report.txt` file contains the full conversion log. All outputs pass validation: no line exceeds 43 characters, no cue exceeds 2 lines or 8000 ms, the minimum gap is 24 ms everywhere, and no two speakers share a single line. Any remaining CPS or minimum-duration violations are from genuinely fast or overlapping speech that cannot be resolved by extending cue durations - these are itemized in each report.

---

## Running the Tests

```sh
python -m unittest discover -s tests    # 19 unit tests
```

Tests cover default settings validation, abbreviation handling, dialog dash rules, line balancing (including the "played on Broadway" case), SDH removal, and timing rule enforcement.

---

## Building Standalone Executables (Optional)

If you'd like standalone `.exe` files that run without a Python installation:

```bat
pip install pyinstaller

:: Build the GUI executable
pyinstaller --noconfirm --onefile --windowed --name JSONtoSRT json_to_srt_gui.py

:: Build the CLI executable
pyinstaller --noconfirm --onefile --console --name json_to_srt json_to_srt_core.py
```

The resulting executables appear in the `dist/` folder. `JSONtoSRT.exe` is fully self-contained - `tkinter` is bundled automatically. You can drag-and-drop a `.json` file onto the GUI `.exe` to pre-fill the input path.

---

## Known Limitations

- **Diarization errors in the source are preserved.** If the transcription service incorrectly assigns a speaker mid-sentence (a "speaker flip"), the converter reproduces that error as-is. Correcting speaker assignment would require a separate punctuation model, which is outside the scope of this tool.
- **English-centric heuristics.** Abbreviation lists and line-break preferences (e.g., break before conjunctions) are tuned for English. The abbreviation list is extensible via settings, but phrase-level break rules are not currently language-aware.
- **ASR glitches.** Zero-length or duplicated words from transcription errors can produce very short cues when there is no room to extend them. These are counted and reported in the conversion log.
