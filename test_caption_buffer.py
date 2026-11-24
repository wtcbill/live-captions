import unittest

from caption_buffer import CaptionBuffer


def texts(pieces):
    return [piece.text for piece in pieces]


class CaptionBufferTests(unittest.TestCase):
    def test_commits_previous_line_when_new_line_starts(self):
        buffer = CaptionBuffer()
        buffer.prime("前の内容は表示済み\n別の内容も表示済み")
        previous = "前の内容は表示済み\n別の内容も表示済み\n明日の午後に連絡します"
        self.assertEqual(buffer.update(previous, 0), [])
        self.assertEqual(buffer.preview(0.3), "")
        self.assertEqual(buffer.preview(0.7), "明日の午後に連絡します")
        self.assertEqual(buffer.preview(0.8), "")
        self.assertEqual(
            texts(buffer.update(previous + "\r\n場所は", 1.0)),
            ["明日の午後に連絡します"],
        )
        self.assertEqual(buffer.pending, "場所は")

    def test_can_preview_uninterrupted_speech_and_commit_last_phrase_at_pause(self):
        buffer = CaptionBuffer()
        phrase = "この会場ではチェックインの前に"
        buffer.update(phrase[:8], 0)
        buffer.update(phrase[:11], 0.5)
        buffer.update(phrase[:14], 1.1)
        buffer.update(phrase, 1.49)
        self.assertEqual(buffer.preview(1.51), phrase)
        self.assertEqual(buffer.preview(1.7), "")
        self.assertIsNone(buffer.finish_on_pause(2.8))
        self.assertEqual(buffer.finish_on_pause(2.9).text, phrase)
        self.assertIsNone(buffer.finish_on_pause(3.0))

    def test_can_read_completed_sentences_between_preview_updates(self):
        buffer = CaptionBuffer()
        text = "受付は９時からです。開始は１０時です。"
        self.assertEqual(
            texts(buffer.update(text, 1)),
            ["受付は９時からです。", "開始は１０時です。"],
        )
        self.assertEqual(buffer.update(text, 2), [])
        self.assertEqual(
            texts(buffer.update(text + "会場は２階です。", 3)), ["会場は２階です。"]
        )

    def test_does_not_repeat_a_sentence_when_windows_scrolls_old_text_out(self):
        buffer = CaptionBuffer()
        self.assertEqual(
            texts(buffer.update("資料はこちらです。会場は２階です。", 1)),
            ["資料はこちらです。", "会場は２階です。"],
        )
        self.assertEqual(
            texts(buffer.update("会場は２階です。受付は９時です。", 2)),
            ["受付は９時です。"],
        )

    def test_aligns_rolling_lines_while_the_active_line_is_corrected(self):
        buffer = CaptionBuffer()
        old = "すでに表示した行\n会場は２階\n明日は午後に連絡"
        buffer.prime(old)
        self.assertEqual(
            texts(buffer.update("会場は２階\n明日は午前に連絡します\n受付は", 1)),
            ["します"],
        )
        self.assertEqual(buffer.pending, "受付は")

    def test_retranslates_the_entire_sentence_when_windows_revises_a_negation(self):
        buffer = CaptionBuffer()
        first = "会場は２階です。"
        pieces = buffer.update(first + "会場では撮影できます。", 1)
        self.assertEqual(
            texts(buffer.update(first + "会場では撮影できません。", 2)),
            ["会場では撮影できません。"],
        )
        self.assertEqual(buffer.invalidated, [pieces[1]])


if __name__ == "__main__":
    unittest.main()
