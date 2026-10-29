"""Token-free local speaker diarization using SpeechBrain ECAPA embeddings."""

from __future__ import annotations

import contextlib
import io
import threading
from pathlib import Path
from typing import Any

_SAMPLE_SECONDS = 5.0
_MIN_SAMPLE_SECONDS = 4.0
_MATCH_THRESHOLD = 0.48
_MAX_SPEAKERS = 6
_MODEL: Any = None
_MODEL_LOCK = threading.Lock()
_CENTROIDS: dict[str, Any] = {}
_COUNTS: dict[str, int] = {}
_NEXT_SPEAKER_ID = 0


def reset_diarization() -> None:
    """Clear speaker matches when a new interview recording starts."""
    global _NEXT_SPEAKER_ID
    with _MODEL_LOCK:
        _CENTROIDS.clear()
        _COUNTS.clear()
        _NEXT_SPEAKER_ID = 0


def _speaker_model() -> Any:
    global _MODEL
    with _MODEL_LOCK:
        if _MODEL is None:
            try:
                from speechbrain.inference.speaker import SpeakerRecognition
                from speechbrain.utils.fetching import LocalStrategy
            except ImportError as error:
                raise RuntimeError(
                    "로컬 화자 분리 패키지가 없습니다. 실시간 기능 설치 파일을 다시 실행해 주세요."
                ) from error
            stdout_capture, stderr_capture = io.StringIO(), io.StringIO()
            try:
                # 실행.bat은 pythonw.exe를 사용하므로 콘솔 출력 스트림이 None일 수 있습니다.
                # 첫 모델 다운로드 중 tqdm/Hugging Face가 None.write()를 호출하지 않도록
                # 이 구간의 출력을 임시로 받아 둡니다.
                with contextlib.redirect_stdout(stdout_capture), contextlib.redirect_stderr(stderr_capture):
                    model_dir = Path(__file__).resolve().parent / "models" / "speechbrain-spkrec-ecapa-voxceleb"
                    source = str(model_dir) if (model_dir / "hyperparams.yaml").is_file() else "speechbrain/spkrec-ecapa-voxceleb"
                    _MODEL = SpeakerRecognition.from_hparams(
                        source=source,
                        savedir=str(model_dir),
                        local_strategy=LocalStrategy.COPY,
                        run_opts={"device": "cpu"},
                    )
            except Exception as error:
                details = "\n".join(
                    part.strip() for part in (str(error), stderr_capture.getvalue()[-1200:], stdout_capture.getvalue()[-1200:]) if part.strip()
                )
                raise RuntimeError(
                    f"공개 화자 임베딩 모델을 내려받거나 불러오지 못했습니다: {details}"
                ) from error
    return _MODEL


def _embed(model: Any, segment: Any, torch: Any) -> Any:
    import numpy as np

    waveform = torch.from_numpy(segment.copy()).float().unsqueeze(0)
    with torch.inference_mode():
        vector = model.encode_batch(waveform).squeeze().cpu().numpy()
    vector = np.asarray(vector, dtype=np.float32)
    norm = float(np.linalg.norm(vector))
    if norm < 1e-8:
        raise RuntimeError("음성 구간에서 화자 임베딩을 만들 수 없습니다.")
    return vector / norm


def _assign_speaker(vector: Any, max_speakers: int | None = None) -> str:
    global _NEXT_SPEAKER_ID
    import numpy as np

    best_label, best_score = "", -1.0
    for label, centroid in _CENTROIDS.items():
        score = float(np.dot(vector, centroid))
        if score > best_score:
            best_label, best_score = label, score
    speaker_limit = max_speakers or _MAX_SPEAKERS
    if best_label and (best_score >= _MATCH_THRESHOLD or len(_CENTROIDS) >= speaker_limit):
        count = _COUNTS[best_label]
        merged = _CENTROIDS[best_label] * count + vector
        _CENTROIDS[best_label] = merged / max(float(np.linalg.norm(merged)), 1e-8)
        _COUNTS[best_label] = count + 1
        return best_label
    label = f"SPEAKER_{_NEXT_SPEAKER_ID:02d}"
    _NEXT_SPEAKER_ID += 1
    _CENTROIDS[label] = vector
    _COUNTS[label] = 1
    return label


def diarize_audio_samples(
    audio: Any,
    sample_rate: int,
    start_seconds: float = 0.0,
    max_speakers: int | None = None,
) -> list[dict[str, object]]:
    """Cluster short speech windows into speaker turns; audio remains on this PC."""
    import numpy as np
    import torch

    if sample_rate != 16_000:
        raise ValueError("화자 분리 입력은 16kHz 오디오여야 합니다.")
    samples = np.asarray(audio, dtype=np.float32).reshape(-1)
    if samples.size < int(_MIN_SAMPLE_SECONDS * sample_rate):
        return []
    model = _speaker_model()
    window_frames = int(_SAMPLE_SECONDS * sample_rate)
    min_frames = int(_MIN_SAMPLE_SECONDS * sample_rate)
    turns: list[dict[str, object]] = []
    cursor = 0
    while cursor < samples.size:
        end = min(cursor + window_frames, samples.size)
        segment = samples[cursor:end]
        if segment.size < min_frames:
            if turns:
                turns[-1]["end"] = round(start_seconds + samples.size / sample_rate, 3)
            break
        rms = float(np.sqrt(np.mean(np.square(segment))))
        if rms >= 0.003:
            vector = _embed(model, segment, torch)
            with _MODEL_LOCK:
                speaker = _assign_speaker(vector, max_speakers)
            turn_start = round(start_seconds + cursor / sample_rate, 3)
            turn_end = round(start_seconds + end / sample_rate, 3)
            if turns and turns[-1]["speaker"] == speaker and abs(float(turns[-1]["end"]) - turn_start) < 0.05:
                turns[-1]["end"] = turn_end
            else:
                turns.append({"start": turn_start, "end": turn_end, "speaker": speaker})
        cursor += window_frames
    return turns
