"""Audio viewer: play a decoded audio entry from memory.

INPUT = "read": the dispatcher runs ``processors.read`` and hands over the
:class:`ReadResult`, whose ``data`` is directly playable bytes (a WEM that
is the repacked Ogg/WAV, a native container the payload unchanged).  Those
bytes go to a fresh :class:`QBuffer` per load and play through
QMediaPlayer.setSourceDevice -- no temp file, no transcode.  The buffer is
rebuilt, not reused, because the backend reads it on its own thread.

codec / channels / sample rate come from the reader's metadata
(:class:`WemAudioInfo` or :class:`AudioInfo`); duration and bitrate come
from the player once media loads (the reader leaves MP3/M4A duration
unknown, needing a full walk the facade skips).

Looping plays a from/to range (millisecond precision); the range resets to
the whole stream per load while the checkbox state carries across loads.

Save writes the playable bytes as-is under the reader's container extension
-- the only formats offered, since the viewer never transcodes.
"""

from __future__ import annotations

import os
import re
from typing import Optional

from PySide6 import QtCore, QtGui, QtMultimedia, QtWidgets

from neoxtractor.gui.utils.export import sanitize_filename
from neoxtractor.gui.viewers.base import ViewerBase, ViewSource


class AudioViewer(ViewerBase):
    """Widget that plays one decoded audio stream and saves it."""

    name = "Audio Viewer"
    accepted_extensions = {"wem", "wav", "ogg", "flac", "mp3", "m4a"}

    def __init__(self, parent: Optional[QtWidgets.QWidget] = None) -> None:
        super().__init__(parent)

        self._data: bytes = b""
        self._ext: str = ""
        self._stem: str = "audio"
        self._info: object | None = None
        self._seeking = False

        # Built before the controls: their signal connections reference them.
        # No buffer here: each load builds a fresh QBuffer (module docstring).
        self._buffer: QtCore.QBuffer | None = None
        self._audio_output = QtMultimedia.QAudioOutput(self)
        self._player = QtMultimedia.QMediaPlayer(self)
        self._player.setAudioOutput(self._audio_output)
        self._player.positionChanged.connect(self._on_position)
        self._player.durationChanged.connect(self._on_duration)
        self._player.playbackStateChanged.connect(self._on_state)
        self._player.mediaStatusChanged.connect(self._on_media_status)

        layout = QtWidgets.QVBoxLayout(self)

        self.info_label = QtWidgets.QLabel("No audio loaded.")
        self.info_label.setWordWrap(True)
        layout.addWidget(self.info_label)

        controls = QtWidgets.QHBoxLayout()
        self.play_button = QtWidgets.QPushButton("Play")
        self.play_button.setEnabled(False)
        self.play_button.clicked.connect(self._toggle_play)
        controls.addWidget(self.play_button)

        self.stop_button = QtWidgets.QPushButton("Stop")
        self.stop_button.setEnabled(False)
        self.stop_button.clicked.connect(self._player.stop)
        controls.addWidget(self.stop_button)

        self.position_slider = QtWidgets.QSlider(QtCore.Qt.Orientation.Horizontal)
        self.position_slider.setEnabled(False)
        self.position_slider.sliderPressed.connect(self._begin_seek)
        self.position_slider.sliderReleased.connect(self._end_seek)
        controls.addWidget(self.position_slider, stretch=1)

        self.time_label = QtWidgets.QLabel("0:00 / 0:00")
        controls.addWidget(self.time_label)

        self.volume_slider = QtWidgets.QSlider(QtCore.Qt.Orientation.Horizontal)
        self.volume_slider.setRange(0, 100)
        self.volume_slider.setValue(100)
        self.volume_slider.setFixedWidth(90)
        self.volume_slider.valueChanged.connect(
            lambda v: self._audio_output.setVolume(v / 100.0)
        )
        controls.addWidget(self.volume_slider)
        layout.addLayout(controls)

        loop_row = QtWidgets.QHBoxLayout()
        self.loop_check = QtWidgets.QCheckBox("Loop")
        self.loop_check.toggled.connect(self._enter_loop_range)
        loop_row.addWidget(self.loop_check)
        loop_row.addWidget(QtWidgets.QLabel("from"))
        self.loop_from = _TimeSpinBox()
        self.loop_from.valueChanged.connect(self._clamp_loop_range)
        loop_row.addWidget(self.loop_from)
        loop_row.addWidget(QtWidgets.QLabel("to"))
        self.loop_to = _TimeSpinBox()
        self.loop_to.valueChanged.connect(self._clamp_loop_range)
        loop_row.addWidget(self.loop_to)
        loop_row.addStretch(1)
        layout.addLayout(loop_row)

        save_row = QtWidgets.QHBoxLayout()
        self.save_button = QtWidgets.QPushButton("Save audio...")
        self.save_button.setEnabled(False)
        self.save_button.clicked.connect(self.save_audio)
        save_row.addWidget(self.save_button)
        save_row.addStretch(1)
        layout.addLayout(save_row)

        layout.addStretch(1)

    # --- viewer contract ----------------------------------------------------

    def load(self, source: ViewSource) -> None:
        super().load(source)
        result = source.read_result
        if result is None or not isinstance(result.data, (bytes, bytearray)):
            raise ValueError("No decodable audio for this entry.")

        self._data = bytes(result.data)
        self._ext = result.ext or "bin"
        self._info = result.metadata
        self._stem = os.path.splitext(sanitize_filename(source.name))[0] or "audio"

        self._player.stop()
        # Fresh buffer per load; the previous one is released only after the
        # player has moved to the new device.
        previous = self._buffer
        buffer = QtCore.QBuffer(self)
        buffer.setData(self._data)
        buffer.open(QtCore.QIODevice.OpenModeFlag.ReadOnly)
        self._buffer = buffer
        # The url is a container hint only; the bytes come from the device.
        self._player.setSourceDevice(
            buffer, QtCore.QUrl.fromLocalFile(f"audio.{self._ext}")
        )
        if previous is not None:
            previous.close()
            previous.deleteLater()

        self.info_label.setText(self._info_text())
        self.play_button.setEnabled(True)
        self.stop_button.setEnabled(True)
        self.position_slider.setEnabled(True)
        self.save_button.setEnabled(True)
        self.position_slider.setRange(0, 0)
        self.time_label.setText("0:00 / 0:00")

    def unload(self) -> None:
        self._player.stop()
        # Detach the device before destroying the buffer.
        self._player.setSource(QtCore.QUrl())
        if self._buffer is not None:
            self._buffer.close()
            self._buffer.deleteLater()
            self._buffer = None
        super().unload()
        self._info = None
        self._data = b""
        self.info_label.setText("No audio loaded.")
        self.play_button.setEnabled(False)
        self.stop_button.setEnabled(False)
        self.position_slider.setEnabled(False)
        self.save_button.setEnabled(False)

    # --- playback -----------------------------------------------------------

    def _toggle_play(self) -> None:
        if (
            self._player.playbackState()
            == QtMultimedia.QMediaPlayer.PlaybackState.PlayingState
        ):
            self._player.pause()
        else:
            self._enter_loop_range()
            self._player.play()

    def _clamp_loop_range(self) -> None:
        """Keep from <= to whichever spinbox the user moved last."""
        if self.loop_from.value() > self.loop_to.value():
            if self.sender() is self.loop_from:
                self.loop_to.setValue(self.loop_from.value())
            else:
                self.loop_from.setValue(self.loop_to.value())

    def _enter_loop_range(self) -> None:
        """Jump to the loop start when armed and the cursor is outside."""
        if not self.loop_check.isChecked():
            return
        start, end = self._loop_bounds_ms()
        if end > start and not start <= self._player.position() < end:
            self._player.setPosition(start)

    def _loop_bounds_ms(self) -> tuple[int, int]:
        """The spinbox range in whole milliseconds (the player works in ms)."""
        return (
            round(self.loop_from.value() * 1000),
            round(self.loop_to.value() * 1000),
        )

    def _loop_target(self, position: int) -> Optional[int]:
        """The loop jump for *position* (ms): the range start past the end, else None."""
        if not self.loop_check.isChecked():
            return None
        start, end = self._loop_bounds_ms()
        if end > start and position >= end:
            return start
        return None

    def _loop_restart_ms(self) -> Optional[int]:
        """The loop start to rewind to at end-of-stream, or None when not looping."""
        if not self.loop_check.isChecked():
            return None
        start, end = self._loop_bounds_ms()
        return start if end > start else None

    def _begin_seek(self) -> None:
        self._seeking = True

    def _end_seek(self) -> None:
        self._player.setPosition(self.position_slider.value())
        self._seeking = False

    def _on_position(self, position: int) -> None:
        target = self._loop_target(position)
        if target is not None:
            self._player.setPosition(target)
            return
        if not self._seeking:
            self.position_slider.setValue(position)
        self.time_label.setText(
            f"{_fmt(position)} / {_fmt(self._player.duration())}"
        )

    def _on_duration(self, duration: int) -> None:
        self.position_slider.setRange(0, max(0, duration))
        self.time_label.setText(f"{_fmt(0)} / {_fmt(duration)}")
        # The loop range follows each new stream: full length by default.
        seconds = max(0, duration) / 1000.0
        self.loop_from.setMaximum(seconds)
        self.loop_to.setMaximum(seconds)
        self.loop_from.setValue(0.0)
        self.loop_to.setValue(seconds)
        # A reader that left duration unknown (MP3, M4A) learns it now.
        if self._info is not None and not getattr(self._info, "duration_samples", 0):
            self.info_label.setText(self._info_text())

    def _on_state(self, state: object) -> None:
        playing = (
            state == QtMultimedia.QMediaPlayer.PlaybackState.PlayingState
        )
        self.play_button.setText("Pause" if playing else "Play")

    def _on_media_status(self, status: QtMultimedia.QMediaPlayer.MediaStatus) -> None:
        if status == QtMultimedia.QMediaPlayer.MediaStatus.LoadedMedia:
            # Bitrate is only known once the backend has read the stream header.
            self.info_label.setText(self._info_text())
        elif status == QtMultimedia.QMediaPlayer.MediaStatus.EndOfMedia:
            # A loop reaching the stream end repeats instead of stopping. The
            # backend fires this before positionChanged reaches ``to`` when the
            # range ends at the duration, so the tail is covered here.
            jump = self._loop_restart_ms()
            if jump is not None:
                self._player.setPosition(jump)
                self._player.play()

    # --- info / save --------------------------------------------------------

    def _info_text(self) -> str:
        info = self._info
        if info is None:
            return "No audio loaded."
        codec = str(getattr(info, "codec", "")).upper() or "?"
        channels = getattr(info, "channels", 0)
        rate = getattr(info, "sample_rate", 0)
        parts = [f"Codec: {codec}", f"{channels} ch", f"{rate} Hz"]
        bitrate = self._bitrate_text()
        if bitrate:
            parts.append(bitrate)
        parts.append(self._duration_text())
        return " | ".join(parts)

    def _bitrate_text(self) -> str:
        value = self._player.metaData().value(
            QtMultimedia.QMediaMetaData.Key.AudioBitRate
        )
        if isinstance(value, int) and value > 0:
            return f"{value // 1000} kbps"
        return ""

    def _duration_text(self) -> str:
        samples = getattr(self._info, "duration_samples", 0)
        if samples:
            seconds = getattr(self._info, "duration_seconds", 0.0)
            return f"{_fmt(int(seconds * 1000))} ({samples} samples)"
        duration = self._player.duration()
        if duration > 0:
            return _fmt(duration)
        return "duration pending"

    def save_audio(self) -> None:
        if not self._data:
            return
        default = os.path.join(os.path.expanduser("~"), f"{self._stem}.{self._ext}")
        chosen, _ = QtWidgets.QFileDialog.getSaveFileName(
            self, "Save audio", default, f"Audio (*.{self._ext})"
        )
        if not chosen:
            return
        path = os.path.splitext(chosen)[0] + "." + self._ext
        try:
            with open(path, "wb") as handle:
                handle.write(self._data)
        except OSError as exc:
            QtWidgets.QMessageBox.critical(self, "Save failed", str(exc))
            return
        self.info_label.setText(f"Saved {os.path.basename(path)}")


