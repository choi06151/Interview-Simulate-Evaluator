"""Local microphone capture with periodic, local faster-whisper transcription."""

from __future__ import annotations

import queue
import threading
import wave
from pathlib import Path
from typing import Any


class LiveAudioCapture:
    """Capture mono audio and emit local, timestamped transcript and diarization snapshots."""

    def __init__(self, output_path: Path, model_size: str = "small", chunk_seconds: int = 8,
                 diarization_interval: int = 20, initial_prompt: str = "") -> None:
        self.output_path = output_path
        self.model_size = model_size
        self.chunk_seconds = chunk_seconds
        self.initial_prompt = initial_prompt.strip()
        self.diarization_interval = diarization_interval
        self.events: queue.Queue[dict[str, Any]] = queue.Queue()
        self._audio_blocks: queue.Queue[Any] = queue.Queue()
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name="interview-live-audio", daemon=True)

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _emit(self, kind: str, **values: Any) -> None:
        self.events.put({"type": kind, **values})

    def _run(self) -> None:
        try:
            import numpy as np
            import sounddevice as sd
            import av

            if int(av.__version__.split(".", maxsplit=1)[0]) >= 19:
                original_open = av.open

                def open_without_legacy_metadata_arg(*args: Any, **kwargs: Any) -> Any:
                    kwargs.pop("metadata_errors", None)
                    return original_open(*args, **kwargs)

                av.open = open_without_legacy_metadata_arg

            from faster_whisper import WhisperModel

            self._emit("status", message=f"음성 인식 모델({self.model_size})을 불러오는 중… 첫 실행에는 모델 다운로드가 필요할 수 있습니다.")
            model = WhisperModel(self.model_size, device="cpu", compute_type="int8")
            self.output_path.parent.mkdir(parents=True, exist_ok=True)
            sample_rate = 16_000
            pending: list[Any] = []
            pending_frames = 0
            processed_frames = 0
            next_diarization_frame = sample_rate * self.diarization_interval
            diarization_blocks: list[Any] = []
            diarization_frames = 0
            self._emit("status", message="마이크를 시작합니다…")

            def callback(indata: Any, frames: int, _time_info: Any, status: Any) -> None:
                if status:
                    self._emit("status", message=f"오디오 입력 상태: {status}")
                self._audio_blocks.put(indata.copy())

            with wave.open(str(self.output_path), "wb") as wav_file:
                wav_file.setnchannels(1)
                wav_file.setsampwidth(2)
                wav_file.setframerate(sample_rate)
                with sd.InputStream(
                    samplerate=sample_rate,
                    channels=1,
                    dtype="int16",
                    callback=callback,
                    blocksize=1600,
                ):
                    self._emit("started", message="녹음 중 · 음성은 이 PC에서 전사됩니다.", audio_path=str(self.output_path))
                    while not self._stop.is_set() or not self._audio_blocks.empty():
                        try:
                            block = self._audio_blocks.get(timeout=0.2)
                        except queue.Empty:
                            continue
                        wav_file.writeframes(block.tobytes())
                        pending.append(block[:, 0].astype(np.float32) / 32768.0)
                        diarization_blocks.append(block[:, 0].astype(np.float32) / 32768.0)
                        diarization_frames += len(block)
                        pending_frames += len(block)
                        processed_frames += len(block)
                        if pending_frames >= sample_rate * self.chunk_seconds:
                            audio = np.concatenate(pending)
                            start_seconds = (processed_frames - pending_frames) / sample_rate
                            self._transcribe(model, audio, start_seconds)
                            pending.clear()
                            pending_frames = 0
                        if processed_frames >= next_diarization_frame:
                            snapshot = np.concatenate(diarization_blocks).copy()
                            start_seconds = (processed_frames - diarization_frames) / sample_rate
                            self._emit("diarization_snapshot", audio=snapshot, start_seconds=start_seconds,
                                       end_seconds=processed_frames / sample_rate, final=False)
                            diarization_blocks.clear()
                            diarization_frames = 0
                            next_diarization_frame = processed_frames + sample_rate * self.diarization_interval
                    if pending_frames:
                        audio = np.concatenate(pending)
                        start_seconds = (processed_frames - pending_frames) / sample_rate
                        self._transcribe(model, audio, start_seconds)

            if diarization_blocks:
                snapshot = np.concatenate(diarization_blocks).copy()
                start_seconds = (processed_frames - diarization_frames) / sample_rate
                self._emit("diarization_snapshot", audio=snapshot, start_seconds=start_seconds,
                           end_seconds=processed_frames / sample_rate, final=True)

            self._emit("finished", message="녹음을 저장했습니다.", audio_path=str(self.output_path))
        except Exception as error:  # Surface device/model errors in the UI and preserve partial WAV if created.
            self._emit("error", message=f"실시간 녹음을 시작하거나 처리하지 못했습니다: {error}", audio_path=str(self.output_path))

    def _transcribe(self, model: Any, audio: Any, start_seconds: float) -> None:
        try:
            segments, _info = model.transcribe(
                audio,
                language="ko",
                task="transcribe",
                beam_size=5,
                temperature=0,
                vad_filter=True,
                condition_on_previous_text=False,
                initial_prompt=self.initial_prompt or None,
                without_timestamps=False,
            )
            for segment in segments:
                text = segment.text.strip()
                if text:
                    self._emit(
                        "transcript",
                        text=text,
                        start_seconds=start_seconds + float(segment.start),
                        end_seconds=start_seconds + float(segment.end),
                    )
        except Exception as error:
            self._emit("status", message=f"이 구간 전사를 건너뜁니다: {error}")
