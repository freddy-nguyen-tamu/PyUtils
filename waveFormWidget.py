#!/usr/bin/env python3
"""
MP3 Trimmer – final version
- Triangle handles point according to mode
- Comfortable waveform margins
- No button focus (space always toggles play)
- Arrow keys fine‑tune handle position (±0.05 s) when handle is selected
- Fast in‑memory preview (no ffmpeg during editing)
"""

import sys
import os
import tempfile
import subprocess
import wave
import numpy as np
from PyQt5 import QtCore, QtGui, QtWidgets, QtMultimedia


# ----------------------------------------------------------------------
# Utility: time formatting
# ----------------------------------------------------------------------
def format_time(seconds):
    if seconds < 0:
        seconds = 0
    mins = int(seconds // 60)
    secs = seconds % 60
    return f"{mins:02d}:{secs:05.1f}"


# ----------------------------------------------------------------------
# ffmpeg helpers (used only for initial MP3→WAV conversion and final save)
# ----------------------------------------------------------------------
def run_ffmpeg(args, check=True):
    cmd = ['ffmpeg', '-hide_banner', '-loglevel', 'error'] + args
    return subprocess.run(cmd, check=check, capture_output=True, text=True)


def get_mp3_duration(filepath):
    cmd = [
        'ffprobe', '-v', 'error', '-show_entries',
        'format=duration', '-of', 'default=noprint_wrappers=1:nokey=1',
        filepath
    ]
    return float(subprocess.run(cmd, capture_output=True, text=True).stdout.strip())


def mp3_to_wav(filepath, out_wav):
    run_ffmpeg(['-i', filepath, '-ac', '1', '-ar', '44100', out_wav, '-y'])


# ----------------------------------------------------------------------
# Waveform peaks (computed once per file)
# ----------------------------------------------------------------------
def get_waveform_peaks(samples, target_width):
    """samples : float32 array normalized -1..1"""
    if len(samples) == 0:
        return [], 1.0
    bins = np.array_split(samples, target_width)
    peaks = []
    global_max = 0.0
    for chunk in bins:
        if len(chunk) == 0:
            peaks.append((0.0, 0.0))
            continue
        pos = float(chunk.max()) if chunk.max() > 0 else 0.0
        neg = float(chunk.min()) if chunk.min() < 0 else 0.0
        peaks.append((pos, neg))
        if pos > global_max: global_max = pos
        if -neg > global_max: global_max = -neg
    return peaks, global_max


# ----------------------------------------------------------------------
# Fast preview audio creation (pure numpy slicing)
# ----------------------------------------------------------------------
def create_preview_wav(samples, sr, start_sec, end_sec, keep, out_path):
    """
    samples : 1D int16 numpy array (original mono audio)
    sr      : sample rate
    Writes a WAV file representing the trimmed preview.
    """
    total = len(samples)
    start_idx = int(start_sec * sr)
    end_idx = int(end_sec * sr)
    start_idx = max(0, min(start_idx, total))
    end_idx = max(0, min(end_idx, total))

    if keep:
        cut = samples[start_idx:end_idx]
    else:
        cut = np.concatenate((samples[:start_idx], samples[end_idx:]))

    with wave.open(out_path, 'wb') as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)          # 16‑bit
        wf.setframerate(sr)
        wf.writeframes(cut.tobytes())


