import os
from abc import ABCMeta, abstractmethod


class IFile(metaclass=ABCMeta):
    """
    Abstracted file interface.
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """Name of the file."""

    @property
    def basename(self) -> str:
        """Name of the file without extension."""
        return os.path.basename(self.name)

    @property
    def extension(self) -> str:
        """Extension of the file."""
        return os.path.splitext(self.name)[1][1:]

    @property
    @abstractmethod
    def data(self) -> bytes:
        """Raw data of the file."""

    @property
    def size(self) -> int:
        """Size of the file in bytes."""
        return len(self.data)


class SimpleFile(IFile):
    """Simple file implementation."""

    def __init__(self, name: str, data: bytes):
        self._name = name
        self._data = data

    @property
    def name(self) -> str:
        return self._name

    @property
    def data(self) -> bytes:
        return self._data
