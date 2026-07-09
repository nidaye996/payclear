import os
import sys
import unittest


sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from services.ocr import _extract_name, parse_contract_pages, validate_contract_pdf_pages  # noqa: E402


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

    def test_retries_contract_body_at_higher_resolution_when_template_is_missing(self):
        calls = []

        def fake_ocr(page, resolution):
            calls.append((page, resolution))
            if page == "first":
                return "乙 方 ( 劳 动 者 姓 名 ; 蔡 玉 泉\n居民身份证号码：371328198305240018"
            if resolution == 150:
                return "劳动报酬为240元/日"
            return "劳动报酬为240元/日\n按考勤天数计算月度工资\n次月20日前支付"

        result = parse_contract_pages(["first", "body"], fake_ocr)

        self.assertTrue(result["template_valid"])
        self.assertEqual(result["daily_wage"], 240.0)
        self.assertIn(("body", 200), calls)


if __name__ == "__main__":
    unittest.main()
