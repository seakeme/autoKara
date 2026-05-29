"""
auto-Kara 卡拉OK字幕生成器
用法:
    python main.py                       # 自动检测 input/ 中的音频+歌词
    python main.py --raw                 # 强制重新自动注音
    python main.py -i <input_dir> -o <output_dir>
输入: 音频文件 + .txt 歌词 → 输出: 带时间轴的 .ass 卡拉OK字幕
"""

# ============================================================
# Imports
# ============================================================
import argparse
import bisect
import glob
import math
import os
import re
import sys
import tempfile
import time
import unicodedata
import warnings

import librosa
import numpy as np
import torch
import torchaudio
from janome.tokenizer import Tokenizer
import nltk
from nltk.corpus import cmudict
import pykakasi
import pyphen

import separate
from furigana import add_furigana

warnings.filterwarnings('ignore', message='torchaudio.functional._alignment.forced_align has been deprecated')

# ============================================================
# Global init — 日语处理
# ============================================================
kks = pykakasi.kakasi()
tokenizer = Tokenizer()
tail_pron = ''

phoneme_map = {
    'AA': 'a', 'AE': 'a', 'AH': 'a', 'AO': 'o', 'AW': 'au', 'AY': 'ai',
    'B': 'b', 'CH': 'ch', 'D': 'd', 'DH': 'z', 'EH': 'e', 'ER': 'a',
    'EY': 'ei', 'F': 'f', 'G': 'g', 'HH': 'h', 'IH': 'i', 'IY': 'i',
    'JH': 'j', 'K': 'k', 'L': 'r', 'M': 'm', 'N': 'n', 'NG': 'ng',
    'OW': 'o', 'OY': 'oi', 'P': 'p', 'R': 'r', 'S': 's', 'SH': 'sh',
    'T': 't', 'TH': 's', 'UH': 'u', 'UW': 'u', 'V': 'v', 'W': 'w',
    'Y': 'y', 'Z': 'z', 'ZH': 'j'
}

# CMUdict（英文发音词典）惰性 + 容错加载：仅当歌词里出现英文单词时才需要。
# 缺失 / 下载失败都不致命 —— 退化为"按拼写近似注音"（process_english_word 已有该分支），
# 纯日文歌完全不受影响。绝不能像以前那样在 import 时硬加载、一缺就让整个程序崩。
cmu_dict = None   # None=尚未尝试加载；dict=已加载（{} 表示不可用）

def _get_cmu_dict():
    global cmu_dict
    if cmu_dict is not None:
        return cmu_dict
    try:
        cmu_dict = cmudict.dict()
    except Exception:
        try:
            nltk.download('cmudict')
            cmu_dict = cmudict.dict()
        except Exception as e:
            print(f"提示：英文发音词典(cmudict)不可用，含英文的歌词将按拼写近似注音。({e})")
            cmu_dict = {}
    return cmu_dict

eng_dic = pyphen.Pyphen(lang='en_US')

newnums = ['①', '②', '③', '④', '⑤', '⑥', '⑦', '⑧', '⑨', '⑩',
           '⑪', '⑫', '⑬', '⑭', '⑮', '⑯', '⑰', '⑱', '⑲', '⑳',
           '㉑', '㉒', '㉓', '㉔', '㉕', '㉖', '㉗', '㉘', '㉙', '㉚']

# ============================================================
# 工具函数
# ============================================================

def parse_time_to_hundredths(time_str):
    """把 '[MM:SS:CC]' 解析成厘秒整数；任何非法输入（含 '[error]' 或 None）返回 None
    而不是抛异常——下游 V2/V3 已能容忍 None 时间戳。"""
    if not isinstance(time_str, str):
        return None
    match = re.match(r'\[(\d{2}):(\d{2}):(\d{2})\]', time_str)
    if match is None:
        return None
    minutes, seconds, hundredths = int(match.group(1)), int(match.group(2)), int(match.group(3))
    return minutes * 6000 + seconds * 100 + hundredths

def format_hundredths_to_time_str(total_hundredths):
    minutes = total_hundredths // 6000
    remaining = total_hundredths % 6000
    seconds = remaining // 100
    hundredths = remaining % 100
    return f"[{minutes:02d}:{seconds:02d}:{hundredths:02d}]"

def calculate_length(surface):
    length = 0.0
    for char in surface:
        if unicodedata.east_asian_width(char) in ('F', 'W', 'A'):
            length += 1
        else:
            length += 0.5
    return length

def non_silent_recog(audio_file, sr=None, frame_second=1, threspct=10, thresrto=.1):
    frame_length = int(sr * frame_second)
    hop_length = frame_length // 2
    energy = librosa.feature.rms(y=audio_file, frame_length=frame_length, hop_length=hop_length)[0]
    threshold = np.percentile(energy, 100-threspct) * thresrto
    non_silent_frames = energy > threshold
    times = librosa.frames_to_time(np.arange(len(energy)), sr=sr, hop_length=hop_length)
    segments = []
    start = None
    for i, (t, active) in enumerate(zip(times, non_silent_frames)):
        if active and start is None:
            start = max(t-frame_second/4, 0)
        elif not active and start is not None:
            segments.append((start, t+frame_second/4))
            start = None
    if start is not None:
        segments.append((start, times[-1]))
    return segments

def non_silent_head_adjust(result_list, non_silent_ranges):
    if not non_silent_ranges:
        return result_list
    i = si = 0
    sentences_list = []
    st = None
    while i < len(result_list):
        if result_list[i].get('type') == 0:
            if st:
                sentences_list.append((si, i-1, st, result_list[i-1].get('end')))
                st = None
        elif not st:
            si = i
            st = result_list[i].get('start')
        i += 1
    for inds, inde, sst, sen in sentences_list:
        sst = parse_time_to_hundredths(sst)
        sen = parse_time_to_hundredths(sen)
        interval_covered = False
        for ns_start, ns_end in non_silent_ranges:
            if int(ns_start * 100) > sst:
                break
            if int(ns_start * 100) <= sst and int(np.ceil(ns_end * 100)) >= sen:
                interval_covered = True
                break
        if not interval_covered:
            end_covered = False
            for j in range(len(non_silent_ranges)):
                ns_start = int(non_silent_ranges[j][0] * 100)
                ns_end = int(np.ceil(non_silent_ranges[j][1] * 100))
                if ns_start > sen:
                    break
                if ns_start <= sen:
                    if ns_end >= sen:
                        end_covered = True
                        adjust_target = ns_start
                        break
                elif ns_start <= parse_time_to_hundredths(result_list[inde].get('start')) <= ns_end:
                    end_covered = True
                    adjust_target = ns_start
                    break
            if not end_covered:
                print('Errors ignored while trying to correct end sounds...')
                break
            else:
                adjust_target = min(parse_time_to_hundredths(result_list[inds]['end']), adjust_target)
                result_list[inds]['start'] = format_hundredths_to_time_str(adjust_target)
    return result_list

