import unittest

from automation.urls import parse_youtube_url, split_url_input


class YoutubeUrlParsingTests(unittest.TestCase):
    def test_normalizes_supported_url_shapes(self):
        video_id = "dQw4w9WgXcQ"
        cases = [
            f"https://youtu.be/{video_id}?si=tracking",
            f"https://www.youtube.com/watch?v={video_id}&list=ignored",
            f"https://youtube.com/shorts/{video_id}",
            f"https://www.youtube.com/embed/{video_id}",
            f"youtube.com/live/{video_id}",
        ]
        for value in cases:
            with self.subTest(value=value):
                parsed = parse_youtube_url(value)
                self.assertIsNone(parsed.error)
                self.assertEqual(video_id, parsed.video_id)
                self.assertEqual(f"https://www.youtube.com/watch?v={video_id}", parsed.normalized_url)

    def test_rejects_unsupported_and_malformed_urls(self):
        for value in ("https://example.com/watch?v=dQw4w9WgXcQ", "https://youtube.com/watch?v=short", "not-a-url"):
            with self.subTest(value=value):
                self.assertIsNotNone(parse_youtube_url(value).error)

    def test_splits_newline_comma_and_space_separated_input(self):
        self.assertEqual(
            ["https://youtu.be/aaaaaaaaaaa", "https://youtu.be/bbbbbbbbbbb", "https://youtu.be/ccccccccccc"],
            split_url_input("https://youtu.be/aaaaaaaaaaa,\nhttps://youtu.be/bbbbbbbbbbb https://youtu.be/ccccccccccc"),
        )


if __name__ == "__main__":
    unittest.main()

