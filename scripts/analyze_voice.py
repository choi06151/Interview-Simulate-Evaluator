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
from importlib.metadata import PackageNotFoundError, version
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
    parser.add_argument(
        "--diarize",
        action="store_true",
        help="Optionally split the recording into speaker turns (requires the diarization extra)",
    )
    parser.add_argument(
        "--num-speakers",
        type=int,
        help="Known speaker count for diarization, when it is known",
    )
    parser.add_argument(
        "--candidate-speaker",
        help="Explicit diarization label to analyze, for example SPEAKER_01",
    )
    return parser.parse_args()


def _package_version(package_name: str) -> str | None:
    try:
        return version(package_name)
    except PackageNotFoundError:
        return None


def _diarize_audio(audio_path: Path, num_speakers: int | None) -> tuple[list[dict], str]:
    """Return locally clustered speaker turns and the installed SpeechBrain version."""
    try:
        import av
        import numpy as np
        from speaker_diarization import diarize_audio_samples, reset_diarization
    except ImportError as error:
        raise RuntimeError(
            "Optional diarization dependency is missing. Install requirements-audio-diarization.txt."
        ) from error

    container = av.open(str(audio_path))
    audio_stream = next((stream for stream in container.streams if stream.type == "audio"), None)
    if audio_stream is None:
        raise RuntimeError("입력 파일에 오디오 스트림이 없습니다.")
    resampler = av.AudioResampler(format="fltp", layout="mono", rate=16_000)
    audio_parts = []
    for frame in container.decode(audio_stream):
        for converted in resampler.resample(frame):
            audio_parts.append(converted.to_ndarray().reshape(-1).astype(np.float32))
    for converted in resampler.resample(None):
        audio_parts.append(converted.to_ndarray().reshape(-1).astype(np.float32))
    container.close()
    if not audio_parts:
        raise RuntimeError("오디오 스트림을 16kHz로 변환하지 못했습니다.")
    reset_diarization()
    turns = diarize_audio_samples(np.concatenate(audio_parts), 16_000, max_speakers=num_speakers)
    return turns, _package_version("speechbrain") or "unknown"


def main() -> int:
    args = parse_args()
    if args.candidate_speaker and not args.diarize:
        print("--candidate-speaker requires --diarize", file=sys.stderr)
        return 2
    if args.num_speakers is not None and args.num_speakers < 1:
        print("--num-speakers must be greater than zero", file=sys.stderr)
        return 2
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
        "schema_version": 2,
        "source_audio": str(audio_path),
        "analysis_mode": "local_asr_evidence_only",
        "model": args.model,
        "language": info.language,
        "language_probability": round(info.language_probability, 4),
        "audio_duration_seconds": round(info.duration, 3),
        "whole_recording_metrics_scope": "mixed_speakers_not_for_candidate_scoring",
        "spoken_duration_seconds": round(spoken_seconds, 3),
        "recognized_character_count_without_spaces": spoken_characters,
        "estimated_speech_rate_chars_per_minute": speech_rate,
        "pause_threshold_seconds": 0.75,
        "pauses": pauses,
        "segments": rows,
        "limitations": [
            "ASR confidence is not a calibrated intelligibility score.",
            "Speaker identity is not inferred unless diarization is requested and the candidate label is explicitly selected.",
            "Top-level duration, speech-rate, and pause metrics cover the mixed full recording and must not be used as candidate voice-score evidence.",
            "Automatic metrics are evidence only and do not assign a voice score.",
        ],
    }

    diarization = {
        "requested": bool(args.diarize),
        "status": "not_requested",
        "model": None,
        "model_version": None,
        "speaker_turns": [],
        "candidate_speaker": None,
        "speaker_mapping_method": None,
        "speaker_mapping_confidence": None,
        "segments_with_speakers": [],
        "candidate_audio_metrics": None,
        "error": None,
    }
    if args.diarize:
        diarization["model"] = "speechbrain/spkrec-ecapa-voxceleb"
        try:
            from voice_metrics import analyze_candidate_audio, assign_speakers

            speaker_turns, diarization_version = _diarize_audio(audio_path, args.num_speakers)
            segments_with_speakers = assign_speakers(rows, speaker_turns)
            diarization.update(
                status="available",
                model_version=diarization_version,
                speaker_turns=speaker_turns,
                segments_with_speakers=segments_with_speakers,
            )
            if args.candidate_speaker:
                speaker_ids = {turn["speaker"] for turn in speaker_turns}
                if args.candidate_speaker not in speaker_ids:
                    diarization.update(
                        status="candidate_speaker_invalid",
                        error="The selected candidate speaker label does not occur in the diarization output.",
                    )
                else:
                    assigned_count = sum(
                        segment.get("speaker") == args.candidate_speaker
                        and segment.get("speaker_assignment") == "assigned"
                        for segment in segments_with_speakers
                    )
                    diarization.update(
                        candidate_speaker=args.candidate_speaker,
                        speaker_mapping_method="explicit_cli_selection",
                        speaker_mapping_confidence="user_confirmed_label; segment overlaps remain model-estimated",
                    )
                    diarization["candidate_audio_metrics"] = analyze_candidate_audio(
                        audio_path,
                        segments_with_speakers,
                        args.candidate_speaker,
                        speaker_turns=speaker_turns,
                    )
                    if not assigned_count:
                        diarization.update(
                            status="candidate_segments_unmapped",
                            error="No ASR segments confidently overlapped the selected speaker; inspect the diarization turns.",
                        )
            else:
                diarization["status"] = "candidate_speaker_unconfirmed"
        except Exception as error:
            # Keep diagnostic records short and free of local paths where possible.
            diarization.update(
                status="unavailable",
                error_type=type(error).__name__,
                error=str(error)[:300],
            )
            print(f"Diarization unavailable: {diarization['error']}", file=sys.stderr)
        result["analysis_mode"] = "local_asr_with_optional_diarization"
        result["limitations"].append(
            "Diarization labels do not identify the candidate; candidate-specific metrics require an explicit speaker selection."
        )
    result["diarization"] = diarization

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
    if diarization["status"] in {"available", "candidate_speaker_unconfirmed"}:
        print(f"Diarization status: {diarization['status']}")
    print("This report contains evidence only; use the voice rubric and review its limitations.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
