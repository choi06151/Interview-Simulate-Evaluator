# 로컬 음성 분석 워크플로

`scripts/analyze_voice.py`는 faster-whisper로 전사/타임스탬프를 추출합니다. `--diarize`를 추가하면 SpeechBrain ECAPA 음성 임베딩으로 화자 구간을 로컬에서 묶고, 후보 화자를 명시하면 해당 구간만 대상으로 속도·쉼·상대 음량·클리핑·F0 통계를 계산합니다. 모든 원본 오디오와 분석 산출물은 기본적으로 로컬에 둡니다.

## 설치

기본 전사만 사용할 때:

```powershell
python -m pip install -r requirements-audio.txt
```

화자 분리와 운율 통계를 사용할 때 추가 설치:

```powershell
python -m pip install -r requirements-audio-diarization.txt
```

처음 화자 분리를 실행할 때 공개 SpeechBrain 모델 파일을 내려받습니다. Hugging Face 토큰은 필요하지 않습니다. 모델이 내려받아진 뒤에는 임베딩 생성과 구간 묶기가 로컬에서 실행됩니다.

```powershell
python scripts/analyze_voice.py "C:\path\to\interview.m4a" --language ko --diarize --num-speakers 2
```

위 명령은 먼저 speaker id와 구간을 JSON에 기록합니다. `diarization.speaker_turns`와 `segments_with_speakers`를 살펴 실제 후보자 label을 확인한 다음 명시적으로 선택하세요.

```powershell
python scripts/analyze_voice.py "C:\path\to\interview.m4a" --language ko --diarize --num-speakers 2 --candidate-speaker SPEAKER_01
```

후보자 label 선택을 확인할 때는 첫 실행 때와 같은 모델/환경을 사용하세요. 최종 JSON의 `diarization.candidate_audio_metrics`만 지원자 전용 통계입니다. 전체 통계나 면접관 구간을 지원자 점수에 섞지 않습니다.

## 산출 자료

기본 출력은 입력 옆의 `<파일명>.voice-evidence.json`이며 `--output`으로 바꿀 수 있습니다. Schema v2의 전사 구간 외에 다음 필드를 포함합니다.

- `diarization.status`: `not_requested`, `candidate_speaker_unconfirmed`, `available`, `unavailable` 등 분석 상태
- `speaker_turns`: 시간 범위와 익명 speaker label
- `segments_with_speakers`: ASR 구간에 겹침 비율을 사용해 연결한 label. 낮거나 모호한 할당은 별도로 표시
- `candidate_speaker`: 사용자가 선택한 label과 매핑 방법
- `candidate_audio_metrics`: 후보자 구간만의 발화 시간/문자 속도, 같은 화자의 내부 쉼, 상대 RMS/dBFS·clipping, F0 통계, 신호 메타데이터
- 최상위 `spoken_duration_seconds`, `estimated_speech_rate_chars_per_minute`, `pauses`: 전체 녹음의 화자 혼합 통계이며 후보자 음성 점수에는 사용하지 않음
- `limitations` 및 각 지표의 `status`: 자동 추정 한계와 측정 불가 원인

기본 전사 명령은 화자 분리를 하지 않고 기존 ASR 자료를 생성합니다.

```powershell
python scripts/analyze_voice.py "C:\path\to\interview.mp3" --language ko --model small
```

## 오류와 수동 판단

- SpeechBrain 모델 파일이나 패키지가 없어 화자 분리가 실패하면 전사 결과는 저장하고 `diarization.status=unavailable`과 안내를 기록합니다. 이 경우 후보자 전용 점수/통계는 만들지 않습니다.
- 화자 label은 사람의 정체를 알지 못합니다. 면접 전사에 적힌 `참석자 1/2`와 자동 `SPEAKER_00/01`은 별개 체계이므로 label 순서를 추정하지 말고 구간을 대조해 선택합니다.
- 음성 점수를 내기 전에 반드시 후보자 화자를 선택하거나 사람이 후보자 발화 구간의 타임스탬프를 확인해야 합니다. 둘 다 없으면 전체 혼합 음성으로 채점하지 말고 음성 점수 및 최종 총점을 보류합니다.
- 짧은 발화, 중첩 발화, 잡음은 diarization 오류를 만들 수 있습니다. ASR 구간과 화자 구간 겹침률은 매핑 참고치이지 확률이 아닙니다.
- 녹음 장비와 gain이 RMS/clipping에 영향을 줍니다. F0는 개인 음성 및 발화 내용에 영향받습니다. 어떤 수치에도 보편적인 합격/불합격 임계값을 두지 않습니다.
- pause는 호흡이 아닙니다. v2.0은 흡기/호기 소리를 자동 검출하거나 호흡 횟수를 세지 않습니다.
- 지표는 점수 근거입니다. 직접 청취하지 않은 경우 실제 발음 명료도, 음량 인상, 억양 전달 효과를 단정하지 않고 잠정/저신뢰도 또는 평가 불가를 명시합니다.

음성 항목과 배점은 [음성 평가 루브릭 v2.0](voice-scoring-rubric.md)을 따릅니다. ASR 텍스트의 내용 정확도는 음성 점수로 다시 채점하지 않습니다.
