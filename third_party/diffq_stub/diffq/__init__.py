"""Narjo Sing helper's stand-in for diffq (MIT). Imported by audio-separator's Demucs code, never called for the
unquantized models this helper uses; any call means a quantized model was requested, which is unsupported."""

_MESSAGE = "Quantized Demucs models are not supported by the Narjo Sing helper"


class DiffQuantizer:
    def __init__(self, *args, **kwargs):
        raise RuntimeError(_MESSAGE)


class UniformQuantizer:
    def __init__(self, *args, **kwargs):
        raise RuntimeError(_MESSAGE)


def restore_quantized_state(*args, **kwargs):
    raise RuntimeError(_MESSAGE)