def _fmt(milliseconds: int) -> str:
    seconds = max(0, milliseconds // 1000)
    return f"{seconds // 60}:{seconds % 60:02d}"


# Clock-form edit patterns for _TimeSpinBox.validate: complete forms
# (m:ss / m:ss.mmm / bare decimal seconds) and edit prefixes ("1", "1:",
# "1:0", "1:02.", "1:02.5").
_CLOCK_FULL_RE = re.compile(
    r"\d+:\d{1,2}(\.\d{1,3})?$|\d+(\.\d*)?$|^\d*\.\d+$"
)
_CLOCK_PREFIX_RE = re.compile(r"\d*(:\d{0,2}(\.\d{0,3})?)?$")


class _TimeSpinBox(QtWidgets.QDoubleSpinBox):
    """A seconds-valued spinbox shown and edited as ``m:ss.mmm``.

    The float value stays in seconds (so the 1 ms precision is real), but the
    text is a clock string: editing parses the same form back.  ``validate``
    is overridden because the base decimal validator rejects the colon and
    would block manual typing before ``valueFromText`` ever sees it.
    """

    def __init__(self, parent: Optional[QtWidgets.QWidget] = None) -> None:
        super().__init__(parent)
        self.setDecimals(3)
        self.setRange(0.0, 0.0)
        self.setKeyboardTracking(False)

    def textFromValue(self, value: float) -> str:
        total_ms = max(0, round(value * 1000))
        minutes, rem = divmod(total_ms, 60000)
        seconds, millis = divmod(rem, 1000)
        return f"{minutes}:{seconds:02d}.{millis:03d}"

    def validate(self, text: str, pos: int):
        # The base validator only knows decimal numbers, so the colon form
        # would be Invalid and valueFromText would never run.  Accept the
        # clock grammar here: complete forms now, edit prefixes as
        # Intermediate.
        state = super().validate(text, pos)
        if state[0] != QtGui.QValidator.State.Invalid:
            return state
        cleaned = text.strip()
        # PySide6 wants the (State, text, pos) triple back, text unchanged.
        if _CLOCK_FULL_RE.match(cleaned):
            return QtGui.QValidator.State.Acceptable, text, pos
        if _CLOCK_PREFIX_RE.match(cleaned):
            return QtGui.QValidator.State.Intermediate, text, pos
        return state

    def fixup(self, text: str) -> str:
        # Partial clock text ("1:", "1:02.") parses through the same rules,
        # so committing mid-edit normalizes instead of reverting.
        return self.textFromValue(self.valueFromText(text))

    def valueFromText(self, text: str) -> float:
        cleaned = text.strip()
        if ":" not in cleaned:
            # A bare number reads as plain seconds.
            try:
                return float(cleaned)
            except ValueError:
                return self.value()
        minutes, _, rest = cleaned.partition(":")
        seconds, _, frac = rest.partition(".")
        try:
            total_ms = (
                int(minutes or 0) * 60000
                + int(seconds or 0) * 1000
                + int((frac + "000")[:3] or 0)
            )
        except ValueError:
            return self.value()
        return total_ms / 1000.0


# Contract members read by the viewers registry.  The class attributes are
# the single source of truth; these module-level names only re-export them.
NAME = AudioViewer.name
VIEWER_CLASS = AudioViewer
ACCEPTED_EXTENSIONS = AudioViewer.accepted_extensions
INPUT = "read"
FALLBACK: str | None = None
PRIORITY = 100
