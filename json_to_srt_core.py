#!/usr/bin/env python3
"""
json_to_srt_core.py
===================

Convert speaker-diarized, per-word timed transcription JSON (AirCaps A5S Async v1
or ElevenLabs Scribe v3) into an industry-standard .srt subtitle file.

The module is the single source of truth for:
  * the settings specification (SETTINGS_SPEC)  -> used by the CLI *and* the GUI
  * input format auto-detection and parsing
  * sentence segmentation with abbreviation awareness (ported from the notebook)
  * cue chunking with balanced splits, dialog (two-speaker) cue assembly
  * timing rules (min/max duration, min gap, CPS/WPM extension, overlap repair)
  * line balancing (widow/orphan avoidance) and dialog dash rules
  * SRT rendering

Runtime dependencies: Python 3.8+ standard library only.

Command line usage:
    python json_to_srt_core.py input.json [-o output.srt] [--include-sdh 0|1] ...
    python json_to_srt_core.py --help
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

__version__ = "1.0.0"

APP_NAME = "JSONtoSRT"

# --------------------------------------------------------------------------------------
# Settings specification
# --------------------------------------------------------------------------------------
# Each entry: (key, type, default, label, group, help)
#   type: "int" | "float" | "bool" | "str" | ("choice", [options...])
# Defaults mirror the user's Subtitle Edit 3 "General" settings.

DIALOG_STYLES = [
    "dash_both_space",       # "- Line 1\n- Line 2"      (Subtitle Edit: Dash both lines with space)
    "dash_both_nospace",     # "-Line 1\n-Line 2"        (Dash both lines without space)
    "dash_second_space",     # "Line 1\n- Line 2"        (Dash second line with space)
    "dash_second_nospace",   # "Line 1\n-Line 2"         (Dash second line without space)
    "none",                  # no dashes at all
]

ENCODINGS = ["utf-8-sig", "utf-8", "utf-16", "utf-16-le", "cp1252", "latin-1"]

SETTINGS_SPEC: List[Tuple[str, Any, Any, str, str, str]] = [
    # --- Subtitle Edit "Rules" --------------------------------------------------------
    ("single_line_max_length", "int", 43, "Single line max. length",
     "Rules", "Maximum characters per subtitle line (dashes / speaker labels count)."),
    ("optimal_cps", "float", 15.0, "Optimal chars/sec",
     "Rules", "Cue end times are extended (into available gaps) to approach this reading speed."),
    ("max_cps", "float", 25.0, "Max. chars/sec",
     "Rules", "Hard reading-speed ceiling; cues above it are extended where possible and otherwise reported."),
    ("max_wpm", "int", 300, "Max. words/min",
     "Rules", "Maximum words per minute; treated like Max CPS when extending cue durations."),
    ("min_duration_ms", "int", 1000, "Min. duration (ms)",
     "Rules", "Cues shorter than this are extended (end later, then start earlier) when room allows."),
    ("max_duration_ms", "int", 8000, "Max. duration (ms)",
     "Rules", "No cue is allowed to be displayed longer than this."),
    ("min_gap_ms", "int", 24, "Min. gap between subtitles (ms)",
     "Rules", "Minimum gap between consecutive cues."),
    ("max_lines", "int", 2, "Max. number of lines",
     "Rules", "Maximum lines per cue."),
    ("unbreak_shorter_than", "int", 43, "Unbreak subtitles shorter than",
     "Rules", "Text at or below this length (incl. prefixes) is kept on a single line."),
    ("dialog_style", ("choice", DIALOG_STYLES), "dash_both_space", "Dialog style",
     "Rules", "How two-speaker cues are dashed (Subtitle Edit naming)."),
    ("cps_includes_spaces", "bool", True, "Chars/sec includes spaces",
     "Rules", "Count spaces when measuring characters per second."),
    ("frame_rate", "float", 23.976, "Default frame rate",
     "Rules", "Stored frame rate; used only when 'Snap to frames' is enabled."),
    ("snap_to_frames", "bool", False, "Snap times to frames",
     "Rules", "Round cue start/end times to frame boundaries of the frame rate."),
    ("encoding", ("choice", ENCODINGS), "utf-8-sig", "Output file encoding",
     "Rules", "utf-8-sig = UTF-8 with BOM (Subtitle Edit default)."),

    # --- Speakers -----------------------------------------------------------------------
    ("include_speaker_names", "bool", False, "Include speaker names/labels",
     "Speakers", "Prefix lines with the diarization speaker label (e.g. 'Speaker 1: ')."),
    ("speaker_label_mode", ("choice", ["speaker_change", "every_cue"]), "speaker_change",
     "Speaker label mode", "Speakers",
     "speaker_change: label only when the speaker changes. every_cue: label the first line of every cue."),
    ("speaker_label_format", "str", "{name}: ", "Speaker label format",
     "Speakers", "Python format string; {name} is replaced by the speaker name."),
    ("speaker_name_map", "str", "", "Speaker name map",
     "Speakers", "Rename speakers: 'speaker_0=George, speaker_1=Selena' (source ids or names)."),
    ("dashes_with_labels", "bool", False, "Dialog dashes when labels are on",
     "Speakers", "If labels are on, also add dialog dashes ('- George: ...')."),
    ("include_speaker_in_length", "bool", True, "Prefix counts toward line length",
     "Speakers", "Count dash / label prefixes toward the single-line max length (as the notebook did)."),

    # --- SDH ------------------------------------------------------------------------------
    ("include_sdh", "bool", True, "Include sound descriptions (SDH)",
     "SDH", "Keep bracketed audio events such as [music] or [laughs] as their own cues."),
    ("sdh_mode", ("choice", ["inline", "separate"]), "inline", "Sound description placement",
     "SDH", "inline: events next to speech stay in the speaker's text ('Sorry. [laughs] I...'); "
            "separate: every event gets its own cue."),
    ("inline_event_max_ms", "int", 2000, "Max duration of an inline event (ms)",
     "SDH", "Clamp long events (e.g. 4 s of laughter) when they are shown inside a speech cue."),
    ("sdh_uppercase", "bool", False, "Upper-case sound descriptions",
     "SDH", "Render [music] as [MUSIC]."),
    ("sdh_group_gap_ms", "int", 1000, "Group sound descriptions closer than (ms)",
     "SDH", "Consecutive audio events this close together share one cue."),

    # --- Segmentation / splitting -----------------------------------------------------
    ("extra_abbreviations", "str", "", "Extra abbreviations",
     "Segmentation", "Comma-separated, e.g. 'Lt., Cmdr., approx.' (merged with the built-in list)."),
    ("merge_short_sentences", "bool", True, "Merge short sentences into one cue",
     "Segmentation", "Join a following sentence of the same speaker to the previous cue when both fit."),
    ("sentence_merge_max_gap_ms", "int", 700, "Max gap for merging sentences (ms)",
     "Segmentation", "Sentences of the same speaker separated by more than this never share a cue."),
    ("pause_break_ms", "int", 1500, "Force cue break on pauses longer than (ms)",
     "Segmentation", "Silence inside a sentence longer than this starts a new cue (0 = off)."),
    ("dialog_merge_max_gap_ms", "int", 1000, "Max gap for two-speaker cues (ms)",
     "Segmentation", "A new speaker joins the previous speaker's cue only if they start within this gap."),
    ("min_chunk_chars", "int", 20, "Discourage cues shorter than (chars)",
     "Segmentation", "When a sentence must be split, pieces shorter than this are penalised (orphan avoidance)."),
    ("max_speakers_per_cue", "int", 2, "Max speakers per cue",
     "Segmentation", "Never more than this many speakers share one cue (each on their own line)."),

    # --- Timing -----------------------------------------------------------------------------
    ("extend_to_optimal_cps", "bool", True, "Extend cues toward optimal CPS",
     "Timing", "Lengthen cues (into gaps) until the optimal reading speed is reached."),
    ("max_end_extension_ms", "int", 1500, "Max end extension beyond speech (ms)",
     "Timing", "Limit how far a cue may linger after the last word (min duration always wins)."),
]

SETTINGS_GROUPS = ["Rules", "Speakers", "SDH", "Segmentation", "Timing"]


def default_settings() -> Dict[str, Any]:
    return {key: default for key, _t, default, *_rest in SETTINGS_SPEC}


def coerce_setting(key: str, value: Any) -> Any:
    """Coerce a raw (string/json) value to the declared type of a setting."""
    spec = {k: t for k, t, *_ in SETTINGS_SPEC}
    if key not in spec:
        raise KeyError(f"Unknown setting: {key}")
    typ = spec[key]
    if typ == "int":
        return int(float(value))
    if typ == "float":
        return float(value)
    if typ == "bool":
        if isinstance(value, bool):
            return value
        return str(value).strip().lower() in ("1", "true", "yes", "on", "y")
    if typ == "str":
        return "" if value is None else str(value)
    if isinstance(typ, tuple) and typ[0] == "choice":
        value = str(value)
        if value not in typ[1]:
            raise ValueError(f"{key}: '{value}' is not one of {typ[1]}")
        return value
    return value


def normalize_settings(raw: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Merge user values onto defaults, coercing types and ignoring unknown keys."""
    merged = default_settings()
    for k, v in (raw or {}).items():
        if k in merged:
            try:
                merged[k] = coerce_setting(k, v)
            except (ValueError, TypeError):
                pass  # keep default for invalid values
    return merged