# ----------------------------------------------------------------------
# Waveform widget – triangle handles, click‑to‑focus for keyboard
# ----------------------------------------------------------------------
class WaveformWidget(QtWidgets.QWidget):
    leftHandleMoved = QtCore.pyqtSignal(float)
    rightHandleMoved = QtCore.pyqtSignal(float)
    playCursorMoved = QtCore.pyqtSignal(float)
    cursorClicked = QtCore.pyqtSignal(float)
    handleClicked = QtCore.pyqtSignal(str)        # 'left' or 'right'
    handleFocusLost = QtCore.pyqtSignal()         # click elsewhere → no handle focused
    dragFinished = QtCore.pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMouseTracking(True)
        self.setFocusPolicy(QtCore.Qt.ClickFocus)   # receive clicks for keyboard focus
        self.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Expanding)
        self.setMinimumHeight(120)
        self.duration = 0.0
        self.peaks = []
        self.max_abs = 1.0
        self.left_frac = 0.0
        self.right_frac = 1.0
        self.cursor_frac = 0.0
        self.keep_mode = True
        self.dragging = None
        self.handle_extend = 16          # parabola bulge width at its widest point
        self.handle_width = 28           # clickable region around the line
        self.cursor_color = QtGui.QColor(255, 200, 0)

    def set_audio_data(self, dur, peaks, max_abs):
        self.duration = dur
        self.peaks = peaks
        self.max_abs = max(max_abs, 1e-6)
        self.left_frac = 0.0
        self.right_frac = 1.0
        self.cursor_frac = 0.0
        self.update()
        self._emit_all_times()

    def set_keep_mode(self, keep):
        self.keep_mode = keep
        self.update()

    def set_left_time(self, sec):
        frac = max(0.0, min(sec / self.duration, self.right_frac - 0.001))
        if frac != self.left_frac:
            self.left_frac = frac
            self.update()
            self.leftHandleMoved.emit(self.left_frac * self.duration)

    def set_right_time(self, sec):
        frac = max(self.left_frac + 0.001, min(sec / self.duration, 1.0))
        if frac != self.right_frac:
            self.right_frac = frac
            self.update()
            self.rightHandleMoved.emit(self.right_frac * self.duration)

    def set_cursor_time(self, sec):
        frac = sec / self.duration
        if not self.keep_mode:
            if self.left_frac < frac < self.right_frac:
                frac = self.left_frac if (frac - self.left_frac) <= (self.right_frac - frac) else self.right_frac
        frac = max(0.0, min(frac, 1.0))
        self.cursor_frac = frac
        self.update()
        self.playCursorMoved.emit(self.cursor_frac * self.duration)

    # ------------------------------------------------------------------
    # Handle shape: a parabola whose two ends sit on the vertical line
    # (at y=0 and y=h) and which bulges out to the tip at the midpoint,
    # giving a smooth rounded shape instead of a sharp triangle.
    # ------------------------------------------------------------------
    def _handle_path(self, line_x, h, direction):
        path = QtGui.QPainterPath()
        path.moveTo(line_x, 0)
        steps = 24
        half = h / 2.0
        for i in range(1, steps + 1):
            y = h * i / steps
            t = (y - half) / half            # -1 .. 1
            bulge = self.handle_extend * (1 - t * t)
            path.lineTo(line_x + direction * bulge, y)
        path.closeSubpath()
        return path

    # ------------------------------------------------------------------
    # Coordinate mapping
    # ------------------------------------------------------------------
    def _frac_from_x(self, x):
        return max(0.0, min(x / self.width(), 1.0))

    def _x_from_frac(self, f):
        return int(f * self.width())

    # ------------------------------------------------------------------
    # Painting
    # ------------------------------------------------------------------
    def paintEvent(self, event):
        if not self.peaks or self.duration == 0:
            return
        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.Antialiasing, False)
        w, h = self.width(), self.height()
        painter.fillRect(0, 0, w, h, QtGui.QColor(20, 40, 60))

        rect = event.rect()
        x_start, x_end = max(0, rect.left()), min(w - 1, rect.right())
        center_y = h / 2
        scale = (h / 2 - 8) / self.max_abs
        peak_color = QtGui.QColor(120, 180, 220)
        for x in range(x_start, x_end + 1):
            if x < len(self.peaks):
                pos, neg = self.peaks[x]
            else:
                pos, neg = 0.0, 0.0
            y_top = int(center_y - pos * scale)
            y_bot = int(center_y - neg * scale)
            if y_top > y_bot: y_top, y_bot = y_bot, y_top
            painter.fillRect(x, y_top, 1, max(1, y_bot - y_top), peak_color)

        left_x = self._x_from_frac(self.left_frac)
        right_x = self._x_from_frac(self.right_frac)

        # Region highlight
        region_color = QtGui.QColor(0, 200, 0, 45) if self.keep_mode else QtGui.QColor(200, 50, 50, 55)
        painter.fillRect(left_x, 0, right_x - left_x, h, region_color)

        # Parabolic handles (rounded "leaf" shape instead of a sharp triangle)
        painter.setRenderHint(QtGui.QPainter.Antialiasing, True)
        painter.setPen(QtCore.Qt.NoPen)
        handle_color = QtGui.QColor(255, 20, 147, 235)   # vivid pink/magenta – stands out from the blue waveform

        # Left handle
        painter.setBrush(handle_color)
        left_dir = -1 if self.keep_mode else 1     # points left in keep mode, right in remove mode
        painter.drawPath(self._handle_path(left_x, h, left_dir))

        # Right handle
        right_dir = 1 if self.keep_mode else -1    # points right in keep mode, left in remove mode
        painter.drawPath(self._handle_path(right_x, h, right_dir))
        painter.setRenderHint(QtGui.QPainter.Antialiasing, False)

        # Precise vertical lines on handles
        painter.setPen(QtGui.QPen(QtGui.QColor(255, 255, 255, 200), 2))
        painter.drawLine(left_x, 0, left_x, h)
        painter.drawLine(right_x, 0, right_x, h)

        # Play cursor
        cursor_x = self._x_from_frac(self.cursor_frac)
        painter.setPen(QtGui.QPen(self.cursor_color, 2))
        painter.drawLine(cursor_x, 0, cursor_x, h)
        tri = 8
        painter.setBrush(self.cursor_color)
        painter.drawPolygon(
            QtCore.QPoint(cursor_x, 0),
            QtCore.QPoint(cursor_x - tri, -tri + 1),
            QtCore.QPoint(cursor_x + tri, -tri + 1)
        )
        painter.end()

    # ------------------------------------------------------------------
    # Mouse events
    # ------------------------------------------------------------------
    def mousePressEvent(self, event):
        if event.button() != QtCore.Qt.LeftButton:
            return
        x = event.x()
        left_x = self._x_from_frac(self.left_frac)
        right_x = self._x_from_frac(self.right_frac)

        if left_x - self.handle_width//2 <= x <= left_x + self.handle_width//2:
            self.dragging = 'left'
            self.setCursor(QtCore.Qt.SizeHorCursor)
            self.handleClicked.emit('left')          # keyboard focus on left handle
            self.setFocus()                           # accept keyboard events
            return
        if right_x - self.handle_width//2 <= x <= right_x + self.handle_width//2:
            self.dragging = 'right'
            self.setCursor(QtCore.Qt.SizeHorCursor)
            self.handleClicked.emit('right')
            self.setFocus()
            return

        # Click elsewhere → move play cursor, lose handle focus
        self.handleFocusLost.emit()
        self.clearFocus()
        frac = self._frac_from_x(x)
        if not self.keep_mode and self.left_frac < frac < self.right_frac:
            frac = self.left_frac if (frac - self.left_frac) <= (self.right_frac - frac) else self.right_frac
        frac = max(0.0, min(frac, 1.0))
        self.cursor_frac = frac
        self.update()
        self.playCursorMoved.emit(self.cursor_frac * self.duration)
        self.cursorClicked.emit(self.cursor_frac * self.duration)

    def mouseMoveEvent(self, event):
        if self.dragging is None:
            return
        frac = self._frac_from_x(event.x())
        if self.dragging == 'left':
            new = max(0.0, min(frac, self.right_frac - 0.001))
            if new != self.left_frac:
                self.left_frac = new
                self.update()
                self.leftHandleMoved.emit(self.left_frac * self.duration)
        elif self.dragging == 'right':
            new = max(self.left_frac + 0.001, min(frac, 1.0))
            if new != self.right_frac:
                self.right_frac = new
                self.update()
                self.rightHandleMoved.emit(self.right_frac * self.duration)

    def mouseReleaseEvent(self, event):
        if self.dragging is not None:
            self.dragFinished.emit(self.dragging)
        self.dragging = None
        self.setCursor(QtCore.Qt.ArrowCursor)

    def _emit_all_times(self):
        self.leftHandleMoved.emit(self.left_frac * self.duration)
        self.rightHandleMoved.emit(self.right_frac * self.duration)
        self.playCursorMoved.emit(self.cursor_frac * self.duration)


