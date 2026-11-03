"""Playable NewBasis ``.nbs`` viewer (VP9 frames packed as IVF, optional MP3)."""

from __future__ import annotations

import os
import shutil
import tempfile

from PySide6 import QtCore, QtMultimedia, QtMultimediaWidgets, QtWidgets

from core.file import IFile
from core.newbasis import parse, to_ivf, to_mkv
from gui.widgets.viewer import Viewer


class NbsViewer(Viewer):
    """Unpack a ``.nbs`` archive into IVF and play it back."""

    name = "NBS Viewer"
    accepted_extensions = {"nbs"}

    def __init__(self, parent: QtWidgets.QWidget | None = None):
        super().__init__(parent)

        self._file: IFile | None = None
        self._video = None
        self._temp_dir: str | None = None
        self._export_name = "video.ivf"
        self._seeking = False
        self._audio_path: str | None = None

        layout = QtWidgets.QVBoxLayout(self)

        self.info_label = QtWidgets.QLabel("No video loaded.")
        self.info_label.setWordWrap(True)
        layout.addWidget(self.info_label)

        self.video_widget = QtMultimediaWidgets.QVideoWidget(self)
        self.video_widget.setMinimumHeight(240)
        layout.addWidget(self.video_widget, stretch=1)

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
        self.export_button = QtWidgets.QPushButton("Export MKV...")
        self.export_button.setEnabled(False)
        self.export_button.clicked.connect(self.export_mkv)
        export_row.addWidget(self.export_button)

        self.ivf_button = QtWidgets.QPushButton("Export IVF...")
        self.ivf_button.setEnabled(False)
        self.ivf_button.clicked.connect(self.export_video)
        export_row.addWidget(self.ivf_button)

        self.audio_button = QtWidgets.QPushButton("Export Audio...")
        self.audio_button.setEnabled(False)
        self.audio_button.clicked.connect(self.export_audio)
        export_row.addWidget(self.audio_button)
        export_row.addStretch(1)
        layout.addLayout(export_row)

        self.status_label = QtWidgets.QLabel("")
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)

        self.player = QtMultimedia.QMediaPlayer(self)
        self.audio_output = QtMultimedia.QAudioOutput(self)
        self.player.setAudioOutput(self.audio_output)
        self.player.setVideoOutput(self.video_widget)
        self.player.positionChanged.connect(self._on_position)
        self.player.durationChanged.connect(self._on_duration)
        self.player.playbackStateChanged.connect(self._on_state)

        # IVF has no audio track, so the MP3 sidecar is played by a second player.
        self.audio_player = QtMultimedia.QMediaPlayer(self)
        self.track_output = QtMultimedia.QAudioOutput(self)
        self.audio_player.setAudioOutput(self.track_output)

    def set_file(self, file: IFile):
        self.unload_file()
        try:
            video = parse(file.data)
        except ValueError as exc:
            raise ValueError(f"Cannot open this .nbs file: {exc}") from exc

        self._temp_dir = tempfile.mkdtemp(prefix="neoxtractor_nbs_")
        ivf_path = os.path.join(self._temp_dir, "preview.ivf")
        with open(ivf_path, "wb") as handle:
            handle.write(to_ivf(video))

        self._audio_path = None
        if video.audio is not None:
            # The MP3 track is stored separately; playing video only keeps this simple.
            self._audio_path = os.path.join(self._temp_dir, "audio.mp3")
            with open(self._audio_path, "wb") as handle:
                handle.write(video.audio.mp3)

        self._file = file
        self._video = video
        stem = os.path.splitext(file.basename)[0] or "video"
        self._export_name = f"{stem}.ivf"

        audio_text = (
            f"MP3 {video.audio.sample_rate} Hz {video.audio.channels} ch "
            f"{video.audio.seconds:.2f} s"
            if video.audio
            else "none"
        )
        self.info_label.setText(
            f"VP9 {video.width}x{video.height} | {video.fps} fps | "
            f"{video.frame_count} frames | {video.duration_ms / 1000:.2f} s | "
            f"Audio: {audio_text}"
        )
        self.status_label.setText(f"Unpacked {video.frame_count} frames to IVF.")

        self.player.setSource(QtCore.QUrl.fromLocalFile(ivf_path))
        if self._audio_path:
            self.audio_player.setSource(QtCore.QUrl.fromLocalFile(self._audio_path))
        self.play_button.setEnabled(True)
        self.stop_button.setEnabled(True)
        self.position_slider.setEnabled(True)
        self.export_button.setEnabled(True)
        self.ivf_button.setEnabled(True)
        self.audio_button.setEnabled(video.audio is not None)

    def unload_file(self):
        self.player.stop()
        self.player.setSource(QtCore.QUrl())
        self.audio_player.stop()
        self.audio_player.setSource(QtCore.QUrl())
        self.play_button.setText("Play")
        for widget in (self.play_button, self.stop_button, self.position_slider):
            widget.setEnabled(False)
        self._file = None
        self._video = None
        self._audio_path = None
        self.export_button.setEnabled(False)
        self.ivf_button.setEnabled(False)
        self.audio_button.setEnabled(False)
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
            self.audio_player.pause()
        else:
            # A stopped player restarts from the beginning, not from its last position.
            state = self.player.playbackState()
            start = (
                0
                if state == QtMultimedia.QMediaPlayer.PlaybackState.StoppedState
                else self.player.position()
            )
            self._sync_audio(start, force=True)
            self.player.play()
            if self._audio_path:
                self.audio_player.play()

    def _stop(self):
        self.player.stop()
        self.audio_player.stop()

    def _begin_seek(self):
        self._seeking = True

    def _end_seek(self):
        position = self.position_slider.value()
        self.player.setPosition(position)
        self._sync_audio(position, force=True)
        self._seeking = False

    def _set_volume(self, value: int):
        volume = value / 100.0
        self.audio_output.setVolume(volume)
        self.track_output.setVolume(volume)

    def _sync_audio(self, position: int, force: bool = False):
        """Align the sidecar MP3 player with the video player when it drifts."""
        if not self._audio_path:
            return
        if force or abs(self.audio_player.position() - position) > 250:
            self.audio_player.setPosition(position)

    def _on_position(self, position: int):
        if not self._seeking:
            self.position_slider.setValue(position)
        self.time_label.setText(
            f"{self._format_time(position)} / {self._format_time(self.player.duration())}"
        )
        if not self._seeking:
            self._sync_audio(position)

    def _on_duration(self, duration: int):
        self.position_slider.setRange(0, max(0, duration))
        self.time_label.setText(f"{self._format_time(0)} / {self._format_time(duration)}")

    def _on_state(self, state):
        if state == QtMultimedia.QMediaPlayer.PlaybackState.PlayingState:
            self.play_button.setText("Pause")
        else:
            self.play_button.setText("Play")
            if (
                state == QtMultimedia.QMediaPlayer.PlaybackState.StoppedState
                and self.audio_player.playbackState()
                == QtMultimedia.QMediaPlayer.PlaybackState.PlayingState
            ):
                # The audio track can be longer than the video; stop it with the video.
                self.audio_player.stop()

    @staticmethod
    def _format_time(milliseconds: int) -> str:
        seconds = max(0, milliseconds // 1000)
        return f"{seconds // 60}:{seconds % 60:02d}"

    def export_mkv(self):
        """Save VP9 video and the original MP3 track as one Matroska file."""
        if self._file is None:
            return
        default = os.path.join(
            os.path.expanduser("~"), os.path.splitext(self._export_name)[0] + ".mkv"
        )
        chosen, _selected = QtWidgets.QFileDialog.getSaveFileName(
            self, "Export MKV", default, "Matroska video (*.mkv)"
        )
        if not chosen:
            return

        path = os.path.splitext(chosen)[0] + ".mkv"
        try:
            video = parse(self._file.data)
            content = to_mkv(video)
        except ValueError as exc:
            self.status_label.setText(f"Export failed: {exc}")
            return

        with open(path, "wb") as handle:
            handle.write(content)
        self.status_label.setText(f"Exported {os.path.basename(path)}")

    def export_audio(self):
        """Save the original MP3 track of the archive."""
        if self._file is None or self._video is None or self._video.audio is None:
            return
        default = os.path.join(
            os.path.expanduser("~"), os.path.splitext(self._export_name)[0] + ".mp3"
        )
        chosen, _selected = QtWidgets.QFileDialog.getSaveFileName(
            self, "Export Audio", default, "MP3 audio (*.mp3)"
        )
        if not chosen:
            return

        path = os.path.splitext(chosen)[0] + ".mp3"
        with open(path, "wb") as handle:
            handle.write(self._video.audio.mp3)
        self.status_label.setText(f"Exported {os.path.basename(path)}")

    def export_video(self):
        if self._file is None:
            return
        default = os.path.join(os.path.expanduser("~"), self._export_name)
        chosen, _selected = QtWidgets.QFileDialog.getSaveFileName(
            self, "Export IVF", default, "IVF video (*.ivf)"
        )
        if not chosen:
            return

        base, _ = os.path.splitext(chosen)
        ivf_path = base + ".ivf"
        try:
            video = parse(self._file.data)
            ivf = to_ivf(video)
        except ValueError as exc:
            self.status_label.setText(f"Export failed: {exc}")
            return

        with open(ivf_path, "wb") as handle:
            handle.write(ivf)

        message = f"Exported {os.path.basename(ivf_path)}"
        if video.audio is not None:
            message += " (IVF has no audio track)"
        self.status_label.setText(message)
