# 참가자 및 회차별 세션 기록 스키마

세 참가자를 분리하고 회차별 면접 원문과 질문 단위 평가를 누적한다. 프로젝트의 `records/participants/<participant_id>/sessions/<session_id>/` 폴더마다 `transcript.md`와 `evaluation.md`를 함께 저장한다.

## 기록 규칙

- 참가자 한 명당 고유 `participant_id`를 유지한다. 현재 ID는 P01, P02, P03이다.
- 면접 한 회당 새 세션 폴더를 만든다. 기존 회차를 덮어쓰지 않는다.
- 질문 점수는 실제 면접에서 묻고 답한 평가 질문 단위로 기록한다. 동일 질문의 후속 질문은 해당 단위 안에 포함한다.
- `text_score`는 `question_scores`에 기록된 채점 가능 질문 점수의 단순 평균을 정수 반올림한 값이다. 태그별 점수는 만들지 않는다.
- 음성 자료가 있으면 `voice_score`는 음성 평가 루브릭의 4개 항목 점수 합계다. 음성 자료가 없거나 분석할 수 없으면 `null`로 기록한다.
- 두 점수가 모두 있으면 `total_score`는 `(text_score + voice_score) / 2`를 정수 반올림한 값이며 최대 100점이다. 음성 점수가 `null`이면 최종 총점도 `null`이다. 기존 `/100` 텍스트 단독 기록은 소급해 환산하지 않는다.
- 질문에 답하지 않았으면 0점이다. 질문하지 않은 주제는 질문 배열에 억지로 넣지 않고 `unassessed_areas`에 기록한다.
- 원문은 transcript.md에 그대로 보존한다. 정정 시 기존 원문을 덮어쓰지 않는다.

## JSON 레코드 예시

```json
{
  "participant_id": "P01",
  "display_name": "예시 이름",
  "session_id": "P01-2026-10-19-01",
  "date": "2026-10-19",
  "role": "게임 엔진 클라이언트 프로그래머",
  "level": "미제공",
  "interview_type": "모의 면접",
  "rubric_version": "text-2.0+voice-1.0",
  "transcript_scope": "제공된 대화 전문",
  "audio_provided": true,
  "audio_scope": "전체 면접 녹음",
  "coverage": "낮음",
  "question_scores": [
    {
      "question_id": "Q1",
      "question_summary": "UObject의 수명과 GC 참조 설명",
      "competency_tags": ["C++/런타임", "엔진/클라이언트 구조"],
      "answer_score": 40,
      "status": "채점",
      "evidence": ["Q1 응답의 짧은 발췌"],
      "strengths": ["질문의 핵심 용어를 구분함"],
      "gaps": ["참조가 객체 수명에 미치는 설명이 없음"],
      "followups_and_hints": "면접관 힌트 없음"
    },
    {
      "question_id": "Q2",
      "question_summary": "충돌 상황에서 팀을 조율하는 방법",
      "competency_tags": ["협업/업무 상황"],
      "answer_score": 60,
      "status": "채점",
      "evidence": ["Q2 응답의 짧은 발췌"],
      "strengths": ["실행 단계를 제안함"],
      "gaps": ["결과 확인 방법이 빠짐"],
      "followups_and_hints": "후속 질문 뒤 사례를 구체화함"
    }
  ],
  "text_score": 50,
  "voice_score": 72,
  "voice_score_breakdown": {
    "intelligibility": 30,
    "spoken_structure": 22,
    "question_responsiveness": 14,
    "delivery_control": 6
  },
  "total_score": 61,
  "total_score_max": 100,
  "unassessed_areas": ["성능/메모리", "게임플레이 구현", "네트워크/플랫폼"],
  "strengths": ["..."],
  "improvement_priorities": ["..."],
  "next_practice_goals": ["..."],
  "comparison": {
    "previous_session_ids": [],
    "comparability": "비교 불가",
    "notes": "이전 기록 없음"
  },
  "evidence_notes": "텍스트 50점과 음성 72점의 평균을 정수 반올림한 예시 데이터임"
}
```

예시 점수는 형식 설명용이다. 운영 시 `text_score`가 채점된 질문 점수의 평균과, `total_score`가 텍스트·음성 점수의 평균을 정수 반올림한 값인지 확인한다. 음성 미평가 시 `voice_score`, 네 항목 점수, `total_score`는 `null`로 두고 사유를 기록한다.

## 참가자 목록

| participant_id | 표시 이름 | 상태 |
|---|---|---|
| P01 | 최재원 | 기록 있음 |
| P02 | 김호준 | 기록 있음 |
| P03 | 이름 미제공 | 기록 있음 |
