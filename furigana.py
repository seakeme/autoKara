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

        # 逐字处理：假名在读音中按序一一匹配，汉字取中间的剩余读音
        reading_pos = 0
        kanji_buf = ""
        token_parts = []

        for ch in surface:
            if needs_furigana(ch):
                kanji_buf += ch
            else:
                if kanji_buf:
                    # 在剩余读音中找到当前假名的位置
                    kana_match = hira.find(ch, reading_pos)
                    if kana_match > reading_pos:
                        kanji_reading = hira[reading_pos:kana_match]
                        token_parts.append(f"{{{kanji_buf}|{kanji_reading}}}")
                    kanji_buf = ""
                    reading_pos = kana_match if kana_match >= 0 else reading_pos
                token_parts.append(ch)
                reading_pos += 1

        if kanji_buf:
            kanji_reading = hira[reading_pos:]
            token_parts.append(f"{{{kanji_buf}|{kanji_reading}}}")

        result.append(''.join(token_parts))

    return ''.join(result)

# ===================== 测试 =====================
if __name__ == '__main__':
    lyric = """
ひとり電車に　揺られて

お気に入りだった　海へ来ていた

肩寄せながら　波音

いつまでも　ふたりきいたよね

なみだ味の風は

私を切なくさせる

波の数ほど

思い出は溢れてくるけれど

あなたの笑顔

今はもう　思い出せない

傾いてゆく　太陽

暖かくすべて包み込んでく

目が覚めるような　オレンジ

この冬の　終わりが近づいてる

打ち寄せられた空き缶さえも

意味があるはず

言葉一つで

大切な人を傷つけてた

子供のような

恋はもうしたくないの

寄せては返す波のように

心強くなろう

なみだ味する風を今

思い切り吸い込んで帰ろう

波の数ほど

思い出は溢れてくるけれど

あなたの笑顔

今はもう　思い出せない

思い出せない


"""
    print(add_furigana(lyric))