# --------------------------------------------------------------------------------------
# Settings storage locations (shared by CLI and GUI)
# --------------------------------------------------------------------------------------

def config_dir() -> str:
    """%LOCALAPPDATA%\\JSONtoSRT on Windows, XDG config dir elsewhere."""
    if sys.platform.startswith("win"):
        base = os.environ.get("LOCALAPPDATA") or os.path.join(os.path.expanduser("~"), "AppData", "Local")
    elif sys.platform == "darwin":
        base = os.path.join(os.path.expanduser("~"), "Library", "Application Support")
    else:
        base = os.environ.get("XDG_CONFIG_HOME") or os.path.join(os.path.expanduser("~"), ".config")
    return os.path.join(base, APP_NAME)


def settings_path() -> str:
    return os.path.join(config_dir(), "settings.json")


def presets_dir() -> str:
    return os.path.join(config_dir(), "presets")


def abbreviations_file() -> str:
    """Optional user file (one abbreviation per line) merged into the built-in list."""
    return os.path.join(config_dir(), "abbreviations.txt")


# --------------------------------------------------------------------------------------
# Data model
# --------------------------------------------------------------------------------------

@dataclass
class Word:
    text: str
    start: int            # ms
    end: int              # ms
    speaker: str          # speaker id (e.g. "speaker_0")
    speaker_name: str     # display name (e.g. "Speaker 0")
    is_event: bool = False  # SDH audio event such as [music]


@dataclass
class Unit:
    """A run of words from one speaker that may become one or more cues.

    Units are bounded by sentence ends, speaker changes, long pauses and audio events.
    """
    words: List[Word]
    speaker: str
    speaker_name: str
    starts_sentence: bool   # True if the unit begins a new sentence (affects dialog dashes)
    starts_turn: bool       # True if the speaker just changed (affects speaker labels)
    is_event: bool = False
    ends_sentence: bool = True  # False when the unit was cut by a long pause mid-sentence

    @property
    def start(self) -> int:
        return self.words[0].start

    @property
    def end(self) -> int:
        return self.words[-1].end


@dataclass
class Part:
    """One speaker's contribution to a cue (a chunk of a Unit)."""
    words: List[Word]
    speaker: str
    speaker_name: str
    starts_sentence: bool
    starts_turn: bool
    is_event: bool = False
    ends_sentence: bool = True   # True if the last word closes a sentence
    lines: List[str] = field(default_factory=list)   # rendered lines (filled at render time)

    @property
    def start(self) -> int:
        return self.words[0].start

    @property
    def end(self) -> int:
        return self.words[-1].end

    @property
    def text(self) -> str:
        return " ".join(w.text for w in self.words)


@dataclass
class Cue:
    parts: List[Part]
    start: int = 0
    end: int = 0
    text: str = ""

    @property
    def is_event(self) -> bool:
        return all(p.is_event for p in self.parts)

    @property
    def start_raw(self) -> int:
        return min(p.start for p in self.parts)

    @property
    def speakers(self) -> List[str]:
        out: List[str] = []
        for p in self.parts:
            if p.speaker not in out:
                out.append(p.speaker)
        return out


@dataclass
class ConversionReport:
    input_format: str = ""
    words: int = 0
    events: int = 0
    units: int = 0
    cues: int = 0
    dialog_cues: int = 0
    over_cps: int = 0
    over_wpm: int = 0
    under_min_duration: int = 0
    long_lines: int = 0
    overlaps_fixed: int = 0
    events_attached: int = 0
    events_dropped: int = 0
    warnings: List[str] = field(default_factory=list)

    def summary(self) -> str:
        lines = [
            f"Input format      : {self.input_format}",
            f"Words / events    : {self.words} / {self.events}",
            f"Sentence units    : {self.units}",
            f"Cues written      : {self.cues} ({self.dialog_cues} two-speaker cues)",
            f"Overlaps repaired : {self.overlaps_fixed}",
            f"SDH attached/drop : {self.events_attached} / {self.events_dropped}",
            f"Still > max CPS   : {self.over_cps}",
            f"Still > max WPM   : {self.over_wpm}",
            f"Still < min dur.  : {self.under_min_duration}",
            f"Lines > max length: {self.long_lines}",
        ]
        for w in self.warnings[:50]:
            lines.append("WARNING: " + w)
        if len(self.warnings) > 50:
            lines.append(f"... {len(self.warnings) - 50} more warnings")
        return "\n".join(lines)


LogFn = Callable[[str], None]


# --------------------------------------------------------------------------------------
# Input parsing
# --------------------------------------------------------------------------------------

EVENT_RE = re.compile(r"^\s*[\[\(][^\]\)]*[\]\)]\s*$")


def _ms(x: Any) -> int:
    return int(round(float(x) * 1000.0))


def _pretty_speaker(spk_id: str) -> str:
    """'speaker_0' -> 'Speaker 0'; anything else returned as-is."""
    m = re.fullmatch(r"(?i)speaker[_\- ]?(\d+)", spk_id or "")
    if m:
        return f"Speaker {int(m.group(1))}"
    return spk_id or "Speaker"


def detect_format(data: Any) -> str:
    """Return 'aircaps', 'elevenlabs_segments', 'elevenlabs_words' or raise ValueError."""
    if isinstance(data, dict):
        model = str(data.get("model", ""))
        if "utterances" in data and isinstance(data["utterances"], list):
            if model.lower().startswith("a5s") or data.get("object") == "transcript" or (
                    data["utterances"] and "words" in data["utterances"][0]):
                return "aircaps"
        if isinstance(data.get("segments"), list) and data["segments"] and isinstance(data["segments"][0], dict) \
                and "words" in data["segments"][0] and "start_time" in data["segments"][0]:
            return "elevenlabs_segments"
        if isinstance(data.get("words"), list) and data["words"]:
            w0 = data["words"][0]
            if "type" in w0 or "speaker_id" in w0 or ("start" in w0 and "end" in w0 and "text" in w0):
                if "utterances" in data:
                    return "aircaps"
                return "elevenlabs_words"
    raise ValueError("Unrecognised JSON layout: expected AirCaps (utterances/words) "
                     "or ElevenLabs Scribe (segments or words with start/end).")


