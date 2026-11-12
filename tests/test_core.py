"""Unit tests for json_to_srt_core (run: python -m unittest discover -s tests)."""
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import json_to_srt_core as core  # noqa: E402


def words(text, speaker="speaker_0", start=0, step=300, name=None):
    """Build Word objects with evenly spaced timings (ms)."""
    out = []
    t = start
    for tok in text.split():
        out.append(core.Word(tok, t, t + step - 50, speaker, name or core._pretty_speaker(speaker),
                             bool(core.EVENT_RE.match(tok))))
        t += step
    return out


def run(turns, **overrides):
    settings = core.normalize_settings(overrides)
    report = core.ConversionReport()
    cues = core.convert_turns(turns, settings, report)
    return cues, report


class SentenceEndTests(unittest.TestCase):
    def setUp(self):
        self.d = core.SentenceEndDetector(["Cmdr."])

    def test_plain_ends(self):
        self.assertTrue(self.d.is_sentence_end("done."))
        self.assertTrue(self.d.is_sentence_end("really?"))
        self.assertTrue(self.d.is_sentence_end("wow!"))
        self.assertTrue(self.d.is_sentence_end('said."'))
        self.assertFalse(self.d.is_sentence_end("hello,"))

    def test_abbreviations(self):
        for tok in ["Dr.", "Mr.", "Mrs.", "e.g.", "U.S.", "A.N.", "Ph.D.", "J.", "etc.", "Cmdr."]:
            self.assertFalse(self.d.is_sentence_end(tok, "Smith"), tok)

    def test_numeric_abbreviation_context(self):
        self.assertFalse(self.d.is_sentence_end("No.", "5"))
        self.assertTrue(self.d.is_sentence_end("No.", "Stop"))

    def test_ellipsis(self):
        self.assertFalse(self.d.is_sentence_end("well...", "maybe"))
        self.assertTrue(self.d.is_sentence_end("well...", "Maybe"))


class LineBalanceTests(unittest.TestCase):
    def test_broadway_example(self):
        ws = words("played on Broadway, on television, and in films.")
        lines = core.wrap_balanced(ws, 43, 0, 43, 2)
        self.assertEqual(lines, ["played on Broadway, on", "television, and in films."])

    def test_unbreak_short(self):
        ws = words("Short enough for one line.")
        self.assertEqual(core.wrap_balanced(ws, 43, 0, 43, 2), ["Short enough for one line."])

    def test_prefers_sentence_end(self):
        ws = words("Well, just YouTube it. Use your initiative. Surprise me.")
        lines = core.wrap_balanced(ws, 43, 0, 43, 2)
        self.assertEqual(lines[0], "Well, just YouTube it.")


class DialogTests(unittest.TestCase):
    def test_two_speakers_dashes_and_continuation(self):
        a = words("It's got to come from somewhere else.", "speaker_1", start=0)
        b = words("Yeah, that one's also fun because the lows are going to suck, right?",
                  "speaker_2", start=a[-1].end + 200, step=200)
        c = words("So it hits you on both sides.", "speaker_1", start=b[-1].end + 200)
        cues, _ = run([a, b, c])
        texts = [cu.text for cu in cues]
        self.assertEqual(texts[0].split("\n")[0], "- It's got to come from somewhere else.")
        self.assertTrue(texts[0].split("\n")[1].startswith("- Yeah, that one's also fun"))
        # second cue: continuation line without dash, new speaker with dash
        l2 = texts[1].split("\n")
        self.assertFalse(l2[0].startswith("-"))
        self.assertTrue(l2[0].endswith("right?"))
        self.assertEqual(l2[1], "- So it hits you on both sides.")
        # never two speakers on one line / never more than 2 lines
        for t in texts:
            self.assertLessEqual(len(t.split("\n")), 2)

    def test_dialog_style_second_only(self):
        a = words("Heroic.", "speaker_1", start=0)
        b = words("Anytime.", "speaker_2", start=400)
        cues, _ = run([a, b], dialog_style="dash_second_nospace")
        self.assertEqual(cues[0].text, "Heroic.\n-Anytime.")

    def test_speaker_labels(self):
        a = words("Heroic.", "speaker_1", start=0)
        b = words("Anytime.", "speaker_2", start=400)
        cues, _ = run([a, b], include_speaker_names=True, speaker_name_map="speaker_1=George")
        self.assertEqual(cues[0].text, "George: Heroic.\nSpeaker 2: Anytime.")


