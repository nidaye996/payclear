"""
用工协议 PDF OCR 解析服务
- 第1页：识别姓名 + 身份证号
- 第2-5页：识别日工资金额 + 验证劳动报酬条款模板
"""
import re
import logging
from typing import Dict, Any

logger = logging.getLogger(__name__)

OCR_RESOLUTION = 150
MAX_CONTRACT_PAGES = 20
MAX_CONTRACT_PAGE_PIXELS = 5_000_000
MAX_CONTRACT_TOTAL_PIXELS = 60_000_000

# 劳动报酬条款必须包含的关键字（用于模板验证）
TEMPLATE_KEYWORDS = [
    "劳动报酬",
    "元/日",
    "按考勤天数计算月度工资",
    "次月20日前支付",
]


def _ocr_page(page, resolution: int = OCR_RESOLUTION) -> str:
    """对 pdfplumber 的一页做 OCR，返回识别文字"""
    try:
        import pytesseract
        from PIL import Image
        import io

        img_obj = page.to_image(resolution=resolution)
        # pdfplumber 的 PageImage 有 original 属性是 PIL Image
        pil_img = img_obj.original
        text = pytesseract.image_to_string(pil_img, lang='chi_sim')
        return text
    except Exception as e:
        logger.warning(f"OCR页面失败: {e}")
        return ""


def _extract_name(text: str) -> str:
    """从第1页文字中提取姓名"""
    patterns = [
        r'乙\s*方\s*[（(]?\s*劳\s*动\s*者\s*[）)]?\s*姓\s*名\s*[:：,，;；]\s*_*\s*([\u4e00-\u9fa5·• \t]{2,16})',
        r'姓\s*名\s*[:：,，;；]\s*_*\s*([\u4e00-\u9fa5·• \t]{2,16})',
    ]
    for pat in patterns:
        m = re.search(pat, text)
        if m:
            name = re.sub(r'\s+', '', m.group(1)).strip('_').strip()
            if re.fullmatch(r'[\u4e00-\u9fa5·•]{2,5}', name):
                return name
    return ''


def validate_contract_pdf_pages(pages: list[tuple[float, float]]) -> None:
    """校验合同 PDF 页数和按默认 OCR 分辨率估算的像素量。"""
    if not pages:
        raise ValueError("PDF无页面")
    if len(pages) > MAX_CONTRACT_PAGES:
        raise ValueError(f"PDF页数过多，最多允许 {MAX_CONTRACT_PAGES} 页")

    total_pixels = 0
    for page_number, (width_points, height_points) in enumerate(pages, start=1):
        page_pixels = int((width_points / 72 * OCR_RESOLUTION) * (height_points / 72 * OCR_RESOLUTION))
        if page_pixels > MAX_CONTRACT_PAGE_PIXELS:
            raise ValueError(
                f"第 {page_number} 页像素过大，单页最多允许 {MAX_CONTRACT_PAGE_PIXELS // 1_000_000} 百万像素"
            )
        total_pixels += page_pixels

    if total_pixels > MAX_CONTRACT_TOTAL_PIXELS:
        raise ValueError(f"PDF总像素过大，最多允许 {MAX_CONTRACT_TOTAL_PIXELS // 1_000_000} 百万像素")


def validate_contract_pdf(file_path: str) -> None:
    """在 OCR 前检查 PDF，避免异常大文件耗尽服务资源。"""
    import pdfplumber

    with pdfplumber.open(file_path) as pdf:
        validate_contract_pdf_pages([(page.width, page.height) for page in pdf.pages])


def _extract_id_card(text: str) -> str:
    """从第1页文字中提取身份证号（18位）"""
    # 先找标准格式
    patterns = [
        r'居\s*民\s*身\s*份\s*证\s*号\s*码\s*[:：]\s*([0-9Xx\s]{15,25})',
        r'身\s*份\s*证\s*号\s*[:：]\s*([0-9Xx\s]{15,25})',
        r'证\s*号\s*[:：]\s*([0-9Xx\s]{15,25})',
    ]
    for pat in patterns:
        m = re.search(pat, text)
        if m:
            raw = re.sub(r'\s', '', m.group(1)).upper()
            if len(raw) == 18:
                return raw
    # 兜底：直接找18位数字串
    m = re.search(r'\b([0-9]{17}[0-9X])\b', text.replace(' ', ''))
    if m:
        return m.group(1).upper()
    return ''


def _extract_daily_wage(text: str) -> float | None:
    """从合同正文中提取日工资金额"""
    patterns = [
        r'劳\s*动\s*报\s*酬\s*为\s*(\d+(?:\.\d+)?)\s*元\s*/\s*日',
        r'(\d+(?:\.\d+)?)\s*元\s*/\s*日',
        r'劳\s*动\s*报\s*酬\s*为\s*(\d+(?:\.\d+)?)',
    ]
    for pat in patterns:
        m = re.search(pat, text)
        if m:
            try:
                val = float(m.group(1))
                if 50 <= val <= 2000:  # 合理日工资范围
                    return val
            except ValueError:
                continue
    return None


def _check_template(text: str) -> tuple:
    """验证劳动报酬条款模板是否合规，返回 (合规bool, 缺失关键词列表)"""
    import re
    clean = re.sub(r'\s+', '', text)  # 去掉空格、换行等所有空白字符
    missing = [kw for kw in TEMPLATE_KEYWORDS if kw not in clean]
    return len(missing) <= 1, missing


def parse_contract_pdf(file_path: str) -> Dict[str, Any]:
    """
    解析用工协议 PDF。
    返回：{
        'name': str,
        'id_card': str,
        'daily_wage': float | None,
        'template_valid': bool,
        'error': str | None,
    }
    """
    result = {
        'name': '',
        'id_card': '',
        'daily_wage': None,
        'template_valid': False,
        'missing_keywords': [],
        'error': None,
    }

    try:
        import pdfplumber
    except ImportError:
        result['error'] = 'pdfplumber 未安装'
        return result

    try:
        with pdfplumber.open(file_path) as pdf:
            validate_contract_pdf_pages([(page.width, page.height) for page in pdf.pages])

            # 第1页：识别姓名和身份证号
            page1_text = _ocr_page(pdf.pages[0])
            result['name'] = _extract_name(page1_text)
            result['id_card'] = _extract_id_card(page1_text)

            # 第2-5页：找日工资 + 验模板
            body_text = ''
            for page in pdf.pages[1:5]:
                body_text += _ocr_page(page) + '\n'

            result['daily_wage'] = _extract_daily_wage(body_text)
            template_valid, missing_kws = _check_template(body_text)
            result['template_valid'] = template_valid
            result['missing_keywords'] = missing_kws

    except Exception as e:
        logger.error(f"解析合同PDF失败 {file_path}: {e}", exc_info=True)
        result['error'] = str(e)

    return result