def _finish_turn_words(words: List[Word]) -> List[Word]:
    """Repair missing / non-monotonic timings inside one speaker turn."""
    out: List[Word] = []
    for w in words:
        if out and w.start < out[-1].end:
            # overlapping tokens inside one turn: keep monotonic
            w.start = out[-1].end
        if w.end < w.start:
            w.end = w.start
        out.append(w)
    return out


def parse_aircaps(data: Dict[str, Any]) -> List[List[Word]]:
    """AirCaps A5S: use `utterances` (already one speaker each) so that overlapping
    speech is not interleaved word-by-word; fall back to flat `words`."""
    turns: List[List[Word]] = []
    utts = data.get("utterances") or []
    if utts:
        for u in utts:
            spk = str(u.get("speaker", "speaker_0"))
            words = []
            for w in u.get("words") or []:
                txt = str(w.get("text", "")).strip()
                if not txt:
                    continue
                words.append(Word(txt, _ms(w["start"]), _ms(w["end"]), spk, _pretty_speaker(spk),
                                  bool(EVENT_RE.match(txt))))
            if not words and u.get("text"):
                # utterance without word timings: spread evenly
                toks = str(u["text"]).split()
                s, e = _ms(u["start"]), _ms(u["end"])
                step = (e - s) / max(1, len(toks))
                words = [Word(t, int(s + i * step), int(s + (i + 1) * step), spk, _pretty_speaker(spk),
                              bool(EVENT_RE.match(t))) for i, t in enumerate(toks)]
            if words:
                turns.append(_finish_turn_words(words))
    else:
        turns = _group_flat_words(data.get("words") or [], "start", "end", "speaker")
    return turns


def _group_flat_words(raw_words: Sequence[Dict[str, Any]], ks: str, ke: str, kspk: str) -> List[List[Word]]:
    """Group a flat word list into speaker turns (consecutive same-speaker words)."""
    turns: List[List[Word]] = []
    cur: List[Word] = []
    cur_spk = None
    for w in raw_words:
        wtype = w.get("type", "word")
        if wtype == "spacing":
            continue
        txt = str(w.get("text", "")).strip()
        if not txt:
            continue
        spk = str(w.get(kspk) or w.get("speaker_id") or "speaker_0")
        is_event = wtype == "audio_event" or bool(EVENT_RE.match(txt))
        if w.get(ks) is None or w.get(ke) is None:
            # no timing: attach to previous word's end
            s = cur[-1].end if cur else (turns[-1][-1].end if turns else 0)
            e = s
        else:
            s, e = _ms(w[ks]), _ms(w[ke])
        word = Word(txt, s, e, spk, _pretty_speaker(spk), is_event)
        if cur and spk != cur_spk:
            turns.append(_finish_turn_words(cur))
            cur = []
        cur.append(word)
        cur_spk = spk
    if cur:
        turns.append(_finish_turn_words(cur))
    return turns


def parse_elevenlabs_segments(data: Dict[str, Any]) -> List[List[Word]]:
    """ElevenLabs Scribe v3 'segments' export: each segment has speaker {id,name} and
    words incl. spacing tokens (text ' ') and bracketed audio events ('[music]')."""
    turns: List[List[Word]] = []
    for seg in data.get("segments") or []:
        spk_obj = seg.get("speaker") or {}
        if isinstance(spk_obj, dict):
            spk = str(spk_obj.get("id") or spk_obj.get("name") or "speaker_0")
            name = str(spk_obj.get("name") or _pretty_speaker(spk))
        else:
            spk = str(spk_obj)
            name = _pretty_speaker(spk)
        words: List[Word] = []
        for w in seg.get("words") or []:
            if w.get("type") == "spacing":
                continue
            txt = str(w.get("text", "")).strip()
            if not txt:
                continue
            is_event = w.get("type") == "audio_event" or bool(EVENT_RE.match(txt))
            words.append(Word(txt, _ms(w["start_time"]), _ms(w["end_time"]), spk, name, is_event))
        if words:
            turns.append(_finish_turn_words(words))
    return turns


def parse_elevenlabs_words(data: Dict[str, Any]) -> List[List[Word]]:
    """Raw ElevenLabs speech-to-text API response: top-level `words` with
    type word|spacing|audio_event, speaker_id, start, end."""
    return _group_flat_words(data.get("words") or [], "start", "end", "speaker_id")


def load_input(path: str) -> Tuple[str, List[List[Word]]]:
    with open(path, "r", encoding="utf-8-sig") as f:
        data = json.load(f)
    fmt = detect_format(data)
    if fmt == "aircaps":
        turns = parse_aircaps(data)
    elif fmt == "elevenlabs_segments":
        turns = parse_elevenlabs_segments(data)
    else:
        turns = parse_elevenlabs_words(data)
    # Order speaker turns by start time (stable); words inside a turn stay together.
    turns.sort(key=lambda t: t[0].start)
    # Merge adjacent turns of the same speaker (ElevenLabs segments are sentence-sized).
    merged: List[List[Word]] = []
    for t in turns:
        if merged and merged[-1][0].speaker == t[0].speaker and t[0].start >= merged[-1][-1].start:
            merged[-1].extend(t)
        else:
            merged.append(t)
    return fmt, merged


# --------------------------------------------------------------------------------------
# Sentence end detection (ported + extended from the notebook's is_valid_sentence_end)
# --------------------------------------------------------------------------------------

DEFAULT_ABBREVIATIONS = {
    # titles / honorifics
    "mr.", "mrs.", "ms.", "mx.", "dr.", "prof.", "sr.", "jr.", "st.", "mt.", "ft.", "rev.", "hon.",
    "sgt.", "capt.", "col.", "gen.", "lt.", "lieut.", "cmdr.", "maj.", "pvt.", "cpl.", "adm.",
    "rep.", "sen.", "gov.", "pres.", "supt.", "insp.", "det.", "msgr.", "fr.", "br.",
    # latin / common
    "vs.", "etc.", "e.g.", "i.e.", "cf.", "al.", "et al.", "approx.", "a.m.", "p.m.", "ca.",
    # business
    "inc.", "corp.", "ltd.", "co.", "llc.", "bros.", "dept.", "assn.", "univ.",
    # measures / misc
    "oz.", "lb.", "lbs.", "in.", "ft.", "mi.", "min.", "hr.", "hrs.", "sec.", "mo.", "yr.",
    "ave.", "blvd.", "rd.", "ste.", "apt.",
    # months / days
    "jan.", "feb.", "mar.", "apr.", "jun.", "jul.", "aug.", "sep.", "sept.", "oct.", "nov.", "dec.",
    "mon.", "tue.", "tues.", "wed.", "thu.", "thur.", "thurs.", "fri.", "sat.", "sun.",
}

# Abbreviations that are only abbreviations when followed by a number ("No. 5", "Vol. 2").
NUMERIC_ABBREVIATIONS = {"no.", "vol.", "fig.", "figs.", "pp.", "p.", "ch.", "art.", "nos.", "ed.", "op."}

_CLOSERS = "'\"”’)\]"
_SENT_END_RE = re.compile(r"[.?!…][" + _CLOSERS + r"]*$")
_ELLIPSIS_RE = re.compile(r"(\.\.\.|…)[" + _CLOSERS + r"]*$")
_ACRONYM_RE = re.compile(r"(?:[a-z]{1,3}\.){2,}")           # u.s., a.n., ph.d., e.g.
_INITIAL_RE = re.compile(r"[a-z]\.")                           # j. (initial)
_NUMBER_END_RE = re.compile(r"\d\.$")


