import os
import sys
import unittest


sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from services.ocr import _extract_name, validate_contract_pdf_pages  # noqa: E402


class ContractOcrTestCase(unittest.TestCase):
    def test_extract_name_accepts_spaces_between_ocr_characters(self):
        text = "乙 方 ( 劳 动 者 ) 姓 名 : 蔡 玉 泉\n居民身份证号码：371328198305240018"

        self.assertEqual(_extract_name(text), "蔡玉泉")

    def test_extract_name_accepts_missing_parenthesis_and_semicolon(self):
        text = "乙 方 ( 劳 动 者 姓 名 ; 蔡 玉 泉\n性 别 ; 男"

        self.assertEqual(_extract_name(text), "蔡玉泉")

    def test_rejects_contract_with_more_than_twenty_pages(self):
        pages = [(595, 842)] * 21

        with self.assertRaisesRegex(ValueError, "页数"):
            validate_contract_pdf_pages(pages)

    def test_rejects_contract_when_total_pixels_are_too_large(self):
        pages = [(960, 960)] * 16

        with self.assertRaisesRegex(ValueError, "总像素"):
            validate_contract_pdf_pages(pages)


if __name__ == "__main__":
    unittest.main()
