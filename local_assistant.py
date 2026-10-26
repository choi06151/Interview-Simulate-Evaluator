"""Local-only interview answer assistance through an Ollama instance on this PC."""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any


OLLAMA_URL = "http://127.0.0.1:11434"
MODEL_NAME = "qwen3.5:4b"

RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {
        "answer_summary": {"type": "string"},
        "claims": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "claim": {"type": "string"},
                    "assessment": {"type": "string"},
                    "reason": {"type": "string"},
                },
                "required": ["claim", "assessment", "reason"],
                "additionalProperties": False,
            },
        },
        "follow_up_questions": {"type": "array", "items": {"type": "string"}},
        "what_to_verify": {"type": "array", "items": {"type": "string"}},
        "confidence": {"type": "string"},
    },
    "required": ["answer_summary", "claims", "follow_up_questions", "what_to_verify", "confidence"],
    "additionalProperties": False,
}


def list_local_models(timeout: float = 2.0) -> list[str]:
    """List models already downloaded in the local Ollama service."""
    request = urllib.request.Request(f"{OLLAMA_URL}/api/tags", method="GET")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as error:
        raise RuntimeError(
            "Ollama에 연결할 수 없습니다. Ollama를 설치하고 실행한 뒤 로컬 모델을 내려받아 주세요."
        ) from error
    return sorted(
        {
            str(item.get("name", "")).strip()
            for item in payload.get("models", [])
            if item.get("name") and not str(item.get("name", "")).strip().endswith(":cloud")
        },
        key=str.casefold,
    )


def analyze_answer(question: str, transcript_excerpt: str, model: str = MODEL_NAME) -> dict[str, Any]:
    """Analyze candidate-only transcript locally; output is provisional interviewer guidance."""
    selected_model = model.strip() or MODEL_NAME
    if selected_model.endswith(":cloud"):
        raise RuntimeError("외부 전송을 막기 위해 Ollama 클라우드 모델은 사용할 수 없습니다. PC에 설치된 로컬 모델을 선택해 주세요.")
    prompt = f"""당신은 게임 엔진 클라이언트 프로그래머 모의 면접관을 돕는 로컬 보조 도구입니다.
후보자의 최종 점수를 매기지 말고, 답변 요점, 검증이 필요한 기술 주장, 중립적인 꼬리질문을 제안하세요.

주의:
- 아래 내용은 로컬 음성 인식 전사이며 오류가 있을 수 있습니다. 잘못 들린 단어나 문맥이 의심되면 확신을 낮추세요.
- 현재 질문과 답변에 나온 주장만 분석하세요. 질문에 답하지 않은 부분과 근거가 부족한 부분을 구분하세요.
- 평가가 명확하지 않으면 단정하지 말고 assessment에 '확인 필요' 또는 '불확실'이라고 쓰세요.
- 외부 검색이나 참고자료에 접근하지 않습니다. 기억에 의존한 기술 사실은 잠정 의견이라고 밝히고 확인할 문서·실험·질문을 제안하세요.
- 구체적이고 유도하지 않는 꼬리질문을 최대 3개 제안하세요.
- 결과는 한국어로 간결하게 작성하세요.

현재 면접 질문:
{question.strip() or '미입력'}

후보자 답변 전사:
{transcript_excerpt.strip()}
"""
    payload = {
        "model": selected_model,
        "messages": [{"role": "user", "content": prompt}],
        "format": RESPONSE_SCHEMA,
        "stream": False,
        "options": {"temperature": 0.2, "num_ctx": 8192, "num_predict": 1200},
    }
    request = urllib.request.Request(
        f"{OLLAMA_URL}/api/chat",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=180) as response:
            result = json.loads(response.read().decode("utf-8"))
        content = result.get("message", {}).get("content", "")
        if not content:
            raise RuntimeError("로컬 모델이 분석 결과를 반환하지 않았습니다.")
        return json.loads(content)
    except urllib.error.HTTPError as error:
        detail = error.read().decode("utf-8", errors="replace")[:500]
        if error.code == 404:
            raise RuntimeError(f"로컬 모델 '{selected_model}'을 찾지 못했습니다. Ollama에서 모델을 먼저 내려받아 주세요.") from error
        raise RuntimeError(f"로컬 모델 분석 실패 ({error.code}): {detail}") from error
    except (urllib.error.URLError, TimeoutError) as error:
        raise RuntimeError("Ollama에 연결할 수 없습니다. Ollama가 실행 중인지 확인해 주세요.") from error
    except json.JSONDecodeError as error:
        raise RuntimeError("로컬 모델이 올바른 JSON 분석 결과를 반환하지 않았습니다. 다시 분석해 주세요.") from error