def _strip_closers(token: str) -> str:
    return re.sub(r"[" + _CLOSERS + r"]+$", "", token)


class SentenceEndDetector:
    def __init__(self, extra_abbreviations: Sequence[str] = (), abbreviation_file: Optional[str] = None):
        self.abbreviations = set(DEFAULT_ABBREVIATIONS)
        for a in extra_abbreviations:
            a = a.strip().lower()
            if a:
                self.abbreviations.add(a if a.endswith(".") else a + ".")
        if abbreviation_file and os.path.isfile(abbreviation_file):
            try:
                with open(abbreviation_file, "r", encoding="utf-8-sig") as f:
                    for line in f:
                        a = line.strip().lower()
                        if a and not a.startswith("#"):
                            self.abbreviations.add(a if a.endswith(".") else a + ".")
            except OSError:
                pass

    def is_sentence_end(self, token: str, next_token: Optional[str] = None) -> bool:
        """True if `token` terminates a sentence. `next_token` (if known) refines the
        decision for ellipses and numeric abbreviations."""
        if not token:
            return False
        tok = token.strip()
        if not _SENT_END_RE.search(tok):
            return False
        last = _strip_closers(tok.split()[-1]).lower()
        # strip leading quotes/brackets/dashes
        last = re.sub(r"^[\"'“‘(\[\-–—]+", "", last)

        if _ELLIPSIS_RE.search(tok):
            # "Well..." -> only a sentence end if the next word is capitalised (or unknown)
            if next_token is None:
                return True
            nt = next_token.strip().lstrip("\"'“‘([-–—")
            return bool(nt) and (nt[0].isupper() or nt[0].isdigit())

        if last.endswith("?") or last.endswith("!"):
            return True

        if last in self.abbreviations:
            return False
        if last in NUMERIC_ABBREVIATIONS:
            nt = (next_token or "").strip()
            if nt and nt[0].isdigit():
                return False
            # "No." as a word ("No. Stop it.") is a real sentence end
            return True
        if _ACRONYM_RE.fullmatch(last):          # U.S.  A.N.  Ph.D.
            return False
        if _INITIAL_RE.fullmatch(last):          # single initial "J."
            return False
        if _NUMBER_END_RE.search(last) and next_token and next_token.strip()[:1].isdigit():
            return False                         # "3. 14"? keep conservative
        return True


# --------------------------------------------------------------------------------------
# Segmentation into Units
# --------------------------------------------------------------------------------------

def build_units(turns: List[List[Word]], settings: Dict[str, Any], detector: SentenceEndDetector,
                report: ConversionReport) -> List[Unit]:
    """Cut speaker turns into Units (sentence-sized pieces).

    Boundaries: sentence ends (abbreviation-aware), speaker changes (turns), long
    pauses, and - depending on `sdh_mode` - audio events.
    """
    include_sdh = bool(settings["include_sdh"])
    inline_sdh = settings["sdh_mode"] == "inline"
    sdh_upper = bool(settings["sdh_uppercase"])
    pause_break = int(settings["pause_break_ms"])
    sdh_gap = int(settings["sdh_group_gap_ms"])
    inline_event_max_ms = int(settings["inline_event_max_ms"])
    units: List[Unit] = []
    last_speaker: Optional[str] = None   # last speaker that actually produced speech

    for turn in turns:
        words = [w for w in turn if include_sdh or not w.is_event]
        if not words:
            continue
        has_speech = any(not w.is_event for w in words)
        if sdh_upper:
            for w in words:
                if w.is_event:
                    w.text = w.text.upper()
        # Clamp the duration of audio events that sit inside speech so they do not
        # drag a cue's end time far past the spoken words.
        for i, w in enumerate(words):
            if not w.is_event:
                continue
            limit = w.end
            if i + 1 < len(words):            # never run into the next word of the same turn
                limit = min(limit, max(words[i + 1].start, w.start + 300))
            if inline_sdh and i > 0 and not words[i - 1].is_event and (w.start - words[i - 1].end) <= sdh_gap:
                limit = min(limit, w.start + inline_event_max_ms)   # event trailing speech
            if w.end > limit:
                w.end = limit

        cur: List[Word] = []
        starts_sentence = True
        starts_turn = words[0].speaker != last_speaker
        prev_ended_sentence = True
        if has_speech:
            last_speaker = words[0].speaker

        def flush(next_starts_sentence: bool):
            nonlocal cur, starts_sentence, starts_turn
            if cur:
                units.append(Unit(cur, cur[0].speaker, cur[0].speaker_name, starts_sentence,
                                  starts_turn, all(w.is_event for w in cur),
                                  ends_sentence=next_starts_sentence))
                cur = []
                starts_turn = False
            starts_sentence = next_starts_sentence

        for i, w in enumerate(words):
            nxt = words[i + 1].text if i + 1 < len(words) else None
            if cur:
                prev = cur[-1]
                gap = w.start - prev.end
                cur_is_event_only = all(x.is_event for x in cur)
                if w.is_event and cur_is_event_only:
                    # event following events: group them when close together
                    if (w.start - prev.start) > sdh_gap:
                        flush(True)
                elif w.is_event and not cur_is_event_only:
                    # event following speech
                    if not inline_sdh or gap > sdh_gap:
                        flush(prev_ended_sentence)
                elif (not w.is_event) and cur_is_event_only:
                    # speech following event(s)
                    if not inline_sdh or gap > sdh_gap:
                        flush(starts_sentence if not prev_ended_sentence else True)
                elif prev_ended_sentence:
                    flush(True)
                elif pause_break > 0 and gap > pause_break:
                    flush(False)
            cur.append(w)
            if not w.is_event:
                prev_ended_sentence = detector.is_sentence_end(w.text, nxt)
            # an event leaves the sentence state untouched
        flush(True)

    report.units = len(units)
    return units


# --------------------------------------------------------------------------------------
# Line breaking helpers
# --------------------------------------------------------------------------------------

# Words we prefer NOT to end a line/cue with (break *before* them instead).
CONJUNCTIONS_PREPOSITIONS = {
    "and", "or", "but", "nor", "so", "yet", "because", "although", "though", "while", "whereas",
    "if", "unless", "until", "when", "whenever", "where", "wherever", "after", "before", "since",
    "than", "that", "which", "who", "whom", "whose", "as",
    "in", "on", "at", "to", "for", "with", "without", "from", "by", "of", "about", "into", "onto",
    "over", "under", "through", "between", "among", "against", "during", "within", "across",
    "toward", "towards", "upon", "like", "near", "off", "out", "up", "down",
}
# Words that should not END a line (articles, determiners, possessives, auxiliaries, prepositions).
NO_BREAK_AFTER = CONJUNCTIONS_PREPOSITIONS | {
    "a", "an", "the", "this", "that", "these", "those", "my", "your", "his", "her", "its", "our",
    "their", "some", "any", "every", "each", "no", "very", "too", "quite", "rather",
    "i", "he", "she", "we", "they", "it", "you",
    "am", "is", "are", "was", "were", "be", "been", "being", "do", "does", "did", "have", "has",
    "had", "will", "would", "can", "could", "shall", "should", "may", "might", "must",
    "not", "n't", "mr.", "mrs.", "ms.", "dr.",
}

_CLAUSE_PUNCT_RE = re.compile(r"[,;:—–\-][" + _CLOSERS + r"]*$")
_SENT_PUNCT_RE = re.compile(r"[.?!…][" + _CLOSERS + r"]*$")


def _clean_word(t: str) -> str:
    return re.sub(r"^[\"'“‘(\[]+|[\"'”’)\].,;:!?…]+$", "", t).lower()


