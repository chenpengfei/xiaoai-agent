from __future__ import annotations

import re
from typing import Optional

from voice_gateway.models import ASRResult


def normalize_trigger_text(text: str) -> str:
    return re.sub(r"[\s，,。！？?：:；;、\"'‘’“”（）()\[\]【】]+", "", text.strip())


def contains_wake_word(text: str, wake_word: str) -> bool:
    normalized_text = normalize_trigger_text(text)
    normalized_wake_word = normalize_trigger_text(wake_word)
    return bool(normalized_wake_word) and normalized_wake_word in normalized_text


def extract_nihao_question(text: str) -> Optional[str]:
    normalized = normalize_trigger_text(text)
    if "你好" not in normalized:
        return None
    return normalized.rsplit("你好", 1)[1].strip(" ，,。？?：:")


def nihao_question_from_asr(asr: ASRResult) -> Optional[str]:
    return extract_nihao_question(asr.text or asr.normalized_text)


def xiaoai_question_from_asr(asr: ASRResult) -> Optional[str]:
    """Return the real user question after XiaoAI wake handling.

    In the real speaker path, `你好` is the wake word and may already be
    stripped before Mac-side ASR sees the utterance.  Accept the ASR text as the
    question, but if `你好` is still present, strip only the wake word and the
    delimiter right after it. Preserve punctuation inside the question because
    punctuation can carry meaning.
    """
    raw_text = (asr.text or asr.normalized_text or "").strip()
    if not raw_text:
        return None

    matches = list(re.finditer("你好", raw_text))
    if not matches:
        return raw_text

    question = raw_text[matches[-1].end() :].lstrip(" \t\r\n，,。！？?：:；;、\"'‘’“”（）()[]【】")
    return question.strip()
