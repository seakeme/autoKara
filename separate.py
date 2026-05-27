"""人声分离模块 — 基于 Demucs htdemucs_ft 模型"""
import os
import torch
import torchaudio

from demucs.pretrained import get_model
from demucs.apply import apply_model
from demucs.audio import convert_audio, save_audio

_separator = None

AUDIO_EXTENSIONS = {'.wav', '.mp3', '.flac', '.ogg', '.m4a', '.aac', '.opus'}


def is_supported_audio(path):
    """检查是否为支持的音频格式"""
    return os.path.splitext(path)[1].lower() in AUDIO_EXTENSIONS


def needs_separation(path):
    """判断是否需要人声分离：非 .wav 格式一律分离"""
    return os.path.splitext(path)[1].lower() != '.wav'


def _get_model():
    global _separator
    if _separator is None:
        _separator = get_model('htdemucs_ft')
    return _separator


def separate_vocals(input_path, output_dir):
    """分离人声并保存，返回 vocals.wav 路径"""
    model = _get_model()
    device = 'cuda' if torch.cuda.is_available() else 'cpu'

    print(f"正在进行人声分离 (htdemucs_ft, {device})...")

    # 加载并转换音频
    wav, sr = torchaudio.load(input_path)
    wav = convert_audio(wav, sr, model.samplerate, model.audio_channels)

    # 确保是 3D tensor: (batch, channels, samples)
    if wav.dim() == 2:
        wav = wav.unsqueeze(0)

    wav = wav.to(device)

    # 分离
    with torch.no_grad():
        sources = apply_model(
            model, wav,
            shifts=1, split=True, overlap=0.1,
            progress=False, device=device
        )

    # htdemucs 输出顺序: [drums, bass, other, vocals]
    vocals = sources[0, 3]  # (channels, samples)

    # 保存
    os.makedirs(output_dir, exist_ok=True)
    base_name = os.path.splitext(os.path.basename(input_path))[0]
    output_path = os.path.join(output_dir, f"{base_name}_vocals.wav")
    save_audio(vocals.cpu(), output_path, model.samplerate)

    print(f"人声分离完成: {output_path}")
    return output_path