def break_penalty(before: Word, after: Word, sentence_bonus: float, clause_bonus: float,
                  conj_bonus: float, nobreak_penalty: float, pause_bonus: float) -> float:
    """Score for breaking between `before` and `after` (lower is better)."""
    p = 0.0
    bt = before.text
    if before.is_event or after.is_event:
        p -= clause_bonus          # breaking next to [sound descriptions] is always welcome
    if _SENT_PUNCT_RE.search(bt):
        p -= sentence_bonus
    elif _CLAUSE_PUNCT_RE.search(bt):
        p -= clause_bonus
    elif not (before.is_event or after.is_event):
        if _clean_word(after.text) in CONJUNCTIONS_PREPOSITIONS:
            p -= conj_bonus
        if _clean_word(bt) in NO_BREAK_AFTER:
            p += nobreak_penalty
    gap = after.start - before.end
    if gap >= 700:
        p -= pause_bonus * 1.5
    elif gap >= 300:
        p -= pause_bonus
    return p


def greedy_line_count(words: Sequence[Word], width: int, prefix_len: int) -> int:
    lines, cur = 0, ""
    first = True
    for w in words:
        pl = prefix_len if first else 0
        test = (cur + " " + w.text) if cur else w.text
        if pl + len(test) > width and cur:
            lines += 1
            first = False
            cur = w.text
        else:
            cur = test
    return lines + (1 if cur else 0)


def wrap_balanced(words: Sequence[Word], width: int, prefix_len: int, unbreak_shorter_than: int,
                  max_lines: int) -> List[str]:
    """Break a chunk into the minimum number of lines, then choose the break points
    that balance line lengths while favouring punctuation / conjunction boundaries.

    Port of the notebook's "binary search for the narrowest width" balancing, replaced by
    an explicit optimisation so punctuation can be favoured as a tie-breaker.
    """
    n = len(words)
    if n == 0:
        return []
    total = sum(len(w.text) for w in words) + (n - 1) + prefix_len
    # "Unbreak subtitles shorter than": keep short text on one line.
    if n == 1 or total <= width or total <= unbreak_shorter_than:
        return [" ".join(w.text for w in words)]
    k = greedy_line_count(words, width, prefix_len)   # minimum number of lines needed
    if k == 1:
        return [" ".join(w.text for w in words)]

    # DP over lines: cost = sum |len - target| + penalties
    lens = [len(w.text) for w in words]
    pref = [0]
    for L in lens:
        pref.append(pref[-1] + L)

    def seg_len(i: int, j: int, first: bool) -> int:  # words i..j-1
        return pref[j] - pref[i] + (j - i - 1) + (prefix_len if first else 0)

    target = total / k
    INF = float("inf")
    # best[l][j] = (cost, prev_i) using l lines for words[0:j]
    best = [[(INF, -1)] * (n + 1) for _ in range(k + 1)]
    best[0][0] = (0.0, -1)
    for l in range(1, k + 1):
        for j in range(1, n + 1):
            for i in range(0, j):
                if best[l - 1][i][0] == INF:
                    continue
                L = seg_len(i, j, l == 1)
                if L > width and (j - i) > 1:
                    continue
                cost = best[l - 1][i][0] + abs(L - target)
                if j < n:
                    cost += break_penalty(words[j - 1], words[j], 20.0, 2.0, 1.5, 3.0, 0.5)
                if cost < best[l][j][0]:
                    best[l][j] = (cost, i)
    if best[k][n][0] == INF:
        # fall back to greedy wrap
        return _greedy_wrap(words, width, prefix_len)
    # reconstruct
    cuts = []
    j = n
    for l in range(k, 0, -1):
        i = best[l][j][1]
        cuts.append((i, j))
        j = i
    cuts.reverse()
    return [" ".join(w.text for w in words[i:j]) for i, j in cuts]


def _greedy_wrap(words: Sequence[Word], width: int, prefix_len: int) -> List[str]:
    lines: List[str] = []
    cur = ""
    for w in words:
        pl = prefix_len if not lines else 0
        test = (cur + " " + w.text) if cur else w.text
        if pl + len(test) > width and cur:
            lines.append(cur)
            cur = w.text
        else:
            cur = test
    if cur:
        lines.append(cur)
    return lines


# --------------------------------------------------------------------------------------
# Chunking a Unit into cue-sized pieces (dynamic programming)
# --------------------------------------------------------------------------------------

class Chunker:
    def __init__(self, settings: Dict[str, Any]):
        self.s = settings
        self.width = int(settings["single_line_max_length"])
        self.max_lines = int(settings["max_lines"])
        self.max_dur = int(settings["max_duration_ms"])
        self.min_chunk = int(settings["min_chunk_chars"])

    def fits(self, words: Sequence[Word], prefix_len: int, max_lines: int, max_dur: Optional[int] = None) -> bool:
        if not words:
            return True
        if len(words) == 1:
            return True  # a single word can never be split further (duration is capped later)
        if (words[-1].end - words[0].start) > (self.max_dur if max_dur is None else max_dur):
            return False
        return greedy_line_count(words, self.width, prefix_len) <= max_lines

    def split(self, words: List[Word], first_prefix_len: int, other_prefix_len: int,
              first_max_lines: Optional[int] = None, first_max_dur: Optional[int] = None,
              first_min_start: Optional[int] = None,
              first_prepend: Optional[Sequence[Word]] = None,
              last_max_lines: Optional[int] = None) -> Optional[List[List[Word]]]:
        """Split `words` into the fewest chunks that each fit a cue; among those pick
        the most balanced split with the nicest break points.

        first_max_lines / first_max_dur / first_min_start constrain the FIRST chunk (used
        when it should share a cue with the previous speaker's line); first_prepend are
        words already in that cue from the same speaker (same-speaker sentence merge) -
        they must fit together with the first chunk. Returns None if impossible.
        """
        prepend = list(first_prepend or [])
        pre_chars = (sum(len(w.text) for w in prepend) + len(prepend)) if prepend else 0
        n = len(words)
        if n == 0:
            return []
        max_lines = self.max_lines
        f_lines = max_lines if first_max_lines is None else first_max_lines
        total_chars = sum(len(w.text) for w in words) + n - 1
        # upper bound on words per chunk (chars budget) to keep the DP fast
        max_words = n
        INF = float("inf")

        # fit cache
        fit_cache: Dict[Tuple[int, int, bool], bool] = {}

        def fit(i: int, j: int, first: bool) -> bool:
            key = (i, j, first)
            if key not in fit_cache:
                seg = words[i:j]
                last = (j == n)
                if first:
                    lim = f_lines if not (last and last_max_lines is not None) else min(f_lines, last_max_lines)
                    ok = self.fits(prepend + seg, first_prefix_len, lim, first_max_dur)
                    if ok and first_min_start is not None and (seg[-1].end - first_min_start) > self.max_dur:
                        ok = False
                else:
                    lim = max_lines if not (last and last_max_lines is not None) else min(max_lines, last_max_lines)
                    ok = self.fits(seg, other_prefix_len, lim)
                fit_cache[key] = ok
            return fit_cache[key]

        # Pass 1: minimum number of chunks ending at each j (first chunk special)
        min_chunks = [INF] * (n + 1)
        min_chunks[0] = 0
        for j in range(1, n + 1):
            for i in range(max(0, j - max_words), j):
                if min_chunks[i] == INF:
                    continue
                if not fit(i, j, i == 0):
                    continue
                c = min_chunks[i] + 1
                if c < min_chunks[j]:
                    min_chunks[j] = c
        if min_chunks[n] == INF:
            return None
        K = int(min_chunks[n])
        if K == 1:
            return [list(words)]

        # Pass 2: among splits with exactly K chunks, minimise balance cost + break penalties
        target = (total_chars + pre_chars) / K
        best: List[List[Tuple[float, int]]] = [[(INF, -1)] * (n + 1) for _ in range(K + 1)]
        best[0][0] = (0.0, -1)
        for k in range(1, K + 1):
            for j in range(1, n + 1):
                for i in range(0, j):
                    if best[k - 1][i][0] == INF or not fit(i, j, i == 0):
                        continue
                    L = sum(len(w.text) for w in words[i:j]) + (j - i - 1) + (pre_chars if i == 0 else 0)
                    cost = best[k - 1][i][0] + 0.3 * abs(L - target)   # cue balance matters less than lines
                    if L < self.min_chunk:
                        cost += (self.min_chunk - L) * 2.0
                    if j < n:
                        cost += break_penalty(words[j - 1], words[j], 30.0, 8.0, 2.0, 6.0, 3.0)
                    if cost < best[k][j][0]:
                        best[k][j] = (cost, i)
        if best[K][n][0] == INF:
            return None
        cuts = []
        j = n
        for k in range(K, 0, -1):
            i = best[k][j][1]
            cuts.append((i, j))
            j = i
        cuts.reverse()
        return [list(words[i:j]) for i, j in cuts]


