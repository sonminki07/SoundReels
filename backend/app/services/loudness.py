"""
Perceptual Loudness Normalization Utility (ITU-R BS.1770 / EBU R128 LUFS)
Computes a gain multiplier so all SFX play at a uniform perceived loudness (-18 LUFS).
"""

import math
from pathlib import Path
import numpy as np
import librosa
import pyloudnorm as pyln


def compute_norm_gain(file_path: str | Path, target_lufs: float = -18.0) -> float:
    """
    Measures active perceptual loudness (LUFS for >=0.45s, active RMS dBFS for ultra-short SFX)
    and returns a safe gain multiplier clamped between [0.15, 8.0].
    """
    try:
        y, sr = librosa.load(str(file_path), sr=48000, mono=True, duration=15.0)
        if len(y) == 0:
            return 1.0

        # Trim silence so long quiet tails don't skew loudness
        active, _ = librosa.effects.trim(y, top_db=35)
        if len(active) == 0:
            active = y

        peak = float(np.max(np.abs(active)))
        if peak < 1e-6:
            return 1.0

        measured_db = None
        if len(active) >= int(sr * 0.45):
            meter = pyln.Meter(sr)
            lufs = float(meter.integrated_loudness(active))
            if math.isfinite(lufs) and lufs > -70.0:
                measured_db = lufs

        if measured_db is None:
            # Ultra-short SFX fallback: RMS dBFS (calibrated roughly -3dB relative to LUFS)
            rms = float(np.sqrt(np.mean(active ** 2)))
            if rms < 1e-6:
                return 1.0
            measured_db = 20.0 * math.log10(rms)

        gain = 10.0 ** ((target_lufs - measured_db) / 20.0)
        # Clamp within safe range; Web Audio DynamicsCompressor handles transient peaks
        return float(round(max(0.15, min(8.0, gain)), 4))
    except Exception as e:
        print(f"[Loudness] Error measuring {file_path}: {e}")
        return 1.0
