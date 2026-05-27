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

try:
    cmu_dict = cmudict.dict()
except LookupError:
    nltk.download('cmudict')
    cmu_dict = cmudict.dict()
eng_dic = pyphen.Pyphen(lang='en_US')

newnums = ['①', '②', '③', '④', '⑤', '⑥', '⑦', '⑧', '⑨', '⑩',
           '⑪', '⑫', '⑬', '⑭', '⑮', '⑯', '⑰', '⑱', '⑲', '⑳',
           '㉑', '㉒', '㉓', '㉔', '㉕', '㉖', '㉗', '㉘', '㉙', '㉚']

# ============================================================
# 工具函数
# ============================================================

def parse_time_to_hundredths(time_str):
    match = re.match(r'\[(\d{2}):(\d{2}):(\d{2})\]', time_str)
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
    if word_lower not in cmu_dict:
        print("Word '"+word+"' not in the dictionary...")
        direct_syllables = [i.replace("'", '').lower() for i in surface_syllables]
        return list(zip(surface_syllables, direct_syllables))

    phonemes = cmu_dict[word_lower][0]
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
            assert len(parts) == 2, f"注音格式错误：{token}"
            kanji, ruby_text = parts
            ruby_text = sylla_split(ruby_text, sokuon_split, hatsuon_split)
            assert len(ruby_text) >= 1, "振假名为空"
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
                asstxt += r'{\k'+str(item_kdur)+'}' + zero_str
        else:
            if struc[i+1].get('start'):
                item_kdur = parse_time_to_hundredths(struc[i+1]['start']) - parse_time_to_hundredths(item['start'])
                nowtime = parse_time_to_hundredths(struc[i+1]['start'])
            else:
                item_kdur = parse_time_to_hundredths(item['end']) - parse_time_to_hundredths(item['start'])
                nowtime = parse_time_to_hundredths(item['end'])
            asstxt += r'{\k'+str(item_kdur)+'}'
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

        model = bundle.get_model().to(device)
        tokenizer = bundle.get_tokenizer()
        aligner = bundle.get_aligner()

        valid_tokens = [token for token in text_tokens if token]

        with torch.inference_mode():
            emission, _ = model(waveform.to(device))
            tokens = tokenizer(valid_tokens)
            token_spans = aligner(emission[0].cpu(), tokens)

        del model
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
                results.append({'token': valid_tokens[i], 'start': '[error]', 'end': '[error]'})
                continue
            adjusted_start = spans[0].start * frame_duration
            adjusted_end = spans[-1].end * frame_duration
            original_start = map_to_original_time(adjusted_start)
            original_end = map_to_original_time(adjusted_end)
            results.append({
                'token': valid_tokens[i],
                'start': format_time(original_start),
                'end': format_time(original_end),
                'original_start': original_start,
                'original_end': original_end
            })

        end_time = time.time()
        print("Alignment inference executed in", round(end_time - start_time, 3), "seconds")
        return results

    except Exception as e:
        print(f"Error during alignment: {e}")
        return []

# ============================================================
# 核心管线
# ============================================================

def run_pipeline(input_audio_path, input_text_path, output_dir,
                 sokuon_split=0, hatsuon_split=1, audio_speed=1.0,
                 tail_correct=3, silent_window_s=0.8, tail_thres_pct=10,
                 tail_thres_ratio=0.1, output_characters_per_line=0):
    start_time = time.time()

    original_audio_name = os.path.splitext(os.path.basename(input_audio_path))[0]

    print(f"输入音频: {input_audio_path}")
    print(f"输入文本: {input_text_path}")
    print(f"输出目录: {output_dir}")
    os.makedirs(output_dir, exist_ok=True)

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

    end_time = time.time()
    print("Lyrics text analysis executed in", round(end_time - start_time, 3), "seconds")

    audio_file, sr = librosa.load(input_audio_path, sr=None)
    non_silent_ranges = non_silent_recog(audio_file, sr, silent_window_s, tail_thres_pct, tail_thres_ratio)

    if audio_speed == 1:
        print('Adding timelines...')
        print("[调试信息] 即将被用于对齐的 alignment_tokens:", alignment_tokens)
        alignment_results = align_audio_with_text(audio_file, alignment_tokens, non_silent_ranges, sr)
    else:
        print('Changing the audio speed...')
        start_time = time.time()
        y_processed = librosa.effects.time_stretch(audio_file, rate=audio_speed)
        end_time = time.time()
        print("Audio speed changing executed in", round(end_time - start_time, 3), "seconds")
        print('Adding timelines...')
        alignment_results = align_audio_with_text(y_processed, alignment_tokens, non_silent_ranges, sr, audio_speed)

    if not alignment_results:
        print("错误：对齐失败，未能生成时间戳。程序终止。")
        sys.exit(1)

    for i, result in enumerate(alignment_results):
        if i in token_to_index_map:
            original_index = token_to_index_map[i]
            result_list[original_index]['start'] = result['start']
            result_list[original_index]['end'] = result['end']

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

def main():
    script_dir = os.path.dirname(os.path.realpath(__file__))
    default_input_dir = os.path.join(script_dir, 'input')
    default_output_dir = os.path.join(script_dir, 'output')

    parser = argparse.ArgumentParser(description='FA-Kara: 自动注音 + 强制对齐 → 卡拉OK字幕')
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
    )

if __name__ == '__main__':
    main()