# --------------------------------------------------------------------------------------
# Cue assembly (incl. dialog / two-speaker cues)
# --------------------------------------------------------------------------------------

class Assembler:
    def __init__(self, settings: Dict[str, Any], report: ConversionReport):
        self.s = settings
        self.report = report
        self.chunker = Chunker(settings)
        self.width = int(settings["single_line_max_length"])
        self.max_lines = int(settings["max_lines"])
        self.max_dur = int(settings["max_duration_ms"])
        self.labels_on = bool(settings["include_speaker_names"])
        self.label_mode = settings["speaker_label_mode"]
        self.label_fmt = settings["speaker_label_format"] or "{name}: "
        self.dashes_on = settings["dialog_style"] != "none" and (
            not self.labels_on or bool(settings["dashes_with_labels"]))
        self.count_prefix = bool(settings["include_speaker_in_length"])
        self.name_map = self._parse_name_map(settings.get("speaker_name_map", ""))
        self.sdh_upper = bool(settings["sdh_uppercase"])
        self.merge_gap = int(settings["dialog_merge_max_gap_ms"])
        self.merge_sentences = bool(settings["merge_short_sentences"])
        self.sentence_gap = int(settings["sentence_merge_max_gap_ms"])
        self.max_speakers = max(1, int(settings["max_speakers_per_cue"]))

    @staticmethod
    def _parse_name_map(text: str) -> Dict[str, str]:
        out: Dict[str, str] = {}
        for pair in re.split(r"[,;\n]+", text or ""):
            if "=" in pair:
                k, v = pair.split("=", 1)
                k, v = k.strip(), v.strip()
                if k and v:
                    out[k] = v
                    out[k.lower()] = v
        return out

    def display_name(self, part: Part) -> str:
        for key in (part.speaker, part.speaker_name, part.speaker.lower(), part.speaker_name.lower()):
            if key in self.name_map:
                return self.name_map[key]
        return part.speaker_name

    # --- prefixes -------------------------------------------------------------------
    def dash_string(self, position: int, n_parts: int) -> str:
        style = self.s["dialog_style"]
        if style == "none":
            return ""
        if style.startswith("dash_second") and position == 0:
            return ""
        return "- " if style.endswith("_space") else "-"

    def part_prefix(self, cue_parts: List[Part], idx: int) -> str:
        """Dash and/or speaker label that precedes a part's first line."""
        part = cue_parts[idx]
        if part.is_event:
            return ""
        prefix = ""
        n_speakers = len({p.speaker for p in cue_parts})
        if self.dashes_on and n_speakers >= 2:
            # Continuation lines (sentence started in an earlier cue) get no dash.
            if part.starts_sentence:
                prefix += self.dash_string(idx, len(cue_parts))
        if self.labels_on:
            label = False
            if self.label_mode == "every_cue":
                label = idx == 0 or cue_parts[idx - 1].speaker != part.speaker
            else:
                label = part.starts_turn
            if label:
                prefix += self.label_fmt.format(name=self.display_name(part))
        return prefix

    def prefix_len(self, prefix: str) -> int:
        return len(prefix) if self.count_prefix else 0

    def first_chunk_prefix_len(self, unit: Unit, dialog: bool) -> int:
        p = ""
        if dialog and self.dashes_on and unit.starts_sentence:
            p += self.dash_string(1, 2)
        if self.labels_on and (unit.starts_turn or self.label_mode == "every_cue"):
            p += self.label_fmt.format(name=unit.speaker_name)
        return self.prefix_len(p)

    def other_chunk_prefix_len(self, unit: Unit) -> int:
        if self.labels_on and self.label_mode == "every_cue":
            return self.prefix_len(self.label_fmt.format(name=unit.speaker_name))
        return 0

    # --- rendering --------------------------------------------------------------
    def render_lines(self, cue_parts: List[Part]) -> List[List[str]]:
        """Return per-part line lists (with prefixes applied) for a tentative cue."""
        out: List[List[str]] = []
        n_parts = len(cue_parts)
        for idx, part in enumerate(cue_parts):
            prefix = self.part_prefix(cue_parts, idx)
            if part.is_event:
                lines = wrap_balanced(part.words, self.width, 0, int(self.s["unbreak_shorter_than"]), self.max_lines)
            else:
                # In a multi-part cue every part gets at most (max_lines - other parts) lines.
                allowed = self.max_lines - (n_parts - 1) if n_parts > 1 else self.max_lines
                lines = wrap_balanced(part.words, self.width, self.prefix_len(prefix),
                                      int(self.s["unbreak_shorter_than"]), max(1, allowed))
            if prefix and lines:
                lines[0] = prefix + lines[0]
            out.append(lines)
        return out

    def _lines_ok(self, line_groups: List[List[str]]) -> bool:
        total = sum(len(g) for g in line_groups)
        if total > self.max_lines:
            return False
        limit = max(self.width, int(self.s["unbreak_shorter_than"]))
        return all(len(line) <= limit or len(line.split()) == 1 for g in line_groups for line in g)

    # --- main --------------------------------------------------------------------------
    def assemble(self, units: List[Unit]) -> List[Cue]:
        cues: List[Cue] = []

        def new_cue(part: Part) -> None:
            cues.append(Cue([part]))

        def mk_part(unit: Unit, words: List[Word], first: bool, last: bool = False) -> Part:
            return Part(words, unit.speaker, unit.speaker_name,
                        unit.starts_sentence if first else False,
                        unit.starts_turn if first else False, unit.is_event,
                        ends_sentence=(unit.ends_sentence if last else False))

        def wants_one_line_tail(idx: int) -> bool:
            """True if the NEXT unit is a different speaker who could share a cue with the
            last chunk of this unit (so prefer ending this unit on a single line)."""
            if idx + 1 >= len(units) or self.max_lines < 2:
                return False
            cur, nxt = units[idx], units[idx + 1]
            if nxt.is_event or cur.is_event or nxt.speaker == cur.speaker:
                return False
            if not (0 <= nxt.start - cur.end <= self.merge_gap):
                return False
            dash = self.dash_string(1, 2) if (self.dashes_on and nxt.starts_sentence) else ""
            return greedy_line_count(nxt.words, self.width, self.prefix_len(dash)) == 1

        def split_tail_aware(unit_words: List[Word], fpl: int, opl: int, idx: int, **kw):
            chunks = self.chunker.split(unit_words, fpl, opl, **kw)
            if chunks and wants_one_line_tail(idx):
                alt = self.chunker.split(unit_words, fpl, opl, last_max_lines=1, **kw)
                if alt and len(alt) == len(chunks):
                    return alt
            return chunks

        for uidx, unit in enumerate(units):
            if unit.is_event:
                chunks = self.chunker.split(unit.words, 0, 0) or [unit.words]
                for ch in chunks:
                    new_cue(Part(ch, unit.speaker, unit.speaker_name, True, True, True))
                continue

            prev = cues[-1] if cues else None

            # (a) Same speaker, short follow-up sentence: join the previous cue if the WHOLE
            #     unit fits there (never split a sentence just to fill the previous cue).
            if (
                self.merge_sentences
                and prev is not None
                and not prev.is_event
                and unit.starts_sentence
                and prev.parts[-1].speaker == unit.speaker
                and prev.parts[-1].ends_sentence
                and 0 <= (unit.start - prev.parts[-1].end) <= self.sentence_gap
                and (unit.end - prev.start_raw) <= self.max_dur
            ):
                last = prev.parts[-1]
                trial_part = Part(last.words + unit.words, last.speaker, last.speaker_name,
                                  last.starts_sentence, last.starts_turn, False)
                trial_parts = prev.parts[:-1] + [trial_part]
                groups = self.render_lines(trial_parts)
                if self._lines_ok(groups):
                    prev.parts = trial_parts
                    continue

            # (b) Can the first chunk of this unit join the previous cue as a second speaker?
            can_dialog = (
                prev is not None
                and not prev.is_event
                and self.max_lines >= 2
                and len(prev.speakers) < self.max_speakers
                and unit.speaker not in prev.speakers
                and prev.parts[-1].speaker != unit.speaker
                and (unit.start - prev.parts[-1].end) <= self.merge_gap
                and unit.start >= prev.parts[0].start
            )
            merged = False
            if can_dialog:
                prev_parts = prev.parts
                lines_used = sum(len(g) for g in self.render_lines(prev_parts + [
                    Part(unit.words[:1], unit.speaker, unit.speaker_name, unit.starts_sentence,
                         unit.starts_turn)])[:-1])
                room = self.max_lines - lines_used
                if room >= 1:
                    fpl = self.first_chunk_prefix_len(unit, dialog=True)
                    chunks = split_tail_aware(
                        unit.words, fpl, self.other_chunk_prefix_len(unit), uidx,
                        first_max_lines=room, first_min_start=prev_parts[0].start)
                    if chunks and len(chunks) > 1:
                        # Splitting the sentence only to share a cue is acceptable when the
                        # sentence needs several cues anyway, or when the remainder is substantial.
                        plain = self.chunker.split(unit.words, self.first_chunk_prefix_len(unit, dialog=False),
                                                   self.other_chunk_prefix_len(unit)) or [unit.words]
                        small_rest = any(
                            (sum(len(w.text) for w in ch) + len(ch) - 1) < self.chunker.min_chunk
                            for ch in chunks[1:])
                        if len(chunks) > len(plain) + 1 or (len(chunks) > len(plain) and small_rest):
                            chunks = None
                    if chunks:
                        trial_parts = prev_parts + [mk_part(unit, chunks[0], True, len(chunks) == 1)]
                        groups = self.render_lines(trial_parts)
                        if self._lines_ok(groups):
                            prev.parts = trial_parts
                            merged = True
                            for ci, ch in enumerate(chunks[1:], start=1):
                                new_cue(mk_part(unit, ch, False, ci == len(chunks) - 1))
            if merged:
                continue

            chunks = split_tail_aware(unit.words, self.first_chunk_prefix_len(unit, dialog=False),
                                      self.other_chunk_prefix_len(unit), uidx)
            if not chunks:
                chunks = [unit.words]
                self.report.warnings.append(
                    f"Could not fit text within limits at {fmt_ts(unit.start)}: '{unit.words[0].text}...'")
            for i, ch in enumerate(chunks):
                new_cue(mk_part(unit, ch, i == 0, i == len(chunks) - 1))

        # Final rendering of text + raw timing
        for cue in cues:
            groups = self.render_lines(cue.parts)
            for part, lines in zip(cue.parts, groups):
                part.lines = lines
            cue.text = "\n".join(line for g in groups for line in g)
            cue.start = min(p.start for p in cue.parts)
            cue.end = max(p.end for p in cue.parts)
            if len(cue.speakers) > 1:
                self.report.dialog_cues += 1
        return cues


