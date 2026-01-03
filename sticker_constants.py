"""
Matrix sticker constants.
"""

import re

SHORTCODE_PATTERN = re.compile(r":([a-zA-Z0-9_-]+):")

STICKER_PROMPT_TEMPLATE = """
## 可用的表情贴纸

你可以在回复中使用以下表情贴纸短码，格式为 :短码:，系统会自动将其替换为对应的贴纸图片。

可用短码列表：
{sticker_list}

使用示例：
- 表达思考时可以用 :thinking:
- 根据语境选择合适的表情来增强表达效果
- 短码区分大小写，请使用准确的短码
"""
