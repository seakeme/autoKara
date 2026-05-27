from sudachipy import Dictionary

_tokenizer = None

# SudachiPy 常见读音修正（通常偏向口语/常用读法）
CORRECTIONS = {
    '私': 'わたし',
    '入り': 'いり',
}

def _get_tokenizer():
    global _tokenizer
    if _tokenizer is None:
        _tokenizer = Dictionary().create()
    return _tokenizer

# 片假名转平假名
def katakana_to_hiragana(text):
    return ''.join(chr(ord(c) - 0x60) if 'ァ' <= c <= 'ヶ' else c for c in text)

# 判断是否为汉字
def is_kanji(ch):
    return '一' <= ch <= '鿿'

def is_katakana(ch):
    return 'ァ' <= ch <= 'ヶ' or ch == 'ー'

def needs_furigana(ch):
    return is_kanji(ch) or is_katakana(ch)

def add_furigana(text):
    tokenizer_obj = _get_tokenizer()
    result = []

    for token in tokenizer_obj.tokenize(text):
        surface = token.surface()
        reading = token.reading_form()
        if surface in CORRECTIONS:
            hira = CORRECTIONS[surface]
        else:
            hira = katakana_to_hiragana(reading)

        # Sudachi 对片假名词汇返回 reading==surface，需手动注音
        if reading == surface:
            if any(is_katakana(c) for c in surface):
                hira = katakana_to_hiragana(surface)
                result.append(f"{{{surface}|{hira}}}")
            else:
                result.append(surface)
            continue

        # 纯假名直接保留
        if surface == hira:
            result.append(surface)
            continue

        # 遍历所有汉字/片假名块，每块分别注解
        pos = 0
        token_parts = []
        while pos < len(surface):
            if needs_furigana(surface[pos]):
                block_start = pos
                while pos < len(surface) and needs_furigana(surface[pos]):
                    pos += 1
                lead = surface[block_start:pos]

                rest_start = pos
                while pos < len(surface) and not needs_furigana(surface[pos]):
                    pos += 1
                rest = surface[rest_start:pos]

                if not rest and pos == len(surface):
                    lead_hira = hira[block_start:]
                else:
                    lead_hira = hira[block_start:block_start + len(lead)]

                token_parts.append(f"{{{lead}|{lead_hira}}}{rest}")
            else:
                token_parts.append(surface[pos])
                pos += 1

        result.append(''.join(token_parts))

    return ''.join(result)

# ===================== 测试 =====================
if __name__ == '__main__':
    lyric = """
こうして夏の終わりは
夢の世に淡く儚く色づき 消える
"""
    print(add_furigana(lyric))
