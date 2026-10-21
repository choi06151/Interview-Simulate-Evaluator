# 게임 엔진 클라이언트 프로그래머 면접 평가기

게임 엔진 클라이언트 프로그래머 모의 면접을 질문별 텍스트 평가와 별도 음성 전달 평가로 기록하는 평가 기준 및 도구 모음입니다.

## 구성

- `SKILL.md`: 면접 평가 AI의 역할과 일관된 평가 절차
- `references/`: 텍스트·음성 루브릭, 회차 기록 스키마, 결과 형식, 음성 분석 안내
- `question-library/`: 공통 면접 질문 모음
- `records/`: 참가자별 회차 원문, 평가, 총점 및 개인별 추천 질문
- `scripts/analyze_voice.py`: MP3/WAV 로컬 음성 인식 및 시간 정보 추출

## 음성 분석

```powershell
python -m pip install -r requirements-audio.txt
python scripts/analyze_voice.py "path\to\interview.mp3" --language ko --model small
```

전사와 시간 정보는 로컬에서 추출합니다. 음성 분석 결과만으로 발음, 음색, 감정, 억양을 판정하지 않습니다. 직접 청취하지 못한 평가는 잠정 점수와 낮은 신뢰도를 명시합니다. 자세한 평가 기준과 제한은 `references/voice-scoring-rubric.md` 및 `references/audio-analysis-workflow.md`를 참고하세요.

## 기록 데이터

`records/participants/`에는 면접 참여자의 실명, 전사, 평가 결과 및 일부 음성 녹음이 포함되어 있습니다. 공개 저장소로 전환할 경우 이 자료의 공개 범위를 먼저 확인하세요.
