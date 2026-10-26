"""Candidate-only acoustic measurements for interview recordings.

These measurements are evidence for a human/evaluator review, not automatic
quality grades. In particular, pauses are not proof of breathing and pitch is
not evidence of emotion, personality, or voice quality.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np


PAUSE_THRESHOLDS_SECONDS = (0.75, 1.5, 3.0)
ANALYSIS_SAMPLE_RATE = 22_050


def overlap_seconds(start: float, end: float, other_start: float, other_end: float) -> float:
    """Return the positive temporal overlap between two intervals."""
    return max(0.0, min(end, other_end) - max(start, other_start))


def assign_speakers(
    segments: list[dict[str, Any]],
    speaker_turns: list[dict[str, Any]],
    *,
    minimum_overlap_ratio: float = 0.5,
    ambiguity_margin: float = 0.05,
) -> list[dict[str, Any]]:
    """Attach the best-overlapping speaker to each ASR segment.

    A diarization label is assigned only when one speaker covers enough of the
    ASR interval and clearly wins over the next speaker. Ratios are geometric
    overlap indicators, not calibrated diarization probabilities.
    """
    assigned: list[dict[str, Any]] = []
    for segment in segments:
        start = float(segment["start"])
        end = float(segment["end"])
        duration = max(0.0, end - start)
        overlaps: dict[str, float] = {}
        for turn in speaker_turns:
            speaker = str(turn["speaker"])
            overlaps[speaker] = overlaps.get(speaker, 0.0) + overlap_seconds(
                start, end, float(turn["start"]), float(turn["end"])
            )

        ranked = sorted(overlaps.items(), key=lambda item: item[1], reverse=True)
        row = dict(segment)
        if not ranked or duration <= 0:
            row.update(speaker=None, speaker_assignment="unmapped", overlap_ratio=0.0)
        else:
            best_speaker, best_overlap = ranked[0]
            ratio = min(1.0, best_overlap / duration)
            runner_up_ratio = ranked[1][1] / duration if len(ranked) > 1 else 0.0
            if ratio < minimum_overlap_ratio:
                row.update(speaker=None, speaker_assignment="low_overlap", overlap_ratio=round(ratio, 4))
            elif ratio - runner_up_ratio <= ambiguity_margin:
                row.update(speaker=None, speaker_assignment="ambiguous", overlap_ratio=round(ratio, 4))
            else:
                row.update(
                    speaker=best_speaker,
                    speaker_assignment="assigned",
                    overlap_ratio=round(ratio, 4),
                )
        assigned.append(row)
    return assigned


def _decode_mono(audio_path: Path) -> tuple[np.ndarray, int, int, int]:
    """Decode audio through PyAV and resample mono float PCM for measurements."""
    import av

    with av.open(str(audio_path)) as container:
        stream = next((item for item in container.streams if item.type == "audio"), None)
        if stream is None:
            raise ValueError("The audio file has no audio stream")
        source_rate = int(stream.rate or 0)
        channel_count = len(stream.codec_context.layout.channels) if stream.codec_context.layout else 0
        resampler = av.AudioResampler(format="flt", layout="mono", rate=ANALYSIS_SAMPLE_RATE)
        samples: list[np.ndarray] = []
        for frame in container.decode(stream):
            for converted in resampler.resample(frame):
                samples.append(np.asarray(converted.to_ndarray(), dtype=np.float32).reshape(-1))
        for converted in resampler.resample(None):
            samples.append(np.asarray(converted.to_ndarray(), dtype=np.float32).reshape(-1))

    if not samples:
        raise ValueError("The audio stream contains no decodable samples")
    signal = np.concatenate(samples)
    signal = np.nan_to_num(signal, nan=0.0, posinf=0.0, neginf=0.0)
    return signal, ANALYSIS_SAMPLE_RATE, source_rate, channel_count


def _merge_intervals(intervals: list[tuple[float, float]]) -> list[tuple[float, float]]:
    merged: list[list[float]] = []
    for start, end in sorted(intervals):
        if end <= start:
            continue
        if merged and start <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])
    return [(start, end) for start, end in merged]


def _sample_bounds(start: float, end: float, sample_rate: int, sample_count: int) -> tuple[int, int]:
    left = max(0, min(sample_count, int(start * sample_rate)))
    right = max(left, min(sample_count, int(end * sample_rate)))
    return left, right


def _pause_summary(
    candidate_turns: list[dict[str, Any]],
    all_turns: list[dict[str, Any]],
) -> dict[str, Any]:
    ordered = sorted(candidate_turns, key=lambda turn: float(turn["start"]))
    gaps: list[float] = []
    for current, following in zip(ordered, ordered[1:]):
        current_end = float(current["end"])
        following_start = float(following["start"])
        gap = following_start - current_end
        if gap <= 0:
            continue
        another_speaker_spoke = any(
            turn["speaker"] != current["speaker"]
            and float(turn["start"]) < following_start
            and float(turn["end"]) > current_end
            for turn in all_turns
        )
        if not another_speaker_spoke:
            gaps.append(gap)
    return {
        "status": "available",
        "eligible_candidate_internal_gaps": len(gaps),
        "threshold_counts": {
            f"at_least_{threshold:g}_seconds": sum(gap >= threshold for gap in gaps)
            for threshold in PAUSE_THRESHOLDS_SECONDS
        },
        "gap_durations_seconds": [round(gap, 3) for gap in gaps],
        "interpretation": "Pause intervals are not reliable breath-event detections.",
    }


def _loudness_summary(signal: np.ndarray, intervals: list[tuple[float, float]], sample_rate: int) -> dict[str, Any]:
    chunks = []
    for start, end in intervals:
        left, right = _sample_bounds(start, end, sample_rate, signal.size)
        if right > left:
            chunks.append(signal[left:right])
    if not chunks:
        return {"status": "unavailable", "reason": "No candidate audio samples were isolated."}

    candidate_audio = np.concatenate(chunks)
    frame_size = max(1, int(sample_rate * 0.1))
    rms_values = []
    for offset in range(0, candidate_audio.size, frame_size):
        frame = candidate_audio[offset : offset + frame_size]
        if frame.size:
            rms_values.append(float(np.sqrt(np.mean(np.square(frame, dtype=np.float64)))))
    rms_dbfs = 20.0 * np.log10(np.maximum(np.asarray(rms_values), 1e-8))
    peak = float(np.max(np.abs(candidate_audio)))
    return {
        "status": "available",
        "frame_duration_seconds": 0.1,
        "rms_dbfs_median": round(float(np.median(rms_dbfs)), 2),
        "rms_dbfs_p10": round(float(np.percentile(rms_dbfs, 10)), 2),
        "rms_dbfs_p90": round(float(np.percentile(rms_dbfs, 90)), 2),
        "peak_dbfs": round(float(20.0 * np.log10(max(peak, 1e-8))), 2),
        "clipped_sample_ratio_at_0_999": round(float(np.mean(np.abs(candidate_audio) >= 0.999)), 6),
        "interpretation": "Relative to this recording; microphone gain and distance affect the values.",
    }


def _prosody_summary(
    signal: np.ndarray,
    intervals: list[tuple[float, float]],
    sample_rate: int,
) -> dict[str, Any]:
    try:
        import librosa
    except ImportError:
        return {"status": "unavailable", "reason": "Install the optional audio-analysis dependencies for F0 analysis."}

    f0_values: list[np.ndarray] = []
    voiced_frames = 0
    total_frames = 0
    for start, end in intervals:
        left, right = _sample_bounds(start, end, sample_rate, signal.size)
        audio = signal[left:right]
        if audio.size < int(sample_rate * 0.5):
            continue
        try:
            f0, voiced_flag, _ = librosa.pyin(
                audio,
                fmin=65.0,
                fmax=500.0,
                sr=sample_rate,
                frame_length=2048,
                hop_length=512,
            )
        except (ValueError, FloatingPointError):
            continue
        total_frames += int(voiced_flag.size)
        voiced_frames += int(np.count_nonzero(voiced_flag))
        valid = np.asarray(f0)[np.isfinite(f0) & (np.asarray(f0) > 0)]
        if valid.size:
            f0_values.append(valid)

    if not f0_values:
        return {"status": "unavailable", "reason": "Not enough reliable voiced candidate audio for F0 estimation."}
    f0 = np.concatenate(f0_values)
    p10, median, p90 = np.percentile(f0, [10, 50, 90])
    return {
        "status": "available",
        "f0_hz_median": round(float(median), 2),
        "f0_hz_p10": round(float(p10), 2),
        "f0_hz_p90": round(float(p90), 2),
        "f0_hz_p90_minus_p10": round(float(p90 - p10), 2),
        "voiced_frame_ratio": round(voiced_frames / total_frames, 4) if total_frames else None,
        "interpretation": "Pitch variation is descriptive only; it is not a proxy for emotion, confidence, or quality.",
    }


def analyze_candidate_audio(
    audio_path: Path,
    segments: list[dict[str, Any]],
    candidate_speaker: str,
    *,
    speaker_turns: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Calculate candidate-only acoustic evidence from diarized speech turns."""
    candidate_turns = [
        turn for turn in (speaker_turns or []) if str(turn.get("speaker")) == candidate_speaker
    ]
    candidate_segments = [
        segment for segment in segments if segment.get("speaker") == candidate_speaker
    ]
    if not candidate_turns:
        return {
            "status": "unavailable",
            "candidate_speaker": candidate_speaker,
            "reason": "No diarized turns were assigned to the selected candidate speaker.",
            "limitations": ["Candidate-specific metrics were not calculated."],
        }

    signal, sample_rate, source_rate, channels = _decode_mono(audio_path)
    # Prefer recognized candidate speech intervals over broad speaker-turn
    # spans, which can contain response-planning silence and inflate duration.
    speech_sources = [
        segment for segment in candidate_segments
        if segment.get("speaker_assignment") == "assigned"
    ] or candidate_turns
    clipped_intervals = []
    for turn in speech_sources:
        start = max(0.0, float(turn["start"]))
        end = min(signal.size / sample_rate, float(turn["end"]))
        if end > start:
            clipped_intervals.append((start, end))
    intervals = _merge_intervals(clipped_intervals)
    speech_seconds = sum(end - start for start, end in intervals)
    character_count = sum(
        len("".join(str(segment.get("text", "")).split()))
        for segment in candidate_segments
        if segment.get("speaker_assignment") == "assigned"
    )
    rate = round(character_count / speech_seconds * 60.0, 1) if speech_seconds > 0 else None
    segment_rates = []
    for segment in candidate_segments:
        duration = max(0.0, float(segment["end"]) - float(segment["start"]))
        characters = len("".join(str(segment.get("text", "")).split()))
        if segment.get("speaker_assignment") == "assigned" and duration > 0 and characters:
            segment_rates.append(characters / duration * 60.0)
    segment_rate_distribution = None
    if segment_rates:
        p10, median, p90 = np.percentile(segment_rates, [10, 50, 90])
        segment_rate_distribution = {
            "segment_count": len(segment_rates),
            "chars_per_minute_p10": round(float(p10), 1),
            "chars_per_minute_median": round(float(median), 1),
            "chars_per_minute_p90": round(float(p90), 1),
            "interpretation": "Short ASR segment rates are noisy; use as descriptive variation, not a speed grade.",
        }

    return {
        "status": "available" if candidate_segments else "partial",
        "candidate_speaker": candidate_speaker,
        "candidate_turn_count": len(candidate_turns),
        "candidate_speech_seconds": round(speech_seconds, 3),
        "candidate_character_count": character_count,
        "speech_rate_chars_per_minute": rate,
        "segment_speech_rate_distribution": segment_rate_distribution,
        "pause_summary": _pause_summary(
            [
                {"start": start, "end": end, "speaker": candidate_speaker}
                for start, end in intervals
            ],
            speaker_turns or [],
        ),
        "loudness": _loudness_summary(signal, intervals, sample_rate),
        "prosody": _prosody_summary(signal, intervals, sample_rate),
        "audio_metadata": {
            "analysis_sample_rate_hz": sample_rate,
            "source_sample_rate_hz": source_rate,
            "source_channel_count": channels,
        },
        "limitations": [
            "Diarization and temporal alignment may assign short or overlapping turns incorrectly.",
            "Pause intervals do not detect inhalation reliably; do not call them breath counts.",
            "RMS and clipping depend on the recording chain and are not direct measures of speaking quality.",
            "Pitch statistics vary with individual voice and phonetic content; do not use them as universal score thresholds.",
        ],
    }
