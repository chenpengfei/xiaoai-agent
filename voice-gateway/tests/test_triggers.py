import unittest

from voice_gateway.dialogue.triggers import contains_wake_word, extract_nihao_question, xiaoai_question_from_asr
from voice_gateway.models import ASRResult


def asr(text: str) -> ASRResult:
    return ASRResult(text=text, normalized_text=text, is_final=True, engine="test")


class TriggerTest(unittest.TestCase):
    def test_extracts_question_after_last_nihao(self):
        self.assertEqual(extract_nihao_question("小爱同学 你好 你是谁"), "你是谁")

    def test_contains_wake_word_ignores_punctuation(self):
        self.assertTrue(contains_wake_word("你，好", "你好"))

    def test_returns_none_without_trigger(self):
        self.assertIsNone(extract_nihao_question("今天天气怎么样"))

    def test_xiaoai_question_accepts_wake_word_stripped_question(self):
        self.assertEqual(xiaoai_question_from_asr(asr("你是谁")), "你是谁")

    def test_xiaoai_question_strips_wake_word_if_asr_includes_it(self):
        self.assertEqual(xiaoai_question_from_asr(asr("你好，你是谁")), "你是谁")

    def test_xiaoai_question_preserves_question_punctuation(self):
        self.assertEqual(xiaoai_question_from_asr(asr("你好，今天 2 + 2 = 几？")), "今天 2 + 2 = 几？")

    def test_xiaoai_question_preserves_punctuation_without_wake_word(self):
        self.assertEqual(xiaoai_question_from_asr(asr("今天 2 + 2 = 几？")), "今天 2 + 2 = 几？")


if __name__ == "__main__":
    unittest.main()