# --------------------------------------------------------------------------------------
# Timing rules
# --------------------------------------------------------------------------------------

def _char_count(text: str, include_spaces: bool) -> int:
    t = text.replace("\n", " " if include_spaces else "")
    if not include_spaces:
        t = t.replace(" ", "")
    return len(t)


def _word_count(text: str) -> int:
    return len(text.split())


def apply_timing(cues: List[Cue], settings: Dict[str, Any], report: ConversionReport) -> List[Cue]:
    if not cues:
        return cues
    min_dur = int(settings["min_duration_ms"])
    max_dur = int(settings["max_duration_ms"])
    gap = int(settings["min_gap_ms"])
    max_cps = float(settings["max_cps"])
    opt_cps = float(settings["optimal_cps"])
    max_wpm = int(settings["max_wpm"])
    inc_sp = bool(settings["cps_includes_spaces"])
    extend_opt = bool(settings["extend_to_optimal_cps"])
    max_ext = int(settings["max_end_extension_ms"])
    snap = bool(settings["snap_to_frames"])
    fps = float(settings["frame_rate"]) or 23.976

    cues.sort(key=lambda c: (c.start, c.end))
    speech_end = [c.end for c in cues]  # remember where speech actually ends

    # 1) cap max duration
    for c in cues:
        if c.end - c.start > max_dur:
            c.end = c.start + max_dur

    # 2a) speech has priority over stand-alone sound-description cues: clip events around
    #     speech; events with no room left are attached to the speech cue (extra line) or dropped
    width = int(settings["single_line_max_length"])
    speech = [c for c in cues if not c.is_event]
    keep: List[Cue] = []
    for c in cues:
        if not c.is_event:
            keep.append(c)
            continue
        s0, e0 = c.start, c.end
        # speech cues overlapping [s0, e0)
        over = [sc for sc in speech if sc.start < e0 + gap and sc.end + gap > s0]
        if not over:
            keep.append(c)
            continue
        # find the first free window of >= 300 ms inside the event span
        window = None
        cursor = s0
        for sc in sorted(over, key=lambda x: x.start):
            if sc.start - gap - cursor >= 300:
                window = (cursor, sc.start - gap)
                break
            cursor = max(cursor, sc.end + gap)
        if window is None and e0 - cursor >= 300:
            window = (cursor, e0)
        if window is not None:
            c.start, c.end = window
            keep.append(c)
            continue
        # no room: attach as an extra line to the speech cue that covers the event start
        host = min(over, key=lambda x: abs(x.start - s0))
        lines = host.text.split("\n")
        ev_text = c.text.replace("\n", " ")
        if len(lines) + 1 <= int(settings["max_lines"]) and len(ev_text) <= width:
            host.text = ev_text + "\n" + host.text if s0 <= host.start else host.text + "\n" + ev_text
            report.events_attached += 1
        else:
            report.events_dropped += 1
            report.warnings.append(f"Dropped sound description '{ev_text}' at {fmt_ts(s0)} (no room next to speech)")
    cues = keep
    cues.sort(key=lambda c: (c.start, c.end))
    speech_end = [c.end for c in cues]

    # 2b) repair overlaps between consecutive cues
    for i in range(1, len(cues)):
        prev, cur = cues[i - 1], cues[i]
        if cur.start < prev.end + gap:
            report.overlaps_fixed += 1
            prev_dur = prev.end - prev.start
            cur_dur = cur.end - cur.start
            boundary = prev.end + gap
            # prefer pushing the current cue later if it keeps a reasonable duration
            if cur.end - boundary >= max(min(min_dur, cur_dur * 0.6), 300):
                cur.start = boundary
            elif cur.start - gap - prev.start >= max(min(min_dur, prev_dur * 0.6), 300):
                prev.end = cur.start - gap
            else:
                mid = (prev.end + cur.start) // 2
                prev.end = max(prev.start + 100, mid - gap // 2)
                cur.start = max(prev.end + gap, min(cur.end - 100, mid + gap // 2))
            if cur.end < cur.start + 100:
                cur.end = cur.start + 100

    # 3) extend durations: min duration, max CPS / WPM, optimal CPS
    n = len(cues)
    for i, c in enumerate(cues):
        chars = _char_count(c.text, inc_sp)
        words = _word_count(c.text)
        need_max = max(chars / max_cps * 1000.0 if max_cps > 0 else 0,
                       words / max_wpm * 60000.0 if max_wpm > 0 else 0)
        need_opt = chars / opt_cps * 1000.0 if (extend_opt and opt_cps > 0) else 0
        limit_next = cues[i + 1].start - gap if i + 1 < n else c.end + 10 * 60 * 1000
        hard_target = max(min_dur, need_max)
        # min duration & max CPS always allowed up to the next cue; optimal CPS limited by max_ext
        desired_end = c.start + int(round(hard_target))
        if need_opt > hard_target:
            opt_end = min(c.start + int(round(need_opt)), speech_end[i] + max_ext)
            desired_end = max(desired_end, opt_end)
        new_end = min(max(c.end, desired_end), limit_next, c.start + max_dur)
        if new_end > c.end:
            c.end = new_end
        # still too short? pull the start earlier
        if c.end - c.start < hard_target:
            limit_prev = cues[i - 1].end + gap if i > 0 else 0
            new_start = max(limit_prev, c.end - int(round(hard_target)), c.end - max_dur)
            if new_start < c.start:
                c.start = new_start

    # 4) frame snapping
    if snap:
        frame = 1000.0 / fps
        for c in cues:
            c.start = int(round(round(c.start / frame) * frame))
            c.end = int(round(round(c.end / frame) * frame))
            if c.end <= c.start:
                c.end = int(round(c.start + frame))
        for i in range(1, len(cues)):
            if cues[i].start - cues[i - 1].end < gap:
                cues[i - 1].end = int(round(round((cues[i].start - gap) / frame - 0.5) * frame))
                if cues[i - 1].end <= cues[i - 1].start:
                    cues[i - 1].end = int(round(cues[i - 1].start + frame))

    # 5) final statistics
    width = int(settings["single_line_max_length"])
    for c in cues:
        dur = max(1, c.end - c.start)
        chars = _char_count(c.text, inc_sp)
        if chars / dur * 1000.0 > max_cps + 1e-6:
            report.over_cps += 1
        if _word_count(c.text) / dur * 60000.0 > max_wpm + 1e-6:
            report.over_wpm += 1
        if dur < min_dur:
            report.under_min_duration += 1
        for line in c.text.split("\n"):
            if len(line) > width:
                report.long_lines += 1
    return cues


# --------------------------------------------------------------------------------------
# SRT output
# --------------------------------------------------------------------------------------

def fmt_ts(ms: int) -> str:
    ms = max(0, int(ms))
    h, rem = divmod(ms, 3_600_000)
    m, rem = divmod(rem, 60_000)
    s, ms = divmod(rem, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def cues_to_srt(cues: Sequence[Cue]) -> str:
    blocks = []
    for i, c in enumerate(cues, start=1):
        text = c.text.replace("-->", "->")
        blocks.append(f"{i}\n{fmt_ts(c.start)} --> {fmt_ts(c.end)}\n{text}\n")
    return "\n".join(blocks)


def write_srt(cues: Sequence[Cue], path: str, encoding: str = "utf-8-sig") -> None:
    data = cues_to_srt(cues)
    with open(path, "w", encoding=encoding, newline="\r\n") as f:
        f.write(data)


# --------------------------------------------------------------------------------------
# Pipeline
# --------------------------------------------------------------------------------------

def convert_turns(turns: List[List[Word]], settings: Dict[str, Any], report: ConversionReport,
                  log: Optional[LogFn] = None) -> List[Cue]:
    log = log or (lambda m: None)
    extra = [a for a in re.split(r"[,;\n]+", settings.get("extra_abbreviations", "") or "") if a.strip()]
    detector = SentenceEndDetector(extra, abbreviations_file())
    report.words = sum(1 for t in turns for w in t if not w.is_event)
    report.events = sum(1 for t in turns for w in t if w.is_event)
    units = build_units(turns, settings, detector, report)
    log(f"Segmented {report.words} words / {report.events} audio events into {len(units)} units.")
    cues = Assembler(settings, report).assemble(units)
    log(f"Assembled {len(cues)} cues ({report.dialog_cues} two-speaker).")
    cues = [c for c in cues if c.text.strip()]
    cues = apply_timing(cues, settings, report)
    report.cues = len(cues)
    return cues


def convert_file(input_path: str, output_path: Optional[str] = None,
                 settings: Optional[Dict[str, Any]] = None,
                 log: Optional[LogFn] = None) -> Tuple[str, ConversionReport]:
    """Convert one JSON file to SRT. Returns (output_path, report)."""
    log = log or (lambda m: None)
    settings = normalize_settings(settings)
    report = ConversionReport()
    fmt, turns = load_input(input_path)
    report.input_format = fmt
    log(f"Detected input format: {fmt}")
    cues = convert_turns(turns, settings, report, log)
    if not output_path:
        output_path = os.path.splitext(input_path)[0] + ".srt"
    write_srt(cues, output_path, settings["encoding"])
    log(f"Wrote {len(cues)} cues to {output_path} ({settings['encoding']}).")
    return output_path, report


# --------------------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------------------

def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="json_to_srt",
        description="Convert AirCaps / ElevenLabs diarized word-timed JSON into a Subtitle-Edit-clean .srt",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    p.add_argument("input", help="input JSON file (AirCaps A5S or ElevenLabs Scribe)")
    p.add_argument("-o", "--output", help="output .srt (default: input name with .srt)")
    p.add_argument("--settings", help="JSON file with settings (e.g. a saved GUI preset)")
    p.add_argument("--print-settings", action="store_true", help="print effective settings and exit")
    p.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    for key, typ, default, label, group, help_ in SETTINGS_SPEC:
        flag = "--" + key.replace("_", "-")
        if typ == "bool":
            p.add_argument(flag, type=lambda v: coerce_setting("include_sdh", v), metavar="0|1",
                           default=None, help=f"{help_} [default: {int(default)}]")
        elif typ == "int":
            p.add_argument(flag, type=int, default=None, help=f"{help_} [default: {default}]")
        elif typ == "float":
            p.add_argument(flag, type=float, default=None, help=f"{help_} [default: {default}]")
        elif typ == "str":
            p.add_argument(flag, type=str, default=None, help=f"{help_} [default: '{default}']")
        else:
            p.add_argument(flag, choices=typ[1], default=None, help=f"{help_} [default: {default}]")
    return p


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_arg_parser().parse_args(argv)
    settings = default_settings()
    if args.settings:
        with open(args.settings, "r", encoding="utf-8-sig") as f:
            settings = normalize_settings(json.load(f))
    for key, *_ in SETTINGS_SPEC:
        val = getattr(args, key, None)
        if val is not None:
            settings[key] = coerce_setting(key, val)
    if args.print_settings:
        print(json.dumps(settings, indent=2))
        return 0
    try:
        out, report = convert_file(args.input, args.output, settings, log=print)
    except (OSError, ValueError, json.JSONDecodeError) as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1
    print(report.summary())
    return 0


if __name__ == "__main__":
    sys.exit(main())
