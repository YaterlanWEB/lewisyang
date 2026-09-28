import unittest

from render_engine import _estimated_output_bytes, _format_bytes, _is_no_space_error


class RenderDiskSpaceTests(unittest.TestCase):
    def test_estimate_grows_with_duration_and_resolution(self):
        short = _estimated_output_bytes(1080, 1440, 30, 60)
        long = _estimated_output_bytes(1080, 1440, 30, 180)
        larger = _estimated_output_bytes(2160, 2880, 30, 180)
        self.assertGreater(long, short)
        self.assertGreater(larger, long)

    def test_formats_sizes_for_user_messages(self):
        self.assertEqual(_format_bytes(0), "0 B")
        self.assertEqual(_format_bytes(1024**3), "1.00 GB")

    def test_detects_ffmpeg_disk_full_errors(self):
        self.assertTrue(_is_no_space_error("av_interleaved_write_frame(): No space left on device"))
        self.assertTrue(_is_no_space_error("There is not enough space on the disk"))
        self.assertFalse(_is_no_space_error("Unknown encoder 'libx264'"))


if __name__ == "__main__":
    unittest.main()
