import unittest


class TestOpenCVImport(unittest.TestCase):
    def test_cv2_import(self):
        try:
            import cv2  # noqa: F401
        except Exception as e:
            self.fail(f"cv2 import failed: {type(e).__name__}: {str(e)[:200]}")


if __name__ == "__main__":
    unittest.main()