class SdhTests(unittest.TestCase):
    def test_exclude_sdh_clean(self):
        a = words("Sorry. [laughs] I didn't mean to scare you.", "speaker_1")
        cues, _ = run([a], include_sdh=False)
        full = "\n".join(c.text for c in cues)
        self.assertNotIn("[", full)
        self.assertNotIn("  ", full)
        self.assertTrue(all(c.text.strip() for c in cues))

    def test_include_sdh_inline_and_standalone(self):
        a = words("[music]", "speaker_0", start=0, step=5000)
        b = words("Sorry. [laughs] I didn't mean to scare you.", "speaker_1", start=20000)
        cues, _ = run([a, b])
        self.assertEqual(cues[0].text, "[music]")
        self.assertIn("[laughs]", "\n".join(c.text for c in cues))

    def test_event_only_removed_leaves_no_empty_cue(self):
        a = words("[music]", "speaker_0", start=0, step=5000)
        cues, _ = run([a], include_sdh=False)
        self.assertEqual(cues, [])


class TimingTests(unittest.TestCase):
    def _check_rules(self, cues, s):
        for i, c in enumerate(cues):
            self.assertGreaterEqual(c.end - c.start, s["min_duration_ms"] - 1, c.text)
            self.assertLessEqual(c.end - c.start, s["max_duration_ms"], c.text)
            if i:
                self.assertGreaterEqual(c.start - cues[i - 1].end, s["min_gap_ms"], c.text)
            for line in c.text.split("\n"):
                self.assertLessEqual(len(line), s["single_line_max_length"])
            self.assertLessEqual(len(c.text.split("\n")), s["max_lines"])

    def test_rules_on_synthetic(self):
        turns = [
            words("Yes.", "speaker_1", start=0),
            words("Yes, that's a great idea. How about we turn your apartment into a gym?",
                  "speaker_1", start=1500),
            words("What?", "speaker_2", start=20000, step=200),
            words("Yeah, because then we'd only have nineteen units and no need for a loving manager.",
                  "speaker_1", start=20500, step=250),
        ]
        cues, rep = run(turns)
        self._check_rules(cues, core.default_settings())

    def test_frame_snapping(self):
        cues, _ = run([words("Hello there friend.", start=1000)], snap_to_frames=True, frame_rate=25.0)
        for c in cues:
            self.assertEqual(c.start % 40, 0)
            self.assertEqual(c.end % 40, 0)


class ParsingTests(unittest.TestCase):
    def test_detect_formats(self):
        self.assertEqual(core.detect_format({"model": "a5s-async-v1", "utterances": [], "words": []}), "aircaps")
        self.assertEqual(core.detect_format({"segments": [{"start_time": 0, "end_time": 1, "words": []}]}),
                         "elevenlabs_segments")
        self.assertEqual(core.detect_format({"words": [{"text": "a", "start": 0, "end": 1, "type": "word"}]}),
                         "elevenlabs_words")
        with self.assertRaises(ValueError):
            core.detect_format({"foo": 1})

    def test_elevenlabs_raw_words_format(self):
        data = {"words": [
            {"text": "Hi", "start": 0.0, "end": 0.3, "type": "word", "speaker_id": "speaker_0"},
            {"text": " ", "start": 0.3, "end": 0.4, "type": "spacing", "speaker_id": "speaker_0"},
            {"text": "there.", "start": 0.4, "end": 0.8, "type": "word", "speaker_id": "speaker_0"},
            {"text": "(laughs)", "start": 0.9, "end": 1.5, "type": "audio_event", "speaker_id": "speaker_0"},
        ]}
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "x.json")
            with open(p, "w") as f:
                json.dump(data, f)
            out, rep = core.convert_file(p, os.path.join(d, "x.srt"))
            self.assertEqual(rep.input_format, "elevenlabs_words")
            with open(out, "rb") as f:
                raw = f.read()
            self.assertTrue(raw.startswith(b"\xef\xbb\xbf"))  # UTF-8 BOM
            self.assertIn(b"Hi there. (laughs)", raw)


class SettingsTests(unittest.TestCase):
    def test_defaults_match_subtitle_edit(self):
        d = core.default_settings()
        self.assertEqual((d["single_line_max_length"], d["optimal_cps"], d["max_cps"], d["max_wpm"],
                          d["min_duration_ms"], d["max_duration_ms"], d["min_gap_ms"], d["max_lines"],
                          d["unbreak_shorter_than"], d["dialog_style"], d["cps_includes_spaces"],
                          d["frame_rate"], d["encoding"]),
                         (43, 15.0, 25.0, 300, 1000, 8000, 24, 2, 43, "dash_both_space", True, 23.976,
                          "utf-8-sig"))

    def test_cli_overrides(self):
        p = core.build_arg_parser()
        a = p.parse_args(["in.json", "--max-cps", "20", "--include-sdh", "0"])
        self.assertEqual(a.max_cps, 20.0)
        self.assertIs(a.include_sdh, False)


if __name__ == "__main__":
    unittest.main()
