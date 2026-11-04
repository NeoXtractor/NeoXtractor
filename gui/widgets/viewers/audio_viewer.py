"""Playable WEM audio viewer with transcoding export."""

from __future__ import annotations

import os
import shutil
import tempfile

from PySide6 import QtCore, QtMultimedia, QtWidgets

from core.file import IFile
from core.wem import convert, probe
from gui.widgets.viewer import Viewer

EXPORT_TARGETS = {
    "vorbis": [("Ogg Vorbis (*.ogg)", "ogg")],
    "opus": [("Ogg Opus (*.opus)", "opus")],
    "pcm": [("Waveform audio (*.wav)", "wav")],
}


class AudioViewer(Viewer):
    """Decode a Wwise `.wem` into a playable file and offer transcoded export."""

    name = "Audio Viewer"
    accepted_extensions = {"wem"}

    def __init__(self, parent: QtWidgets.QWidget | None = None):
        super().__init__(parent)

        self._file: IFile | None = None
        self._info = None
        self._temp_dir: str | None = None
        self._export_name = "audio"
        self._seeking = False

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
        self.stop_button.clicked.connect(self._stop)
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
        self.volume_slider.valueChanged.connect(self._set_volume)
        controls.addWidget(self.volume_slider)

        layout.addLayout(controls)

        export_row = QtWidgets.QHBoxLayout()
        self.export_button = QtWidgets.QPushButton("Export audio...")
        self.export_button.setEnabled(False)
        self.export_button.clicked.connect(self.export_audio)
        export_row.addWidget(self.export_button)
        export_row.addStretch(1)
        layout.addLayout(export_row)

        self.status_label = QtWidgets.QLabel("")
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)

        layout.addStretch(1)

        self.player = QtMultimedia.QMediaPlayer(self)
        self.audio_output = QtMultimedia.QAudioOutput(self)
        self.player.setAudioOutput(self.audio_output)
        self.player.positionChanged.connect(self._on_position)
        self.player.durationChanged.connect(self._on_duration)
        self.player.playbackStateChanged.connect(self._on_state)

    def set_file(self, file: IFile):
        self.unload_file()
        data = file.data
        info = probe(data)

        if info.is_bank:
            raise ValueError(
                "This entry is a Wwise bank container (BKHD), not a playable .wem."
            )

        target = "ogg" if info.codec == "vorbis" else (
            "opus" if info.codec == "opus" else "wav"
        )
        try:
            converted = convert(data, target)
        except ValueError as exc:
            raise ValueError(f"Cannot decode this .wem: {exc}") from exc

        self._temp_dir = tempfile.mkdtemp(prefix="neoxtractor_audio_")
        path = os.path.join(self._temp_dir, "preview." + target)
        with open(path, "wb") as handle:
            handle.write(converted)

        self._file = file
        self._info = info
        stem = os.path.splitext(file.basename)[0] or "audio"
        self._export_name = f"{stem}.{target}"

        self.info_label.setText(
            "Codec: %s | %d ch | %d Hz | %.2f s"
            % (
                info.codec.upper(),
                info.channels,
                info.sample_rate,
                info.duration_samples / info.sample_rate
                if info.sample_rate
                else 0.0,
            )
        )
        self.status_label.setText(
            f"Decoded to {target.upper()} ({len(converted):,} bytes)."
        )

        self.player.setSource(QtCore.QUrl.fromLocalFile(path))
        self.play_button.setEnabled(True)
        self.stop_button.setEnabled(True)
        self.position_slider.setEnabled(True)
        self.export_button.setEnabled(True)

    def unload_file(self):
        self.player.stop()
        self.player.setSource(QtCore.QUrl())
        self.play_button.setText("Play")
        for widget in (self.play_button, self.stop_button, self.position_slider):
            widget.setEnabled(False)
        self.export_button.setEnabled(False)
        self._file = None
        self._info = None
        self._schedule_temp_cleanup()

    def _schedule_temp_cleanup(self):
        """Delete the preview directory once the backend released its file handle."""
        temp_dir = self._temp_dir
        self._temp_dir = None
        if not temp_dir:
            return
        QtCore.QTimer.singleShot(
            500, lambda: shutil.rmtree(temp_dir, ignore_errors=True)
        )

    def closeEvent(self, event):
        self.unload_file()
        super().closeEvent(event)

    def _toggle_play(self):
        if self.player.playbackState() == QtMultimedia.QMediaPlayer.PlaybackState.PlayingState:
            self.player.pause()
        else:
            self.player.play()

    def _stop(self):
        self.player.stop()

    def _begin_seek(self):
        self._seeking = True

    def _end_seek(self):
        self.player.setPosition(self.position_slider.value())
        self._seeking = False

    def _set_volume(self, value: int):
        self.audio_output.setVolume(value / 100.0)

    def _on_position(self, position: int):
        if not self._seeking:
            self.position_slider.setValue(position)
        duration = self.player.duration()
        self.time_label.setText(
            f"{self._format_time(position)} / {self._format_time(duration)}"
        )

    def _on_duration(self, duration: int):
        self.position_slider.setRange(0, max(0, duration))
        self.time_label.setText(
            f"{self._format_time(0)} / {self._format_time(duration)}"
        )

    def _on_state(self, state):
        if state == QtMultimedia.QMediaPlayer.PlaybackState.PlayingState:
            self.play_button.setText("Pause")
        else:
            self.play_button.setText("Play")

    @staticmethod
    def _format_time(milliseconds: int) -> str:
        seconds = max(0, milliseconds // 1000)
        return f"{seconds // 60}:{seconds % 60:02d}"

    def export_audio(self):
        if self._file is None or self._info is None:
            return
        filters = EXPORT_TARGETS.get(self._info.codec)
        if not filters:
            return
        default = os.path.join(os.path.expanduser("~"), self._export_name)
        chosen, selected_filter = QtWidgets.QFileDialog.getSaveFileName(
            self, "Export audio", default, ";;".join(item[0] for item in filters)
        )
        if not chosen:
            return

        target = filters[-1][1]
        for text, suffix in filters:
            if text == selected_filter:
                target = suffix
                break

        base, _ = os.path.splitext(chosen)
        path = base + "." + target
        try:
            converted = convert(self._file.data, target)
        except ValueError as exc:
            self.status_label.setText(f"Export failed: {exc}")
            return
        with open(path, "wb") as handle:
            handle.write(converted)
        self.status_label.setText(f"Exported {os.path.basename(path)}")