# ----------------------------------------------------------------------
# Main window
# ----------------------------------------------------------------------
class MP3Trimmer(QtWidgets.QMainWindow):
    FINE_STEP = 0.05                 # seconds per arrow key

    def __init__(self):
        super().__init__()
        self.setWindowTitle("MP3 Trimmer")

        # Size the window relative to the available screen so the whole
        # workspace can be centered nicely instead of stretching edge to edge.
        screen = QtWidgets.QApplication.primaryScreen().availableGeometry()
        win_w = int(screen.width() * 0.6)
        win_h = int(screen.height() * 0.75)
        self.resize(win_w, win_h)
        self.setMinimumSize(850, 380)
        frame = self.frameGeometry()
        frame.moveCenter(screen.center())
        self.move(frame.topLeft())

        # Audio data (raw int16 samples for fast preview)
        self.audio_samples = None
        self.audio_rate = 0
        self.duration = 0.0

        self.original_file = ""
        self.temp_preview_wav = None
        self.cached_left = -1.0
        self.cached_right = -1.0
        self.cached_keep = True

        self.media_player = QtMultimedia.QMediaPlayer(self)
        self.media_player.setNotifyInterval(50)
        self.media_player.positionChanged.connect(self.on_playback_position)
        self.is_playing = False

        self.active_handle = None      # 'left' or 'right' when selected for arrow keys

        self._init_ui()
        self._check_ffmpeg()

    def _check_ffmpeg(self):
        try:
            subprocess.run(['ffmpeg', '-version'], capture_output=True, check=True)
        except:
            QtWidgets.QMessageBox.warning(self, "Missing FFmpeg",
                "FFmpeg is not found in PATH.\nhttps://ffmpeg.org/download.html")

    def _init_ui(self):
        central = QtWidgets.QWidget()
        self.setCentralWidget(central)
        central.setStyleSheet("background: #0D233A; color: white; font-family: Segoe UI;")

        # Outer layout: stretch / content / stretch so the workspace sits
        # vertically centered and only occupies roughly two thirds of the
        # window's height, instead of filling it edge to edge.
        outer_layout = QtWidgets.QVBoxLayout(central)
        outer_layout.setContentsMargins(0, 0, 0, 0)
        outer_layout.setSpacing(0)

        content = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(content)
        layout.setContentsMargins(10, 8, 10, 8)
        layout.setSpacing(4)

        outer_layout.addStretch(1)
        outer_layout.addWidget(content, 4)   # 4 / (1+4+1) = 2/3 of the height
        outer_layout.addStretch(1)

        # ---------- Top row ----------
        top = QtWidgets.QHBoxLayout()
        btn_style = "background: #1A3A5C; border: none; border-radius: 4px; padding: 6px 12px; color: white; font-weight: bold;"
        btn_open = QtWidgets.QPushButton("Open MP3")
        btn_open.clicked.connect(self.open_file)
        btn_open.setFocusPolicy(QtCore.Qt.NoFocus)
        btn_open.setStyleSheet(btn_style)
        top.addWidget(btn_open)

        self.btn_keep = QtWidgets.QPushButton("Keep")
        self.btn_keep.setCheckable(True)
        self.btn_keep.setChecked(True)
        self.btn_keep.setFocusPolicy(QtCore.Qt.NoFocus)
        self.btn_keep.setStyleSheet("""
            QPushButton { background: #2A5A3A; border: none; border-radius: 4px; padding: 6px 12px; color: white; font-weight: bold; }
            QPushButton:checked { background: #3A7A4A; }
        """)
        self.btn_remove = QtWidgets.QPushButton("Remove")
        self.btn_remove.setCheckable(True)
        self.btn_remove.setFocusPolicy(QtCore.Qt.NoFocus)
        self.btn_remove.setStyleSheet("""
            QPushButton { background: #5A2A2A; border: none; border-radius: 4px; padding: 6px 12px; color: white; font-weight: bold; }
            QPushButton:checked { background: #7A3A3A; }
        """)
        self.btn_keep.clicked.connect(lambda: self.set_mode(True))
        self.btn_remove.clicked.connect(lambda: self.set_mode(False))
        top.addWidget(self.btn_keep)
        top.addWidget(self.btn_remove)
        top.addStretch()
        btn_reset = QtWidgets.QPushButton("Reset")
        btn_reset.clicked.connect(self.reset_trim)
        btn_reset.setFocusPolicy(QtCore.Qt.NoFocus)
        btn_reset.setStyleSheet(btn_style)
        top.addWidget(btn_reset)
        btn_save = QtWidgets.QPushButton("Save")
        btn_save.clicked.connect(self.save_file)
        btn_save.setFocusPolicy(QtCore.Qt.NoFocus)
        btn_save.setStyleSheet("background: #2A5A8A; border: none; border-radius: 4px; padding: 6px 18px; color: white; font-weight: bold;")
        top.addWidget(btn_save)
        layout.addLayout(top)

        # ---------- Waveform (with horizontal margins) ----------
        self.waveform = WaveformWidget()
        self.waveform.leftHandleMoved.connect(lambda t: self.edit_left.setText(format_time(t)))
        self.waveform.rightHandleMoved.connect(lambda t: self.edit_right.setText(format_time(t)))
        self.waveform.cursorClicked.connect(self.on_waveform_clicked)
        self.waveform.handleClicked.connect(self.on_handle_focused)
        self.waveform.handleFocusLost.connect(self.on_handle_focus_lost)
        self.waveform.dragFinished.connect(self.on_handle_drag_finished)

        wave_container = QtWidgets.QWidget()
        wave_container.setStyleSheet("background: transparent;")
        wave_layout = QtWidgets.QHBoxLayout(wave_container)
        # Noticeable margins on either side so the waveform doesn't touch
        # the edges of the window, while still using most of the width.
        wave_layout.setContentsMargins(60, 0, 60, 0)
        wave_layout.addWidget(self.waveform)
        # Stretch factor of 1 lets the waveform grow to fill most of the
        # leftover vertical space, while the small spacing added below
        # keeps a bit of breathing room from the button rows.
        layout.addSpacing(14)
        layout.addWidget(wave_container, 1)
        layout.addSpacing(14)

        # ---------- Bottom row ----------
        bottom = QtWidgets.QHBoxLayout()
        self.btn_play = QtWidgets.QPushButton("▶")
        self.btn_play.setFixedSize(46, 46)
        self.btn_play.clicked.connect(self.toggle_play)
        self.btn_play.setFocusPolicy(QtCore.Qt.NoFocus)
        self.btn_play.setStyleSheet("""
            QPushButton { background: #1A3A5C; border: none; border-radius: 23px; font-size: 22px; color: white; }
            QPushButton:hover { background: #2A5A8A; }
        """)
        btn_stop = QtWidgets.QPushButton("■")
        btn_stop.setFixedSize(46, 46)
        btn_stop.clicked.connect(self.stop)
        btn_stop.setFocusPolicy(QtCore.Qt.NoFocus)
        btn_stop.setStyleSheet("""
            QPushButton { background: #1A3A5C; border: none; border-radius: 23px; font-size: 22px; color: white; }
            QPushButton:hover { background: #2A5A8A; }
        """)
        bottom.addWidget(self.btn_play)
        bottom.addWidget(btn_stop)
        bottom.addSpacing(12)

        self.edit_left = QtWidgets.QLineEdit("00:00.0")
        self.edit_left.setFixedWidth(70)
        self.edit_left.setAlignment(QtCore.Qt.AlignCenter)
        self.edit_left.editingFinished.connect(self.on_left_time_edited)
        self.edit_left.setStyleSheet("background: #1A2A40; border: none; border-radius: 3px; padding: 4px; color: white;")
        bottom.addWidget(QtWidgets.QLabel("Start:"))
        bottom.addWidget(self.edit_left)

        self.edit_right = QtWidgets.QLineEdit("00:00.0")
        self.edit_right.setFixedWidth(70)
        self.edit_right.setAlignment(QtCore.Qt.AlignCenter)
        self.edit_right.editingFinished.connect(self.on_right_time_edited)
        self.edit_right.setStyleSheet("background: #1A2A40; border: none; border-radius: 3px; padding: 4px; color: white;")
        bottom.addWidget(QtWidgets.QLabel("End:"))
        bottom.addWidget(self.edit_right)
        bottom.addStretch()
        layout.addLayout(bottom)

        self.setFocusPolicy(QtCore.Qt.StrongFocus)   # main window handles space

    # ------------------------------------------------------------------
    # Keyboard: space toggles play, arrows fine‑tune active handle
    # ------------------------------------------------------------------
    def keyPressEvent(self, event):
        # Space – only if no text field is focused
        if event.key() == QtCore.Qt.Key_Space and not event.isAutoRepeat():
            if not isinstance(self.focusWidget(), QtWidgets.QLineEdit):
                self.toggle_play()
                return

        # Arrow keys – fine‑tune the selected handle
        if event.key() in (QtCore.Qt.Key_Left, QtCore.Qt.Key_Right) and self.active_handle is not None:
            if isinstance(self.focusWidget(), QtWidgets.QLineEdit):
                return   # don't steal from time edits
            delta = -self.FINE_STEP if event.key() == QtCore.Qt.Key_Left else self.FINE_STEP
            self._nudge_handle(self.active_handle, delta)
            return

        super().keyPressEvent(event)

    def _nudge_handle(self, handle, delta):
        """Move the given handle by delta seconds, respecting limits."""
        if self.audio_samples is None:
            return
        if handle == 'left':
            new_time = max(0.0, self.waveform.left_frac * self.duration + delta)
            new_time = min(new_time, self.waveform.right_frac * self.duration - 0.001)
            self.waveform.set_left_time(new_time)
        else:   # 'right'
            new_time = max(self.waveform.left_frac * self.duration + 0.001,
                           self.waveform.right_frac * self.duration + delta)
            new_time = min(new_time, self.duration)
            self.waveform.set_right_time(new_time)

        # After moving, apply the same cursor logic as when the drag finishes
        self._apply_handle_drag_logic(handle)

    def _apply_handle_drag_logic(self, handle):
        """Recalculate cursor position after a handle move and restart playback if needed."""
        if self.audio_samples is None:
            return
        keep = self.btn_keep.isChecked()
        left = self.waveform.left_frac * self.duration
        right = self.waveform.right_frac * self.duration

        if handle == 'left':
            if keep:
                new_cursor = left
            else:
                if left < 0.001:
                    new_cursor = right
                else:
                    new_cursor = max(0.0, left - 3.0)
        else:   # 'right'
            if keep:
                cur = self.waveform.cursor_frac * self.duration
                new_cursor = min(cur, right)
            else:
                new_cursor = right

        self.waveform.set_cursor_time(new_cursor)
        if self.media_player.state() == QtMultimedia.QMediaPlayer.PlayingState:
            self.restart_playback()

    # ------------------------------------------------------------------
    # Handle focus tracking
    # ------------------------------------------------------------------
    def on_handle_focused(self, handle):
        self.active_handle = handle

    def on_handle_focus_lost(self):
        self.active_handle = None

    # ------------------------------------------------------------------
    # Mode switch
    # ------------------------------------------------------------------
    def set_mode(self, keep):
        self.btn_keep.setChecked(keep)
        self.btn_remove.setChecked(not keep)
        self.waveform.set_keep_mode(keep)
        self.cached_left = -1.0
        if self.media_player.state() == QtMultimedia.QMediaPlayer.PlayingState:
            self.media_player.stop()
            self.is_playing = False
            self.update_play_button()

    # ------------------------------------------------------------------
    # File loading
    # ------------------------------------------------------------------
    def open_file(self):
        path, _ = QtWidgets.QFileDialog.getOpenFileName(self, "Open MP3", "", "MP3 files (*.mp3);;All files (*)")
        if not path:
            return
        try:
            dur = get_mp3_duration(path)
            fd, wav_path = tempfile.mkstemp(suffix='.wav')
            os.close(fd)
            mp3_to_wav(path, wav_path)
            with wave.open(wav_path, 'rb') as wf:
                assert wf.getnchannels() == 1, "expected mono"
                sw = wf.getsampwidth()
                framerate = wf.getframerate()
                nframes = wf.getnframes()
                frames = wf.readframes(nframes)
            dtype = np.int16 if sw == 2 else np.int32
            self.audio_samples = np.frombuffer(frames, dtype=dtype).copy()
            self.audio_rate = framerate
            self.duration = dur

            max_val = 2 ** (8 * sw - 1)
            float_samples = self.audio_samples.astype(np.float32) / max_val
            widget_width = max(1, self.waveform.width())
            peaks, max_abs = get_waveform_peaks(float_samples, widget_width)

            self.original_file = path
            self.waveform.set_audio_data(dur, peaks, max_abs)
            os.unlink(wav_path)
            self.stop()
        except Exception as e:
            QtWidgets.QMessageBox.critical(self, "Error", f"Could not load MP3:\n{e}")

    # ------------------------------------------------------------------
    # Reset
    # ------------------------------------------------------------------
    def reset_trim(self):
        if self.audio_samples is None:
            return
        self.waveform.set_left_time(0.0)
        self.waveform.set_right_time(self.duration)
        self.waveform.set_cursor_time(0.0)
        self.stop()

    # ------------------------------------------------------------------
    # Waveform click → seek during playback
    # ------------------------------------------------------------------
    def on_waveform_clicked(self, orig_time):
        if self.media_player.state() == QtMultimedia.QMediaPlayer.PlayingState:
            temp_time = self._original_to_temp_time(orig_time)
            self.media_player.setPosition(int(temp_time * 1000))

    # ------------------------------------------------------------------
    # Handle drag finished (same logic as above)
    # ------------------------------------------------------------------
    def on_handle_drag_finished(self, handle):
        if self.audio_samples is None:
            return
        # cursor update and playback restart
        self._apply_handle_drag_logic(handle)

    # ------------------------------------------------------------------
    # Playback restart using fast in‑memory preview
    # ------------------------------------------------------------------
    def restart_playback(self):
        self.media_player.stop()
        if self.audio_samples is None:
            return
        preview_path = self._get_preview_wav_path()
        if not preview_path:
            return
        orig_time = self.waveform.cursor_frac * self.duration
        temp_time = self._original_to_temp_time(orig_time)
        self.media_player.setMedia(
            QtMultimedia.QMediaContent(QtCore.QUrl.fromLocalFile(preview_path))
        )
        self._pending_seek = True
        self._pending_seek_temp_time = temp_time
        self.media_player.mediaStatusChanged.connect(self._on_media_loaded_restart)

    def _on_media_loaded_restart(self, status):
        if status == QtMultimedia.QMediaPlayer.LoadedMedia and getattr(self, '_pending_seek', False):
            self._pending_seek = False
            self.media_player.mediaStatusChanged.disconnect(self._on_media_loaded_restart)
            self.media_player.setPosition(int(self._pending_seek_temp_time * 1000))
            self.media_player.play()
            self.is_playing = True
            self.update_play_button()

    def _get_preview_wav_path(self):
        left = self.waveform.left_frac * self.duration
        right = self.waveform.right_frac * self.duration
        keep = self.btn_keep.isChecked()
        if (self.temp_preview_wav and
            abs(self.cached_left - left) < 1e-6 and
            abs(self.cached_right - right) < 1e-6 and
            self.cached_keep == keep):
            return self.temp_preview_wav
        if self.temp_preview_wav and os.path.exists(self.temp_preview_wav):
            os.unlink(self.temp_preview_wav)
        fd, path = tempfile.mkstemp(suffix='.wav')
        os.close(fd)
        try:
            create_preview_wav(self.audio_samples, self.audio_rate,
                               left, right, keep, path)
        except Exception as e:
            QtWidgets.QMessageBox.critical(self, "Error", f"Preview generation failed:\n{e}")
            return ""
        self.temp_preview_wav = path
        self.cached_left, self.cached_right, self.cached_keep = left, right, keep
        return path

    # ------------------------------------------------------------------
    # Time mapping
    # ------------------------------------------------------------------
    def _original_to_temp_time(self, orig):
        left = self.waveform.left_frac * self.duration
        right = self.waveform.right_frac * self.duration
        keep = self.btn_keep.isChecked()
        if keep:
            return max(0.0, min(orig - left, right - left))
        else:
            if orig < left:
                return orig
            if orig >= right:
                return orig - (right - left)
            return left

    def _temp_to_original_time(self, temp):
        left = self.waveform.left_frac * self.duration
        right = self.waveform.right_frac * self.duration
        keep = self.btn_keep.isChecked()
        if keep:
            return left + temp
        else:
            if temp < left:
                return temp
            return temp + (right - left)

    # ------------------------------------------------------------------
    # Play / Pause / Stop
    # ------------------------------------------------------------------
    def toggle_play(self):
        if self.audio_samples is None:
            self.open_file()
            return
        if self.media_player.state() == QtMultimedia.QMediaPlayer.PlayingState:
            self.media_player.pause()
            self.is_playing = False
        elif self.media_player.state() == QtMultimedia.QMediaPlayer.PausedState:
            self.media_player.play()
            self.is_playing = True
        else:
            self.restart_playback()
        self.update_play_button()

    def stop(self):
        self.media_player.stop()
        self.is_playing = False
        self.update_play_button()

    def update_play_button(self):
        self.btn_play.setText("⏸" if self.is_playing else "▶")

    def on_playback_position(self, pos_ms):
        if self.duration == 0:
            return
        temp = pos_ms / 1000.0
        orig = self._temp_to_original_time(temp)
        self.waveform.cursor_frac = orig / self.duration
        self.waveform.update()
        if pos_ms >= self.media_player.duration() > 0:
            self.is_playing = False
            self.update_play_button()

    # ------------------------------------------------------------------
    # Time edits
    # ------------------------------------------------------------------
    def on_left_time_edited(self):
        t = self._parse_time(self.edit_left.text())
        if t is not None and self.duration > 0:
            self.waveform.set_left_time(min(t, self.duration))

    def on_right_time_edited(self):
        t = self._parse_time(self.edit_right.text())
        if t is not None and self.duration > 0:
            self.waveform.set_right_time(min(t, self.duration))

    @staticmethod
    def _parse_time(txt):
        try:
            parts = txt.split(':')
            if len(parts) != 2: return None
            return int(parts[0]) * 60 + float(parts[1])
        except:
            return None

    # ------------------------------------------------------------------
    # Save
    # ------------------------------------------------------------------
    def save_file(self):
        if self.audio_samples is None:
            return
        path, _ = QtWidgets.QFileDialog.getSaveFileName(self, "Save", "", "MP3 (*.mp3)")
        if not path:
            return
        preview = self._get_preview_wav_path()
        if not preview:
            return
        try:
            run_ffmpeg(['-i', preview, '-codec:a', 'libmp3lame', '-qscale:a', '2', path, '-y'])
            self.statusBar().showMessage("Saved", 3000)
        except Exception as e:
            QtWidgets.QMessageBox.critical(self, "Error", f"Save failed:\n{e}")

    def closeEvent(self, event):
        self.media_player.stop()
        if self.temp_preview_wav and os.path.exists(self.temp_preview_wav):
            os.unlink(self.temp_preview_wav)
        super().closeEvent(event)


if __name__ == "__main__":
    app = QtWidgets.QApplication(sys.argv)
    win = MP3Trimmer()
    win.show()
    sys.exit(app.exec_())