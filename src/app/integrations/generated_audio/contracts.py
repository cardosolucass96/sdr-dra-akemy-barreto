from __future__ import annotations


class GeneratedAudioStorageError(RuntimeError):
    pass


class GeneratedAudioStoredFileNotFoundError(RuntimeError):
    pass


class GeneratedAudioConversionError(RuntimeError):
    def __init__(self, message: str, *, error_code: str) -> None:
        super().__init__(message)
        self.error_code = error_code


__all__ = [
    "GeneratedAudioConversionError",
    "GeneratedAudioStorageError",
    "GeneratedAudioStoredFileNotFoundError",
]
