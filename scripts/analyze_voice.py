#!/usr/bin/env python3
"""Create local, time-aligned speech evidence for the interview voice rubric.

This tool does not assign a voice score. The evaluator uses the transcript,
timestamps, pauses, and ASR diagnostics as evidence under references/voice-scoring-rubric.md.
Audio and transcripts remain on the local machine; faster-whisper downloads its
model from Hugging Face on first use unless the model is already cached.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("audio", type=Path, help="Path to an interview recording")
    parser.add_argument(
        "--language",
        default="ko",
        help="Whisper language code (default: ko; use auto to detect)",
    )
    parser.add_argument(
        "--model",
        default="small",
        help="faster-whisper model size or local model directory (default: small)",
    )
    parser.add_argument(
        "--output",
        type=Path,
        help="Output JSON path (default: <audio-name>.voice-evidence.json)",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    audio_path = args.audio.expanduser().resolve()
    if not audio_path.is_file():
        print(f"Audio file not found: {audio_path}", file=sys.stderr)
        return 2

    try:
        import av

        # PyAV 19 removed metadata_errors from av.open, while some published
        # faster-whisper versions still pass it. It only controlled metadata
        # parsing behavior, so drop that legacy keyword on newer PyAV.
        if int(av.__version__.split(".", maxsplit=1)[0]) >= 19:
            original_open = av.open

            def open_without_legacy_metadata_arg(*open_args, **open_kwargs):
                open_kwargs.pop("metadata_errors", None)
                return original_open(*open_args, **open_kwargs)

            av.open = open_without_legacy_metadata_arg

        from faster_whisper import WhisperModel
    except ImportError:
        print(
            "Missing dependency. Install the project's audio requirements with "
            "the configured Python: pip install -r requirements-audio.txt",
            file=sys.stderr,
        )
        return 3

    language = None if args.language.lower() == "auto" else args.language
    model = WhisperModel(args.model, device="cpu", compute_type="int8")
    segments, info = model.transcribe(
        str(audio_path),
        language=language,
        vad_filter=True,
        word_timestamps=True,
        beam_size=5,
    )

    rows = []
    for segment in segments:
        text = segment.text.strip()
        if not text:
            continue
        words = []
        for word in segment.words or []:
            words.append(
                {
                    "start": round(word.start, 3),
                    "end": round(word.end, 3),
                    "text": word.word,
                    "probability": round(word.probability, 4),
                }
            )
        rows.append(
            {
                "start": round(segment.start, 3),
                "end": round(segment.end, 3),
                "text": text,
                "avg_logprob": round(segment.avg_logprob, 4),
                "no_speech_probability": round(segment.no_speech_prob, 4),
                "words": words,
            }
        )

    pauses = []
    spoken_seconds = 0.0
    spoken_characters = 0
    for index, segment in enumerate(rows):
        duration = max(0.0, segment["end"] - segment["start"])
        spoken_seconds += duration
        spoken_characters += len(re.sub(r"\s+", "", segment["text"]))
        if index + 1 < len(rows):
            gap = max(0.0, rows[index + 1]["start"] - segment["end"])
            if gap >= 0.75:
                pauses.append(
                    {
                        "start": segment["end"],
                        "end": rows[index + 1]["start"],
                        "duration_seconds": round(gap, 3),
                    }
                )

    speech_rate = None
    if spoken_seconds > 0:
        speech_rate = round(spoken_characters / spoken_seconds * 60, 1)

    result = {
        "source_audio": str(audio_path),
        "analysis_mode": "local_asr_evidence_only",
        "model": args.model,
        "language": info.language,
        "language_probability": round(info.language_probability, 4),
        "audio_duration_seconds": round(info.duration, 3),
        "spoken_duration_seconds": round(spoken_seconds, 3),
        "recognized_character_count_without_spaces": spoken_characters,
        "estimated_speech_rate_chars_per_minute": speech_rate,
        "pause_threshold_seconds": 0.75,
        "pauses": pauses,
        "segments": rows,
        "limitations": [
            "ASR confidence is not a calibrated intelligibility score.",
            "The transcript does not identify interviewer and candidate speakers.",
            "This report cannot assess voice quality, emotion, personality, or accent.",
            "Review segment timestamps against the source audio before assigning a voice score.",
        ],
    }

    output_path = args.output or audio_path.with_suffix(".voice-evidence.json")
    output_path = output_path.expanduser().resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"Voice evidence written to: {output_path}")
    print(
        f"Recognized {len(rows)} segments; duration={info.duration:.1f}s; "
        f"estimated speech rate={speech_rate if speech_rate is not None else 'n/a'} chars/min."
    )
    print("This report is evidence only; score with the voice rubric after audio review.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
