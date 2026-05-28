import os
from sudachipy import Dictionary, SplitMode

_tokenizer = None

# Sudachi 分词粒度：C=最长单元(默认/原行为)，A=最小单元。
# A 切分更细、对 "东京の空" 这类过度合并更稳健；默认保持 C 以确保不回归。
SPLIT_MODE = SplitMode.C

# SudachiPy 常见读音修正（通常偏向口语/常用读法）
CORRECTIONS = {
    '私': 'わたし',
    '入り': 'いり',
}

def _load_user_corrections():
    """加载用户自定义读音覆盖 readings.txt（每行 “表层=读音”）。
    一次纠正、长期生效：以后同一个词不会再注错，避免重复人工返工。"""
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'readings.txt')
    if not os.path.exists(path):
        return
    try:
        with open(path, encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith('#') or '=' not in line:
                    continue
                key, val = line.split('=', 1)
                if key.strip() and val.strip():
                    CORRECTIONS[key.strip()] = val.strip()
    except Exception as e:
        print(f'读音覆盖文件加载失败(已忽略): {e}')

_load_user_corrections()

def _get_tokenizer():
    global _tokenizer
    if _tokenizer is None:
        # 优先用 full 词典（条目最全、读音错误最少）；缺失时回退到 core
        try:
            _tokenizer = Dictionary(dict='full').create()
        except Exception:
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

def _split_single_block(surface, hira):
    """单个汉字块(可带前/后送假名)时，用双向锚定切分：前送假名锚定读音开头、
    后送假名锚定读音结尾，汉字取中间。修正“歌う→う”这类尾送假名与读音首字
    同形导致贪心匹配错位的 bug。不符合单块结构则返回 None，回退逐字贪心。"""
    runs = []
    for ch in surface:
        is_k = not needs_furigana(ch)   # True=假名/其它，False=汉字
        if runs and runs[-1][0] == is_k:
            runs[-1][1] += ch
        else:
            runs.append([is_k, ch])
    kanji_idx = [i for i, (k, _) in enumerate(runs) if not k]
    if len(kanji_idx) != 1:
        return None
    ki = kanji_idx[0]
    lead = ''.join(t for k, t in runs[:ki])
    kanji = runs[ki][1]
    trail = ''.join(t for k, t in runs[ki + 1:])
    if not hira.startswith(lead) or not hira.endswith(trail):
        return None
    if len(lead) + len(trail) >= len(hira):
        return None
    kr = hira[len(lead): len(hira) - len(trail)]
    if not kr:
        return None
    return lead + "{" + kanji + "|" + kr + "}" + trail

def add_furigana(text):
    tokenizer_obj = _get_tokenizer()
    result = []

    for token in tokenizer_obj.tokenize(text, SPLIT_MODE):
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

        # 单汉字块优先用双向锚定切分（修正 歌う→う 之类的尾送假名错位）
        _be = _split_single_block(surface, hira)
        if _be is not None:
            result.append(_be)
            continue

        # 逐字处理：假名在读音中按序一一匹配，汉字取中间的剩余读音
        # （处理多汉字块/中间夹假名等复杂情形）
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