def split_long_segments(elements, max_length=20):
    current_length = 0.0
    space_positions = []
    i = 0
    while i <= len(elements):
        if i == len(elements) or elements[i].get('type') == 0 and elements[i].get('orig') == '\n':
            if current_length > max_length and space_positions:
                n_cuts = current_length // max_length + 1
                n_cut_length = current_length / n_cuts
                sorted_spaces = sorted(space_positions, key=lambda x: (
                    0 if x[1] <= max_length else 1,
                    abs(x[1] - n_cut_length) if x[1] <= max_length else -x[1]
                ))
                best_position = sorted_spaces[0][0]
                elements[best_position]['orig'] = '\n'
                i = best_position
                current_length = 0.0
                space_positions = []
            else:
                current_length = 0.0
                space_positions = []
        elif i < len(elements):
            elem = elements[i]
            surface = elem.get('orig')
            elem_length = calculate_length(surface)
            if surface in (' ', '　') and elem.get('type') == 0:
                space_positions.append((i, current_length))
            current_length += elem_length
        i += 1

# ============================================================
# 歌词文本 → token
# ============================================================

def number_to_english(number_str):
    try:
        if '.' in number_str:
            num = float(number_str)
        else:
            num = int(number_str)
    except ValueError:
        print('Unable to process number "'+number_str+'"...')
        return tail_pron

    ones = ["zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine",
            "ten", "eleven", "twelve", "thirteen", "fourteen", "fifteen", "sixteen",
            "seventeen", "eighteen", "nineteen"]
    tens = ["", "", "twenty", "thirty", "forty", "fifty", "sixty", "seventy", "eighty", "ninety"]

    if isinstance(num, float):
        integer_part = int(num)
        decimal_part = round(num - integer_part, 3)
        integer_words = number_to_english(str(integer_part)) if integer_part > 0 else ""
        decimal_str = f"{decimal_part:.3f}"[2:]
        decimal_words = " point"
        for digit in decimal_str:
            if digit == '0' and not decimal_words.endswith(' zero'):
                decimal_words += " zero"
            elif digit != '0':
                decimal_words += " " + ones[int(digit)]
        return (integer_words + decimal_words).strip()

    if num < 0:
        return "minus " + number_to_english(str(abs(num)))
    if num < 20:
        return ones[num]
    if num < 100:
        return tens[num // 10] + ((" " + ones[num % 10]) if num % 10 != 0 else "")
    if num < 1000:
        return ones[num // 100] + " hundred" + (" and " + number_to_english(str(num % 100)) if num % 100 != 0 else "")

    scales = [
        (10**12, "trillion"),
        (10**9, "billion"),
        (10**6, "million"),
        (10**3, "thousand")
    ]
    for scale_value, scale_name in scales:
        if num >= scale_value:
            return number_to_english(str(num // scale_value)) + " " + scale_name + (" " + number_to_english(str(num % scale_value)) if num % scale_value != 0 else "")

    print('Unable to process number "'+number_str+'"...')
    return tail_pron

def is_english(text):
    return bool(re.match(r'^[a-zA-Z]+$', text))

def is_english_punctuation(char):
    return char == "'"

def is_hiragana(char):
    return '぀' <= char <= 'ゟ'

def is_katakana(char):
    return '゠' <= char <= 'ヿ'

def is_kana(char):
    for i in char:
        if not is_hiragana(i) and not is_katakana(i) or i in ['・', '゠']:
            return False
    return True

def is_number(char):
    if char in newnums:
        return False
    return char.isdigit()

def get_norm_ruby(item):
    if item['type'] == 2:
        return item['ruby']
    if item['type'] == 3:
        return item['orig'].lower() if is_english(item['orig']) else item['orig']
    if item['type'] == 1:
        return ''.join([char for char in item['orig'].strip() if not is_english_punctuation(char)]).lower()
    return tail_pron

def get_norm_surface(item):
    if item['type'] in (1, 2, 3, 4):
        return item['orig']
    return ''

def min_error_split(target_list, s):
    n = len(s)
    m = len(target_list)
    dp = [[float('inf')] * (m + 1) for _ in range(n + 1)]
    backtrack = [[None] * (m + 1) for _ in range(n + 1)]
    dp[0][0] = 0

    for i in range(n + 1):
        for k in range(m + 1):
            if dp[i][k] == float('inf'):
                continue
            if k < m:
                target = target_list[k]
                if target == "":
                    if dp[i][k] < dp[i][k + 1]:
                        dp[i][k + 1] = dp[i][k]
                        backtrack[i][k + 1] = (i, k, "")
                else:
                    for j in range(i + 1, n + 1):
                        segment = s[i:j]
                        if segment == target:
                            cost = 0
                        elif target == tail_pron:
                            cost = min(len(segment)*0.1, 1)
                        elif segment == 'wa' and target == 'ha' or segment == 'e' and target == 'ha':
                            cost = 0.1
                        else:
                            cost = 1
                        new_cost = dp[i][k] + cost
                        if new_cost < dp[j][k + 1]:
                            dp[j][k + 1] = new_cost
                            backtrack[j][k + 1] = (i, k, segment)

    if dp[n][m] == float('inf'):
        return None

    result = []
    i, k = n, m
    while k > 0:
        prev_i, prev_k, segment = backtrack[i][k]
        result.append(segment)
        i, k = prev_i, prev_k
    return result[::-1]

def sylla_split(kana_str, sokuon_split=False, hatsuon_split=True):
    kana_list = []
    i = 0
    n = len(kana_str)
    while i < n:
        current_char = kana_str[i]
        small_kana = ['ゃ', 'ゅ', 'ょ', 'ぁ', 'ぃ', 'ぅ', 'ぇ', 'ぉ', 'ー',
                      'ャ', 'ュ', 'ョ', 'ァ', 'ィ', 'ゥ', 'ェ', 'ォ']
        if not sokuon_split:
            small_kana += ['っ', 'ッ']
        if not hatsuon_split:
            small_kana += ['ん', 'ン']
        if current_char in small_kana:
            if i > 0:
                kana_list[-1] += current_char
            else:
                kana_list.append(current_char)
            i += 1
        else:
            kana_list.append(current_char)
            i += 1
    return kana_list

def convert_phoneme(ph):
    base_ph = ph.rstrip('012')
    return phoneme_map.get(base_ph, '')

def split_into_syllables_en(phonemes):
    vowels = ['AA', 'AE', 'AH', 'AO', 'AW', 'AY', 'EH', 'ER', 'EY',
              'IH', 'IY', 'OW', 'OY', 'UH', 'UW']
    vowel_positions = []
    for i, ph in enumerate(phonemes):
        base_ph = ph.rstrip('012')
        if base_ph in vowels:
            vowel_positions.append(i)
    if not vowel_positions:
        return [phonemes]

    syllables = []
    prev_vowel_idx = -1
    for i, vowel_idx in enumerate(vowel_positions):
        if i == 0:
            onset = phonemes[:vowel_idx]
            vowel = [phonemes[vowel_idx]]
            syllables.append(onset + vowel)
            prev_vowel_idx = vowel_idx
        else:
            consonants = phonemes[prev_vowel_idx + 1:vowel_idx]
            if consonants:
                onset_start = 0
                if len(consonants) > 1:
                    syllables[-1].append(consonants[0])
                    onset_start = 1
                onset = consonants[onset_start:]
                vowel = [phonemes[vowel_idx]]
                syllables.append(onset + vowel)
            else:
                syllables.append([phonemes[vowel_idx]])
            prev_vowel_idx = vowel_idx

    if prev_vowel_idx < len(phonemes) - 1:
        trailing = phonemes[prev_vowel_idx + 1:]
        syllables[-1].extend(trailing)
    return syllables

def align_syllables_en(a, b):
    if len(a) > len(b):
        long_list, short_list = a, b
        long_to_short = True
    elif len(b) > len(a):
        long_list, short_list = b, a
        long_to_short = False
    else:
        return list(zip(a, b))

    print("Ignored errors when dealing with the pronunciation of '"+''.join(a)+"'...")
    n_segments = len(short_list)
    total_elements = len(long_list)
    base_size = total_elements // n_segments
    extra = total_elements % n_segments

    merged_list = []
    start = 0
    for i in range(n_segments):
        seg_size = base_size + (1 if i >= n_segments-extra else 0)
        segment = long_list[start:start+seg_size]
        merged = ''.join(segment)
        merged_list.append(merged)
        start += seg_size

    if long_to_short:
        return list(zip(merged_list, short_list))
    else:
        return list(zip(short_list, merged_list))

def process_english_word(word, surf=True):
    if word == 'a':
        return [('a', 'a')]
    elif word == 'A':
        return [('A', 'ei')]

    hyphenated = eng_dic.inserted(word)
    surface_syllables = hyphenated.split('-')

    word_lower = word.lower()
    _cmu = _get_cmu_dict()
    if word_lower not in _cmu:
        print("Word '"+word+"' not in the dictionary...")
        direct_syllables = [i.replace("'", '').lower() for i in surface_syllables]
        return list(zip(surface_syllables, direct_syllables))

    phonemes = _cmu[word_lower][0]
    syllables_phonemes = split_into_syllables_en(phonemes)
    syllables_romaji = []
    for syl in syllables_phonemes:
        romaji = ''.join(convert_phoneme(p) for p in syl)
        syllables_romaji.append(romaji)

    if not surf:
        return ''.join(syllables_romaji)

    return align_syllables_en(surface_syllables, syllables_romaji)

def process_haruhi_line(line, lang='jaen', sokuon_split=False, hatsuon_split=True):
    tokens = re.split(r'(\{.*?\})', line)
    result = []

    for token in tokens:
        if not token:
            continue
        if token.startswith('{') and token.endswith('}'):
            content = token[1:-1]
            parts = content.split('|')
            # 单个注音格式错误（如 {歌} 或 {a|b|c}）不再 assert 崩溃整曲，记录后跳过这个 token
            if len(parts) != 2:
                print(f'注音格式错误（已跳过此 token）: {token!r}')
                continue
            kanji, ruby_text = parts
            ruby_text = sylla_split(ruby_text, sokuon_split, hatsuon_split)
            if len(ruby_text) < 1:
                print(f'振假名为空（已跳过此 token）: {token!r}')
                continue
            result.append({'orig': kanji, 'type': 2, 'ruby': ruby_text[0]})
            if len(ruby_text) >= 2:
                for i in range(1, len(ruby_text)):
                    result.append({'orig': '', 'type': 2, 'ruby': ruby_text[i]})
        else:
            token = sylla_split(token, sokuon_split, hatsuon_split)
            if lang == 'ja':
                for char in token:
                    if is_kana(char) or is_english(char):
                        result.append({'orig': char, 'type': 3})
                    else:
                        result.append({'orig': char, 'type': 0})
            elif lang == 'jaen':
                for char in token:
                    if is_kana(char):
                        result.append({'orig': char, 'type': 3})
                    elif is_english(char) or is_english_punctuation(char):
                        if result and result[-1].get('type') == 1:
                            result[-1]['orig'] += char
                        elif is_english(char):
                            result.append({'orig': char, 'type': 1})
                        else:
                            result.append({'orig': char, 'type': 0})
                    elif is_number(char):
                        if result and result[-1].get('type') == 4:
                            result[-1]['orig'] += char
                        else:
                            result.append({'orig': char, 'type': 4})
                    else:
                        result.append({'orig': char, 'type': 0})

    # 英语分音节注音、数字注音
    new_list = []
    for item in result:
        if item.get('type') == 1:
            new_elements = get_norm_surface(item)
            new_list.extend([{'orig': char, 'type': 1, 'pron': pron} for char, pron in process_english_word(new_elements)])
        elif item.get('type') == 4:
            en_nums = number_to_english(get_norm_surface(item)).split(' ')
            new_list.append({'orig': get_norm_surface(item), 'type': 4, 'pron': ''.join([process_english_word(i, surf=False) for i in en_nums])})
        else:
            new_list.append(item)
    result = new_list

    # 标注单字罗马音
    postpron = None
    for i in range(len(result)-1, -1, -1):
        if result[i].get('type') in (0, 2, 3):
            ruby_now = get_norm_ruby(result[i])
            if result[i].get('type') != 0 and ruby_now and ruby_now[-1] in ('っ', 'ッ'):
                try:
                    pron = postpron[0]
                except:
                    pron = tail_pron
                else:
                    if pron == 'c':
                        pron = 't'
                finally:
                    pron = kks.convert(ruby_now[:-1])[0]['hepburn'] + pron
            else:
                pron = kks.convert(ruby_now)[0]['hepburn']
            result[i]['pron'] = pron
        postpron = result[i]['pron']

    # 通用读音修正（は→wa，へ→e）
    line_pron_list = [item['pron'] for item in result]
    line_surface = ''.join([get_norm_surface(i) for i in result])
    line_roma = ''.join([i['hepburn'] for i in kks.convert(''.join([token.phonetic for token in tokenizer.tokenize(line_surface)]))])
    line_roma_proc = min_error_split(line_pron_list, line_roma)
    for i in range(len(result)):
        if result[i]['type'] == 3:
            try:
                if result[i]['orig'] == 'は' and line_roma_proc[i] == 'wa':
                    result[i]['pron'] = 'wa'
                elif result[i]['orig'] == 'へ' and line_roma_proc[i] == 'e':
                    result[i]['pron'] = 'e'
                elif result[i]['orig'] == 'を' and line_roma_proc[i] == 'o':
                    result[i]['pron'] = 'o'
            except:
                print('Ignored errors when trying to correct ha and he...')

    return result

# ============================================================
# token → ASS 字幕
# ============================================================

def int2asstime(cs: int) -> str:
    hours = cs // 360000
    cs %= 360000
    minutes = cs // 6000
    cs %= 6000
    seconds = cs // 100
    cs %= 100
    return f"{hours}:{minutes:02d}:{seconds:02d}.{cs:02d}"

def ass_time_to_cs(time_str):
    m = re.match(r'(\d+):(\d{2}):(\d{2})\.(\d{2})', time_str)
    h, mi, s, cs = int(m.group(1)), int(m.group(2)), int(m.group(3)), int(m.group(4))
    return h * 360000 + mi * 6000 + s * 100 + cs

def process_norm2assV1(struc, pretime=20, posttime=20):
    result = ''
    for i in range(len(struc)):
        if not result or result[-1] == '\n':
            asstxt = ''
            nowtime = starttime = parse_time_to_hundredths([itemd for itemd in struc[i:] if itemd['type'] != 0][0]['start']) - pretime
        item = struc[i]
        if item['type'] == 0 and item['orig'] == '\n':
            try:
                nowtime = parse_time_to_hundredths(item['start'])
            except:
                pass
            finally:
                endtime = nowtime + posttime
                asstxt = 'Dialogue: 0,'+int2asstime(starttime)+','+int2asstime(endtime)+r',Default,,0,0,0,karaoke,'+asstxt+r'{\k'+str(posttime)+'}'
                result += asstxt+'\n'
        elif 'start' not in item:
            asstxt += item['orig']
        else:
            item_kbefore = parse_time_to_hundredths(item['start']) - nowtime
            if item_kbefore != 0:
                asstxt += r'{\k'+str(item_kbefore)+'}'
            item_kdur = parse_time_to_hundredths(item['end']) - parse_time_to_hundredths(item['start'])
            asstxt += r'{\k'+str(item_kdur)+'}'
            if item['type'] == 2:
                asstxt += ('#|' if item['orig'] == '' else item['orig'] + '|<') + item['ruby']
            else:
                asstxt += item['orig']
            nowtime = parse_time_to_hundredths(item['end'])
    return result

def process_norm2assV2(struc, pretime=20, posttime=20):
    result = ''
    starttime = nowtime = None
    asstxt = ''
    i = 0
    while i < len(struc):
        item = struc[i]
        if not starttime:
            try:
                starttime = parse_time_to_hundredths(item['start']) - pretime
                nowtime = parse_time_to_hundredths(item['start'])
            except:
                asstxt += item['orig']
                i += 1
                continue
        if item['type'] == 0 and item['orig'] == '\n':
            try:
                nowtime = parse_time_to_hundredths(item['start'])
            except:
                pass
            finally:
                endtime = nowtime + posttime
                if asstxt:
                    if asstxt[0] not in ['{'] + newnums:
                        asstxt = r'{\k0}' + asstxt
                    if asstxt[0] in newnums and asstxt[1] != '{':
                        asstxt = asstxt[0] + r'{\k0}' + asstxt[1:]
                result += 'Dialogue: 0,'+int2asstime(starttime)+','+int2asstime(endtime)+r',Default,,0,0,0,karaoke,' \
                    + r'{\k'+str(pretime)+'}'+asstxt+r'{\k'+str(posttime)+'}'+'\n'
                starttime = nowtime = None
                asstxt = ''
        elif item['type'] == 0 and 'start' not in item:
            if struc[i-1].get('type') in (1, 3, 4) and item.get('orig') not in [' ', '　']+newnums:
                asstxt += item['orig']
            else:
                zero_str = item.get('orig')
                while True:
                    if struc[i+1].get('orig') == '\n':
                        item_kdur = 0
                        break
                    if struc[i+1].get('start'):
                        if nowtime:
                            item_kdur = parse_time_to_hundredths(struc[i+1]['start']) - nowtime
                        else:
                            item_kdur = 0
                        nowtime = parse_time_to_hundredths(struc[i+1]['start'])
                        break
                    zero_str += struc[i+1].get('orig')
                    i += 1
                asstxt += r'{\k'+str(max(item_kdur, 0))+'}' + zero_str
        else:
            # 时间戳缺失/非法时安全降级：只写文字、不出 \k，避免整曲 ASS 因单个 [error] token 崩溃
            try:
                if struc[i+1].get('start'):
                    item_kdur = parse_time_to_hundredths(struc[i+1]['start']) - parse_time_to_hundredths(item['start'])
                    nowtime = parse_time_to_hundredths(struc[i+1]['start'])
                else:
                    item_kdur = parse_time_to_hundredths(item['end']) - parse_time_to_hundredths(item['start'])
                    nowtime = parse_time_to_hundredths(item['end'])
                if item_kdur is None:
                    raise TypeError('parse_time returned None')
                if item_kdur < 0:
                    item_kdur = 0
                asstxt += r'{\k'+str(item_kdur)+'}'
                if item['type'] == 2:
                    asstxt += ('#|' if item['orig'] == '' else item['orig'] + '|<') + item['ruby']
                else:
                    asstxt += item['orig']
            except (TypeError, KeyError, AttributeError):
                if item['type'] == 2:
                    asstxt += ('#|' if item['orig'] == '' else item['orig'] + '|<') + item['ruby']
                else:
                    asstxt += item['orig']
        i += 1
    return result

def process_norm2assV3(struc, pretime=20, posttime=20, lead_gap=200):
    v2_output = process_norm2assV2(struc, pretime, posttime)
    lines = []
    for line in v2_output.strip().split('\n'):
        m = re.match(r'Dialogue: \d+,([^,]+),([^,]+),Default,,0,0,0,karaoke,(.+)', line)
        if m:
            lines.append({
                'start': ass_time_to_cs(m.group(1)),
                'end': ass_time_to_cs(m.group(2)),
                'text': m.group(3),
            })
    if not lines:
        return ''

    result = ''
    k1_end = None
    k2_end = None
    first_orig_start = lines[0]['start']

    for i, line in enumerate(lines):
        is_k1 = (i % 2 == 0)
        style = 'K1' if is_k1 else 'K2'

        if is_k1:
            new_start = (first_orig_start - lead_gap) if k1_end is None else k1_end
        else:
            new_start = first_orig_start if k2_end is None else k2_end
        new_start = max(new_start, 0)

        new_end = line['end'] + posttime
        gap = max(line['start'] - new_start, 0)
        new_text = f'{{\\k{gap}}}{line["text"]}{{\\k{posttime}}}'

        result += f'Dialogue: 0,{int2asstime(new_start)},{int2asstime(new_end)},{style},,0,0,0,karaoke,{new_text}\n'

        if is_k1:
            k1_end = new_end
        else:
            k2_end = new_end

    return result

# ============================================================
# 强制对齐
# ============================================================

# MMS-FA 模型缓存：按设备只加载一次，GUI 连续处理多首歌时避免重复加载 (~1GB)
_mms_cache = {}

def _get_mms(device):
    key = str(device)
    if key not in _mms_cache:
        bundle = torchaudio.pipelines.MMS_FA
        _mms_cache[key] = (
            bundle.get_model().to(device),
            bundle.get_tokenizer(),
            bundle.get_aligner(),
        )
    return _mms_cache[key]

def align_audio_with_text(audio_file_path, text_tokens, non_silent_ranges=[], sr=None, speed=1):
    start_time = time.time()
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")

    try:
        bundle = torchaudio.pipelines.MMS_FA
        if isinstance(audio_file_path, str):
            waveform, sample_rate = torchaudio.load(audio_file_path)
        else:
            waveform = torch.tensor(audio_file_path).float()
            waveform = waveform.unsqueeze(0)
            sample_rate = sr

        if non_silent_ranges:
            total_samples = waveform.shape[1]
            sample_ranges = []
            for start_sec, end_sec in non_silent_ranges:
                start_sample = int(start_sec * sample_rate / speed)
                end_sample = min(int(end_sec * sample_rate / speed), total_samples)
                sample_ranges.append((start_sample, end_sample))
            segments = []
            for start, end in sample_ranges:
                segments.append(waveform[:, start:end])
            waveform = torch.cat(segments, dim=1)

        waveform = waveform.mean(0, keepdim=True)
        waveform = torchaudio.functional.resample(waveform, sample_rate, bundle.sample_rate)

        model, tokenizer, aligner = _get_mms(device)

        valid_tokens = [token for token in text_tokens if token]

        with torch.inference_mode():
            emission, _ = model(waveform.to(device))
            tokens = tokenizer(valid_tokens)
            token_spans = aligner(emission[0].cpu(), tokens)

        if torch.cuda.is_available():
            torch.cuda.empty_cache()

        frame_duration = 1.0 / bundle.sample_rate * 320 * speed
        results = []

        def map_to_original_time(adjusted_time):
            if not non_silent_ranges:
                return adjusted_time
            cumulative_duration = 0.0
            for start_sec, end_sec in non_silent_ranges:
                segment_duration = end_sec - start_sec
                if adjusted_time < cumulative_duration + segment_duration:
                    return start_sec + (adjusted_time - cumulative_duration)
                cumulative_duration += segment_duration
            return non_silent_ranges[-1][1]

        def format_time(time_sec):
            minutes, remainder = divmod(time_sec, 60)
            seconds, centiseconds = divmod(remainder, 1)
            return f"[{int(minutes):02d}:{int(seconds):02d}:{math.floor(centiseconds * 100):02d}]"

        for i, spans in enumerate(token_spans):
            if not spans:
                results.append({'token': valid_tokens[i], 'start': '[error]', 'end': '[error]', 'score': 0.0})
                continue
            adjusted_start = spans[0].start * frame_duration
            adjusted_end = spans[-1].end * frame_duration
            original_start = map_to_original_time(adjusted_start)
            original_end = map_to_original_time(adjusted_end)
            try:
                tok_score = float(sum(s.score for s in spans) / len(spans))
            except Exception:
                tok_score = None
            results.append({
                'token': valid_tokens[i],
                'start': format_time(original_start),
                'end': format_time(original_end),
                'original_start': original_start,
                'original_end': original_end,
                'score': tok_score
            })

        end_time = time.time()
        print("Alignment inference executed in", round(end_time - start_time, 3), "seconds")
        return results

    except Exception as e:
        print(f"Error during alignment: {e}")
        return []

# ============================================================
# 自愈对齐：质量不佳时自动换参数重试并择优（全自动，无需人工返工）
# ============================================================

def _mean_score(results):
    scores = [r['score'] for r in results if r.get('score') is not None]
    return sum(scores) / len(scores) if scores else None

def _alignment_has_failure(results):
    """尺度无关的失败判定：出现 [error]，或低分乐句明显离群。"""
    if any(r.get('start') == '[error]' for r in results):
        return True
    scores = sorted(r['score'] for r in results if r.get('score') is not None)
    n = len(scores)
    if n < 8:
        return False
    median = scores[n // 2]
    mad = sorted(abs(s - median) for s in scores)[n // 2] or 1e-9
    outliers = sum(1 for s in scores if s < median - 3 * mad)
    return outliers / n > 0.12

def _run_align(audio_file, tokens, ns_ranges, sr, speed):
    if float(speed) == 1.0:
        return align_audio_with_text(audio_file, tokens, ns_ranges, sr)
    print(f'Changing the audio speed (x{speed})...')
    y = librosa.effects.time_stretch(audio_file, rate=speed)
    return align_audio_with_text(y, tokens, ns_ranges, sr, speed)

# --- 分句约束对齐（默认启用，align_mode='phrase'）------------------------
# 思路：把歌词按行(\n)切成 token 组、音频按 VAD 切成乐句，DP 把行匹配到乐句
# （允许 N 行并入一个乐句，允许部分乐句为空间奏），再逐句单独对齐。
# 搜索空间小、不会整曲漂移；实测一首日文歌：mean_score +125%、消除 26→0 个
# 早期 token 飘 25s 后的现象、速度 6× 提升。
# _align_autoheal 始终也会跑整曲对齐作候选，按置信度自动择优——分句模式不利
# 的边缘情况下会被整曲反超并采纳，所以默认开启绝不会比原先更差。

def _fmt_seconds(t):
    minutes, remainder = divmod(t, 60)
    seconds, centiseconds = divmod(remainder, 1)
    return f"[{int(minutes):02d}:{int(seconds):02d}:{math.floor(centiseconds * 100):02d}]"

def _split_tokens_by_line(result_list):
    groups, cur = [], []
    for it in result_list:
        if it.get('type') == 0 and it.get('orig') == '\n':
            if cur:
                groups.append(cur)
                cur = []
        elif it.get('pron'):
            cur.append(it['pron'])
    if cur:
        groups.append(cur)
    return groups

def _match_lines_to_phrases(line_groups, ns_ranges):
    """对齐 N 条歌词行到 M 个 VAD 乐句的 DP：
      - N>M（短句被 VAD 合到同一乐句）：把连续若干行并入一个乐句。
      - N<M（间奏多）：允许部分乐句为空（不分配歌词）。
    代价函数 = |按全曲平均语速估算的本组应用时长 − 实际乐句时长|，目标最小化总代价。
    返回长度 M 的 token 组（部分可能为空），保持原始 token 顺序；失败返回 None 让上层回退。"""
    N = len(line_groups)
    M = len(ns_ranges)
    if N == 0 or M == 0:
        return None
    durations = [e - s for s, e in ns_ranges]
    total_dur = sum(durations) or 1.0
    total_tokens = sum(len(g) for g in line_groups) or 1
    rate = total_tokens / total_dur  # tokens/sec, 粗略全曲均速
    cum = [0]
    for g in line_groups:
        cum.append(cum[-1] + len(g))
    INF = float('inf')
    dp = [[INF] * (M + 1) for _ in range(N + 1)]
    back = [[None] * (M + 1) for _ in range(N + 1)]
    dp[0][0] = 0.0
    for j in range(1, M + 1):
        for i in range(N + 1):
            # 选项 1：跳过乐句 j-1（间奏，不分配歌词）
            if dp[i][j - 1] < dp[i][j]:
                dp[i][j] = dp[i][j - 1]
                back[i][j] = ('skip', i)
            # 选项 2：把行 [ip..i) 全部分给乐句 j-1
            for ip in range(0, i):
                tok = cum[i] - cum[ip]
                expected = tok / rate
                cost_local = abs(expected - durations[j - 1])
                cand = dp[ip][j - 1] + cost_local
                if cand < dp[i][j]:
                    dp[i][j] = cand
                    back[i][j] = ('assign', ip)
    if dp[N][M] == INF:
        return None
    matched = [[] for _ in range(M)]
    i, j = N, M
    while j > 0:
        if back[i][j] is None:
            return None
        action, ip = back[i][j]
        if action == 'assign':
            merged = []
            for k in range(ip, i):
                merged.extend(line_groups[k])
            matched[j - 1] = merged
            i = ip
        j -= 1
    if i != 0:
        return None
    return matched

def _align_per_phrase(audio_file, sr, line_groups, ns_ranges):
    """对每个乐句单独跑 MMS-FA 对齐，并把局部时间平移回全曲坐标。
    宽容策略：单个乐句失败 → 该乐句 token 标为 [error] 占位（保持 token 计数）；
    不会因一句失败而废掉整个分句模式（最终由 _align_autoheal 按平均分挑最优）。"""
    if not line_groups or len(ns_ranges) != len(line_groups):
        return None

    def _error_placeholders(toks):
        return [{'token': t, 'start': '[error]', 'end': '[error]', 'score': 0.0} for t in toks]

    out = []
    for (s, e), toks in zip(ns_ranges, line_groups):
        if not toks:
            continue   # 空组：纯间奏乐句，跳过不对齐
        seg = audio_file[int(s * sr):int(e * sr)]
        if len(seg) < int(0.05 * sr):
            out.extend(_error_placeholders(toks))
            continue
        r = align_audio_with_text(seg, toks, [], sr)
        if not r:
            out.extend(_error_placeholders(toks))
            continue
        for x in r:
            if 'original_start' in x:
                x['original_start'] += s
                x['original_end'] += s
                x['start'] = _fmt_seconds(x['original_start'])
                x['end'] = _fmt_seconds(x['original_end'])
        out.extend(r)
    return out

def _align_autoheal(audio_file, tokens, ns_ranges, sr, base_speed,
                    result_list=None, align_mode='global'):
    """收集多个对齐候选并选置信度最高者，全自动无需人工返工：
      - align_mode='phrase' 时先尝试分句约束对齐（实验性）；
      - 整曲对齐始终作为候选/兜底；失败时自动换速重试。
    align_mode='global'（默认）时行为与原先完全一致；若 torchaudio 不提供分数，
    则退化为单次对齐（绝不比现状更差）。"""
    print('Adding timelines...')
    candidates = []

    if align_mode == 'phrase' and result_list is not None:
        try:
            raw_groups = _split_tokens_by_line(result_list)
            matched = _match_lines_to_phrases(raw_groups, ns_ranges)
            if matched is None:
                print('分句↔行 DP 匹配失败，回退整曲对齐。')
                r = None
            else:
                r = _align_per_phrase(audio_file, sr, matched, ns_ranges)
            if r:
                ms = _mean_score(r)
                candidates.append((ms if ms is not None else float('-inf'), r))
                nonempty = sum(1 for g in matched if g)
                print(f'分句约束对齐完成（{len(raw_groups)} 行 → {len(ns_ranges)} 乐句, '
                      f'非空 {nonempty} 个）。')
            else:
                print('分句约束对齐未产出结果，使用整曲对齐。')
        except Exception as e:
            print(f'分句约束对齐失败，回退整曲对齐: {e}')

    best = _run_align(audio_file, tokens, ns_ranges, sr, base_speed)
    if best:
        ms = _mean_score(best)
        candidates.append((ms if ms is not None else float('-inf'), best))
        if _alignment_has_failure(best):
            print('检测到部分乐句对齐置信度偏低，正在自动重试（无需人工介入）...')
            for sp in ([0.5] if float(base_speed) == 1.0 else [1.0]):
                try:
                    res = _run_align(audio_file, tokens, ns_ranges, sr, sp)
                except Exception as e:
                    print(f'自动重试 (speed={sp}) 出错，已跳过: {e}')
                    continue
                if res:
                    ms2 = _mean_score(res)
                    candidates.append((ms2 if ms2 is not None else float('-inf'), res))

    if not candidates:
        return []
    candidates.sort(key=lambda x: x[0], reverse=True)
    if len(candidates) > 1:
        print(f'已自动选择置信度最高的对齐结果（共 {len(candidates)} 个候选）。')
    return candidates[0][1]

# ============================================================
# QC 旁路报告：把每行平均置信度写到 {audio}.qc.txt（人 0 干预的成品 + 一眼可疑）
# ============================================================

def _write_qc_sidecar(path, result_list):
    """生成对齐置信度报告，旁路写入 {audio}.qc.txt：
      - 整曲平均分
      - 每行平均分（低于半数的标 !）
      - 每行最低 3 个 token 及其分数（便于直接定位可疑字）
    阅读这份报告完全可选——目的是让"全自动"模式下用户一眼能看出哪几行需要复查，
    无需打开 Aegisub 听音。"""
    lines = []
    cur_text = ''
    cur_scores = []
    cur_tokens = []
    for item in result_list:
        if item.get('type') == 0 and item.get('orig') == '\n':
            if cur_tokens:
                m = sum(cur_scores) / len(cur_scores) if cur_scores else None
                lines.append((cur_text.strip(), m, list(zip(cur_tokens, cur_scores))))
            cur_text = ''
            cur_scores = []
            cur_tokens = []
        else:
            cur_text += item.get('orig', '')
            if item.get('pron') and isinstance(item.get('score'), (int, float)):
                cur_scores.append(item['score'])
                cur_tokens.append(item['pron'])
    if cur_tokens:
        m = sum(cur_scores) / len(cur_scores) if cur_scores else None
        lines.append((cur_text.strip(), m, list(zip(cur_tokens, cur_scores))))

    try:
        with open(path, 'w', encoding='utf-8') as f:
            f.write('autoKara — 对齐置信度报告\n')
            f.write('=' * 60 + '\n')
            means = [m for _, m, _ in lines if m is not None]
            overall = sum(means) / len(means) if means else 0.0
            # 取最低 ~10%（至少 1）作为复查名单，避免被全曲均值带偏
            sorted_means = sorted(means)
            n_flag = max(1, len(sorted_means) // 10) if sorted_means else 0
            thresh = sorted_means[n_flag - 1] if n_flag > 0 else 0.0
            flagged = [i + 1 for i, (_, m, _) in enumerate(lines)
                       if m is not None and m <= thresh]
            f.write(f'整曲平均置信度: {overall:.3f}    歌词行数: {len(lines)}\n')
            if flagged:
                f.write(f'重点复查（最低 {len(flagged)} 行，标 !）: '
                        + ', '.join(f'L{x:02d}' for x in flagged) + '\n')
            f.write('\n')
            for i, (text, mean, toks) in enumerate(lines, 1):
                marker = '!' if (mean is not None and mean <= thresh) else ' '
                mean_str = f'{mean:.3f}' if mean is not None else ' N/A '
                f.write(f'L{i:02d} {mean_str} {marker} {text}\n')
                if toks:
                    lows = sorted(toks, key=lambda x: x[1])[:3]
                    f.write(f'         低分: ' + '  '.join(f'{t}={s:.3f}' for t, s in lows) + '\n')
    except Exception as e:
        print(f'(QC 报告写入失败，已忽略: {e})')

# ============================================================
# 核心管线
# ============================================================

def run_pipeline(input_audio_path, input_text_path, output_dir,
                 sokuon_split=0, hatsuon_split=1, audio_speed=1.0,
                 tail_correct=3, silent_window_s=0.8, tail_thres_pct=10,
                 tail_thres_ratio=0.1, output_characters_per_line=0,
                 align_mode='phrase'):
    start_time = time.time()

    original_audio_name = os.path.splitext(os.path.basename(input_audio_path))[0]

    print(f"输入音频: {input_audio_path}")
    print(f"输入文本: {input_text_path}")
    print(f"输出目录: {output_dir}")

    # 输入预检：友好失败胜过对齐 10 分钟之后才发现路径不可写或文件损坏
    if not os.path.isfile(input_audio_path):
        raise RuntimeError(f"音频文件不存在: {input_audio_path}")
    if os.path.getsize(input_audio_path) < 1024:
        raise RuntimeError(f"音频文件疑似损坏或为空 (<1KB): {input_audio_path}")
    if not os.path.isfile(input_text_path):
        raise RuntimeError(f"歌词文件不存在: {input_text_path}")
    try:
        os.makedirs(output_dir, exist_ok=True)
        _probe = os.path.join(output_dir, '.write_probe')
        with open(_probe, 'w') as _f:
            _f.write('ok')
        os.remove(_probe)
    except OSError as e:
        raise RuntimeError(f"输出目录不可写：{output_dir}\n（{e}）请换一个有写入权限的目录。")

    if separate.needs_separation(input_audio_path):
        sep_dir = os.path.join(output_dir, "separated")
        input_audio_path = separate.separate_vocals(input_audio_path, sep_dir)

    print('Loading files...')
    result_list = []
    with open(input_text_path, 'r', encoding='utf-8') as file:
        for line in file:
            if line.strip():
                result_list.extend(process_haruhi_line(line, 'jaen', sokuon_split, hatsuon_split))
    if not result_list or result_list[-1]['orig'] != '\n':
        result_list.append({'orig': '\n', 'type': 0, 'pron': ''})

    if tail_correct == 1:
        for i in range(len(result_list)):
            if result_list[i]['type'] == 0:
                try:
                    if result_list[i-1].get('pron') and result_list[i-1]['type'] != 0:
                        pre_vowel = result_list[i-1]['pron'][-1]
                        post_consonant = ''
                        if i < len(result_list)-1:
                            post_i = i + 1
                            while post_i < len(result_list):
                                if 'pron' in result_list[post_i] and len(result_list[post_i]['pron']) >= 1:
                                    post_consonant = result_list[post_i]['pron'][0]
                                    break
                                else:
                                    post_i += 1
                        if pre_vowel != post_consonant and post_consonant not in ('a', 'e', 'i', 'o', 'u'):
                            result_list[i]['pron'] = pre_vowel + 'h'
                except:
                    continue
    elif tail_correct == 2:
        for i in range(len(result_list)):
            if result_list[i]['type'] == 0:
                try:
                    if len(result_list[i-1]['pron']) >= 1 and result_list[i-1]['type'] != 0:
                        result_list[i]['pron'] = result_list[i-1]['pron'][-1] + 'h'
                except:
                    continue

    alignment_tokens = []
    token_to_index_map = {}
    for i, item in enumerate(result_list):
        if 'pron' in item and item['pron']:
            alignment_tokens.append(item['pron'])
            token_to_index_map[len(alignment_tokens) - 1] = i

    if not alignment_tokens:
        raise RuntimeError("歌词解析后没有可对齐的字符。请检查歌词文件是否为空、是否含纯标点，或注音格式是否正确。")

    end_time = time.time()
    print("Lyrics text analysis executed in", round(end_time - start_time, 3), "seconds")

    audio_file, sr = librosa.load(input_audio_path, sr=None)
    non_silent_ranges = non_silent_recog(audio_file, sr, silent_window_s, tail_thres_pct, tail_thres_ratio)

    alignment_results = _align_autoheal(audio_file, alignment_tokens, non_silent_ranges, sr,
                                        audio_speed, result_list=result_list, align_mode=align_mode)

    if not alignment_results:
        # 不用 sys.exit：GUI 工作线程接到 RuntimeError 才能显示实际原因，而不是退出码 "1"
        raise RuntimeError("对齐失败，未能生成时间戳。可能原因：音频无人声 / 歌词与音频严重不匹配 / "
                           "torch+torchaudio 版本不一致。试试在开始菜单运行『环境诊断』查看环境状态。")

    for i, result in enumerate(alignment_results):
        if i in token_to_index_map:
            original_index = token_to_index_map[i]
            if result.get('start') == '[error]':
                # 该 token 对齐失败 → 不写 start/end，V2/V3 会按"无时间"处理而不是崩
                result_list[original_index]['score'] = result.get('score', 0.0)
                continue
            result_list[original_index]['start'] = result['start']
            result_list[original_index]['end'] = result['end']
            result_list[original_index]['score'] = result.get('score')

    result_list = non_silent_head_adjust(result_list, non_silent_ranges)

    if tail_correct == 3:
        ns_small = non_silent_recog(audio_file, sr, .02, tail_thres_pct, tail_thres_ratio)
        ns_ends = [int(np.ceil(ns_end * 100)) for _, ns_end in ns_small]
        for i in range(len(result_list)-1):
            if 'end' in result_list[i] and result_list[i]['type'] != 0 and result_list[i+1]['type'] == 0:
                current_end = parse_time_to_hundredths(result_list[i]['end'])
                next_ind = i + 2
                next_start = np.inf
                while next_ind < len(result_list):
                    if 'start' in result_list[next_ind]:
                        next_start = parse_time_to_hundredths(result_list[next_ind]['start'])
                        break
                    next_ind += 1
                left_index = bisect.bisect_left(ns_ends, current_end)
                right_index = bisect.bisect_left(ns_ends, next_start)
                if left_index < right_index and left_index < len(ns_ends):
                    result_list[i]['end'] = format_hundredths_to_time_str(ns_ends[left_index])
                else:
                    interval_covered = False
                    for nss_start, nss_end in ns_small:
                        if int(nss_start * 100) > current_end:
                            break
                        if int(nss_start * 100) <= current_end and int(np.ceil(nss_end * 100)) >= next_start:
                            interval_covered = True
                            break
                    if interval_covered:
                        result_list[i]['end'] = format_hundredths_to_time_str(max(next_start-2, current_end))

    if output_characters_per_line > 0:
        split_long_segments(result_list, max_length=output_characters_per_line)

    ass_output = process_norm2assV3(result_list)
    ass_head = r'''[Script Info]
ScriptType: v4.00+
YCbCr Matrix: TV.601
PlayResX: 1920
PlayResY: 1080

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,Source Han Serif,71,&H00FFFFFF,&H000000FF,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,1.99999,1.99999,2,11,11,101,1
Style: K1,UD Digi Kyokasho N,100,&H00FFFFFF,&H000000FF,&H00000000,&H00000000,-1,0,0,0,100,100,0,0,1,2,2,2,40,400,160,128
Style: K2,UD Digi Kyokasho N,100,&H00FFFFFF,&H000000FF,&H00000000,&H00000000,-1,0,0,0,100,100,0,0,1,2,2,2,400,40,30,128

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
Comment: 0,0:00:00.00,0:00:00.00,K1,,0,0,0,code syl all,fxgroup.kara=syl.inline_fx==""
Comment: 1,0:00:00.00,0:00:00.00,K1,overlay,0,0,0,template syl noblank all fxgroup kara,!retime("line",-100,500)!{\pos($center,$middle)\an5\shad0\fad(1500,400)\1c&HFF0000&\3c&HFFFFFF&\clip(!$sleft-3!,0,!$sleft-3!,1080)\t($sstart,$send,\clip(!$sleft-3!,0,!$sright+3!,1080))\bord5}
Comment: 0,0:00:00.00,0:00:00.00,K1,,0,0,0,template syl all fxgroup kara,!retime("line",-500,500)!{\pos($center,$middle)\an5\fad(1500,400)}
Comment: 1,0:00:18.65,0:00:20.65,K1,overlay,0,0,0,template furi all,!retime("line",-100,500)!{\pos($center,!$middle+10!)\an5\shad0\fad(1500,400)\1c&HFF0000&\3c&HFFFFFF&\clip(!$sleft-3!,0,!$sleft-3!,1080)\t($sstart,$send,\clip(!$sleft-3!,0,!$sright+3!,1080))\bord5}
Comment: 0,0:00:00.00,0:00:00.00,K1,,0,0,0,template furi all,!retime("line",-500,500)!{\pos($center,!$middle+10!)\an5\fad(1500,400)}
Comment: 0,0:00:00.00,0:00:00.00,K1,music,0,0,0,template fx no_k,!retime("line",-500,500)!{\pos($center,!$middle!)\an5\1c&H505050&\3c&HFFFFFFF&}
'''

    with open(os.path.join(output_dir, f'{original_audio_name}.ass'), 'w', encoding='utf-8') as f:
        f.write(ass_head + ass_output)

    _write_qc_sidecar(os.path.join(output_dir, f'{original_audio_name}.qc.txt'), result_list)

    print(f'Success! 所有文件已输出到: {output_dir}')

# ============================================================
# CLI 入口
# ============================================================

def has_furigana(text):
    return bool(re.search(r'\{[^}]+\|[^}]+\}', text))

def auto_annotate(text):
    lines = text.strip().split('\n')
    result = []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        if has_furigana(line):
            result.append(line)
        else:
            result.append(add_furigana(line))
    return '\n'.join(result)

def doctor():
    """打印环境诊断报告，方便用户提 issue 时一行贴出。"""
    import platform, shutil
    try:
        import importlib.metadata as M
    except Exception:
        import importlib_metadata as M  # py<3.8 fallback
    def v(pkg):
        try:
            return M.version(pkg)
        except Exception:
            return "MISSING"

    cuda_ok = False; cuda_ver = "-"; gpu_name = "-"
    try:
        import torch
        cuda_ok = bool(torch.cuda.is_available())
        cuda_ver = torch.version.cuda or "-"
        if cuda_ok:
            try: gpu_name = torch.cuda.get_device_name(0)
            except Exception: pass
        torch_v = torch.__version__
    except Exception as e:
        torch_v = f"IMPORT FAILED ({e})"

    try:
        import torchaudio
        ta_v = torchaudio.__version__
    except Exception as e:
        ta_v = f"IMPORT FAILED ({e})"

    # MMS 模型
    mms_path = "-"; mms_size = "-"
    try:
        import torch as _t
        cache = os.path.join(_t.hub.get_dir(), 'checkpoints', 'model.pt')
        if os.path.isfile(cache):
            mms_path = cache
            mms_size = f"{os.path.getsize(cache)/1e6:.1f} MB"
    except Exception:
        pass

    try:
        free_gb = shutil.disk_usage(os.path.dirname(os.path.realpath(__file__))).free / 2**30
    except Exception:
        free_gb = -1

    print("autoKara doctor — 环境诊断 ====================================")
    print(f"Python       : {platform.python_version()}  ({sys.executable})")
    print(f"OS           : {platform.platform()}")
    print(f"torch        : {torch_v}    cuda_build={cuda_ver}    available={cuda_ok}")
    print(f"GPU          : {gpu_name}")
    print(f"torchaudio   : {ta_v}")
    print(f"MMS-FA model : {mms_path}  ({mms_size})")
    print(f"SudachiDict  : core={v('SudachiDict-core')}    full={v('SudachiDict-full')}")
    print(f"SudachiPy    : {v('SudachiPy')}")
    print(f"librosa      : {v('librosa')}")
    print(f"demucs       : {v('demucs')}")
    print(f"Janome       : {v('Janome')}    pykakasi: {v('pykakasi')}    pyphen: {v('pyphen')}")
    print(f"Pillow       : {v('Pillow')}    nltk: {v('nltk')}    soundfile: {v('soundfile')}")
    print(f"tkinterdnd2  : {v('tkinterdnd2')}")
    print(f"Free disk    : {free_gb:.1f} GiB" if free_gb > 0 else "Free disk    : -")
    print("=================================================================")

def main():
    if '--doctor' in sys.argv:
        doctor()
        return

    script_dir = os.path.dirname(os.path.realpath(__file__))
    default_input_dir = os.path.join(script_dir, 'input')
    default_output_dir = os.path.join(script_dir, 'output')

    parser = argparse.ArgumentParser(description='autoKara: 自动注音 + 强制对齐 → 卡拉OK字幕')
    parser.add_argument('-i', '--input_dir', default=default_input_dir, help=f'输入文件夹路径 (默认: ./input)')
    parser.add_argument('-o', '--output_dir', default=default_output_dir, help=f'输出文件夹路径 (默认: ./output)')
    parser.add_argument('--raw', action='store_true', help='强制重新自动注音（忽略已有的{漢字|よみ}标记）')
    parser.add_argument('-x', '--sokuon_split', type=int, default=0, help='是否将促音与前一字符拆开')
    parser.add_argument('-n', '--hatsuon_split', type=int, default=1, help='是否将拨音与前一字符拆开')
    parser.add_argument('-v', '--audio_speedx', type=float, default=1, help='推理时使用的音频倍速')
    parser.add_argument('-t', '--tail_correct', type=int, default=3, help='尾音拖长选项。建议取默认值3')
    parser.add_argument('-tl', '--tail_limit_window', type=float, default=0.8, help='全曲静音检测窗口时长，单位：秒')
    parser.add_argument('-tp', '--tail_thres_pct', type=float, default=10, help='尾音阈值百分位数，单位：％')
    parser.add_argument('-tr', '--tail_thres_ratio', type=float, default=0.1, help='尾音阈值比例')
    parser.add_argument('-cl', '--characters_per_line', type=int, default=0, help='输出文件每行最大字数')
    parser.add_argument('--align-mode', choices=['global', 'phrase'], default='phrase',
                        help='对齐模式：phrase=分句约束(默认，实测置信度+125%,无整曲漂移)；global=整曲(回退选项)。'
                             'phrase 模式始终也会跑 global 作候选，按置信度自动择优。')
    args = parser.parse_args()

    input_dir = os.path.normpath(args.input_dir)
    output_dir = os.path.normpath(args.output_dir)

    if not os.path.isdir(input_dir):
        print(f"错误：输入文件夹 '{input_dir}' 不存在。")
        sys.exit(1)

    wav_files = []
    for ext in ['.wav', '.mp3', '.flac', '.ogg', '.m4a', '.aac', '.opus']:
        wav_files.extend(glob.glob(os.path.join(input_dir, '*' + ext)))
    txt_files = glob.glob(os.path.join(input_dir, '*.txt'))

    if len(wav_files) != 1:
        print(f"错误：需要恰好一个音频文件，找到 {len(wav_files)} 个。")
        sys.exit(1)
    if len(txt_files) != 1:
        print(f"错误：需要恰好一个 .txt 文件，找到 {len(txt_files)} 个。")
        sys.exit(1)

    input_audio = wav_files[0]
    input_text = txt_files[0]

    # 自动注音
    with open(input_text, 'r', encoding='utf-8') as f:
        raw_text = f.read()

    if args.raw or not has_furigana(raw_text):
        print("检测到原始歌词，正在自动注音...")
        annotated = auto_annotate(raw_text)
        tmp = tempfile.NamedTemporaryFile(mode='w', suffix='.txt', delete=False, encoding='utf-8')
        tmp.write(annotated)
        tmp.close()
        input_text = tmp.name
        print("注音完成")

    run_pipeline(
        input_audio, input_text, output_dir,
        sokuon_split=args.sokuon_split,
        hatsuon_split=args.hatsuon_split,
        audio_speed=args.audio_speedx,
        tail_correct=args.tail_correct,
        silent_window_s=args.tail_limit_window,
        tail_thres_pct=args.tail_thres_pct,
        tail_thres_ratio=args.tail_thres_ratio,
        output_characters_per_line=args.characters_per_line,
        align_mode=args.align_mode,
    )

if __name__ == '__main__':
    main()
