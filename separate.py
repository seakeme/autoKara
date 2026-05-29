"""人声分离模块 — 基于 Demucs htdemucs_ft 模型"""
import os
import torch

from demucs.pretrained import get_model
from demucs.apply import apply_model
from demucs.audio import convert_audio

_separator = None

AUDIO_EXTENSIONS = {'.wav', '.mp3', '.flac', '.ogg', '.m4a', '.aac', '.opus'}


def load_audio_tensor(path):
    """把音频读成 (channels, samples) 的 float32 张量 + 采样率，不依赖 torchcodec。

    新版 torchaudio(>=2.8) 把 torchaudio.load() 默认改走 TorchCodec 后端，
    而 torchcodec 依赖 FFmpeg、wheel 还不全，目标机常常没有 -> 会报
    "TorchCodec is required for load_with_torchcodec"。这里改用 soundfile
    (wav/flac/ogg/mp3) 优先、librosa 兜底(m4a/aac/opus 需系统 ffmpeg)，
    都是项目已有依赖，彻底绕开 torchcodec。"""
    # 1) soundfile：libsndfile 1.2+ 支持 wav/flac/ogg/mp3
    try:
        import soundfile as sf
        data, sr = sf.read(path, dtype='float32', always_2d=True)  # (frames, channels)
        wav = torch.from_numpy(data.T).contiguous()                # (channels, frames)
        if wav.numel() > 0:
            return wav, sr
    except Exception:
        pass
    # 2) librosa 兜底（可经 audioread/ffmpeg 处理更多格式）
    import numpy as np
    import librosa
    data, sr = librosa.load(path, sr=None, mono=False)             # (channels, frames) 或 (frames,)
    if data.ndim == 1:
        data = data[None, :]
    wav = torch.from_numpy(np.ascontiguousarray(data)).float()
    return wav, sr


def save_wav_tensor(wav, path, sr):
    """把 (channels, samples) 张量存成 WAV，用 soundfile 写，不依赖 torchcodec。

    demucs 自带的 save_audio() 内部调 torchaudio.save() -> 新版同样要 torchcodec
    才能用，所以这里直接用 soundfile 写 WAV（采样率/声道与输入一致）。"""
    import numpy as np
    import soundfile as sf
    arr = wav.detach().cpu().numpy()
    if arr.ndim == 1:
        arr = arr[None, :]
    arr = arr.T  # (channels, frames) -> (frames, channels)，soundfile 期望的布局
    arr = np.clip(arr, -1.0, 1.0)
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    sf.write(path, arr, int(sr), subtype='PCM_16')


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

    # 加载并转换音频（不走 torchaudio.load，避开 torchcodec 依赖）
    wav, sr = load_audio_tensor(input_path)
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

    # 保存（用 soundfile 写 WAV，避开 torchaudio.save 的 torchcodec 依赖）
    os.makedirs(output_dir, exist_ok=True)
    base_name = os.path.splitext(os.path.basename(input_path))[0]
    output_path = os.path.join(output_dir, f"{base_name}_vocals.wav")
    save_wav_tensor(vocals.cpu(), output_path, model.samplerate)

    print(f"人声分离完成: {output_path}")
    return output_path
