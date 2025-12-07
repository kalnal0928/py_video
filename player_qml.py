import sys
import os
from pathlib import Path
from PySide6 import QtCore, QtWidgets
from PySide6 import QtGui
from PySide6.QtCore import QUrl, Slot
import json
import shutil
import subprocess
import tempfile
import threading
from PySide6.QtQuickWidgets import QQuickWidget
# On Windows, allow explicit libvlc location via env var or common install paths
if sys.platform.startswith('win'):
    _env_path = os.environ.get('PY_VIDEO_LIBVLC')
    if _env_path and os.path.exists(_env_path):
        try:
            os.add_dll_directory(_env_path)
        except Exception:
            os.environ['PATH'] = _env_path + os.pathsep + os.environ.get('PATH', '')
    else:
        _possible_vlc_paths = [r"C:\Program Files\VideoLAN\VLC", r"C:\Program Files (x86)\VideoLAN\VLC"]
        for _p in _possible_vlc_paths:
            try:
                if os.path.exists(_p) and os.path.exists(os.path.join(_p, 'libvlc.dll')):
                    try:
                        os.add_dll_directory(_p)
                    except Exception:
                        os.environ['PATH'] = _p + os.pathsep + os.environ.get('PATH', '')
                    break
            except Exception:
                continue

try:
    import vlc
except Exception as e:
    raise RuntimeError("python-vlc import failed: %s" % e)

class Backend(QtCore.QObject):
    def __init__(self, instance, player, quick_widget, video_frame, player_window=None):
        super().__init__()
        self.instance = instance
        self.player = player
        self.quick_widget = quick_widget
        self.video_frame = video_frame
        self.player_window = player_window  # Reference to PlayerWindow for update_status
        self.playlist = []
        self.current_index = -1
        # attach VLC end-of-media event to trigger next playback
        try:
            self.em = self.player.event_manager()
            self.em.event_attach(vlc.EventType.MediaPlayerEndReached, self._vlc_end_callback)
        except Exception:
            self.em = None

    @Slot('QStringList')
    def addFiles(self, paths):
        for p in paths:
            self.addFile(p)

    @Slot(str)
    def addFile(self, path):
        if not os.path.exists(path):
            return
        if path in self.playlist:
            return
        index = len(self.playlist)
        self.playlist.append(path)
        root = self.quick_widget.rootObject()
        if root:
            # add with placeholder duration/size (updated later)
            try:
                root.addItem(os.path.basename(path), path, 0, 0)
                # show a small toast so user sees the add event
                try:
                    root.showToast('Added: ' + os.path.basename(path))
                except Exception:
                    pass
            except Exception as e:
                print('Error calling QML addItem:', e)
        else:
            # QML not ready yet; schedule a retry shortly on the main thread
            print('Warning: QML rootObject() is None; scheduling add for', path)
            try:
                def _delayed_add():
                    rt = self.quick_widget.rootObject()
                    if rt:
                        try:
                            rt.addItem(os.path.basename(path), path, 0, 0)
                            try:
                                rt.showToast('Added: ' + os.path.basename(path))
                            except Exception:
                                pass
                        except Exception as e:
                            print('Error calling delayed QML addItem:', e)
                    else:
                        print('Delayed add still could not find QML root for', path)
                QtCore.QTimer.singleShot(200, _delayed_add)
            except Exception:
                pass
        # collect metadata in background (size + duration)
        t = threading.Thread(target=self._collect_metadata, args=(path, index), daemon=True)
        t.start()
        # keep reference to thread to avoid GC (optional)
        try:
            self._threadpool
        except AttributeError:
            self._threadpool = []
        self._threadpool.append(t)

    def _collect_metadata(self, path, index):
        size = 0
        duration = 0
        try:
            size = os.path.getsize(path)
        except Exception:
            size = 0
        # try to get duration via VLC media parse (may block briefly)
        try:
            m = self.instance.media_new(path)
            try:
                m.parse()
            except Exception:
                pass
            dur = m.get_duration()
            if dur and dur > 0:
                duration = dur
        except Exception:
            duration = 0
        # notify QML on main thread
        try:
            QtCore.QMetaObject.invokeMethod(self, 'updateMetadata', QtCore.Qt.QueuedConnection,
                                             QtCore.Q_ARG(int, index), QtCore.Q_ARG(int, duration), QtCore.Q_ARG(int, size))
        except Exception:
            pass

    @Slot(int, int, int)
    def updateMetadata(self, index, durationMs, sizeBytes):
        root = self.quick_widget.rootObject()
        if root:
            try:
                root.updateItemMetadata(index, durationMs, sizeBytes)
            except Exception:
                pass

    @Slot(int)
    def playAt(self, index):
        if 0 <= index < len(self.playlist):
            self.current_index = index
            self.open_path(self.playlist[index])

    def _vlc_end_callback(self, event):
        try:
            QtCore.QMetaObject.invokeMethod(self, 'on_media_end', QtCore.Qt.QueuedConnection)
        except Exception:
            pass

    @Slot()
    def on_media_end(self):
        # play next item if exists (called on main thread), with looping
        try:
            if not self.playlist:
                return

            self.current_index = (self.current_index + 1) % len(self.playlist)
            next_path = self.playlist[self.current_index]
            self.open_path(next_path)
            # show toast in QML
            try:
                root = self.quick_widget.rootObject()
                if root:
                    root.showToast('Playing: ' + os.path.basename(next_path))
            except Exception:
                pass
        except Exception:
            pass
        
    @Slot(int, float)
    def requestThumbnail(self, index, percent):
        # index: playlist index, percent: 0..100
        try:
            if not (0 <= index < len(self.playlist)):
                return
            path = self.playlist[index]
            # get duration via media; fallback to 0
            length = 0
            try:
                m = self.instance.media_new(path)
                try:
                    m.parse()
                except Exception:
                    pass
                length = m.get_duration() or 0
            except Exception:
                length = 0
            if length > 0:
                t_ms = int((percent / 100.0) * length)
            else:
                t_ms = 0
            threading.Thread(target=self._generate_thumbnail, args=(path, t_ms), daemon=True).start()
        except Exception:
            pass

    def _generate_thumbnail(self, path, t_ms):
        outdir = tempfile.gettempdir()
        outpath = os.path.join(outdir, f"thumb_{abs(hash(path))}_{t_ms}.jpg")
        ffmpeg = shutil.which('ffmpeg')
        if ffmpeg:
            sec = max(0, t_ms / 1000.0)
            cmd = [ffmpeg, '-ss', str(sec), '-i', path, '-frames:v', '1', '-q:v', '2', outpath, '-y']
            try:
                subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=8)
            except Exception:
                try:
                    if os.path.exists(outpath):
                        os.remove(outpath)
                except Exception:
                    pass
        else:
            # fallback using libVLC snapshot (may be slow and intrusive)
            try:
                tmp_instance = vlc.Instance()
                tmp_player = tmp_instance.media_player_new()
                m = tmp_instance.media_new(path)
                tmp_player.set_media(m)
                tmp_player.play()
                QtCore.QThread.msleep(200)
                try:
                    tmp_player.set_time(int(t_ms))
                    QtCore.QThread.msleep(200)
                except Exception:
                    pass
                try:
                    tmp_player.video_take_snapshot(0, outpath, 160, 90)
                except Exception:
                    pass
                try:
                    tmp_player.stop()
                except Exception:
                    pass
            except Exception:
                pass
        # deliver to QML
        try:
            if os.path.exists(outpath):
                QtCore.QMetaObject.invokeMethod(self, '_deliver_thumbnail', QtCore.Qt.QueuedConnection,
                                                 QtCore.Q_ARG(str, outpath))
        except Exception:
            pass

    @Slot(str)
    def _deliver_thumbnail(self, outpath):
        root = self.quick_widget.rootObject()
        if root:
            try:
                root.showThumb(outpath)
            except Exception:
                pass
    @Slot(float)
    def setPositionPercent(self, percent):
        try:
            length = self.player.get_length()
            if length > 0:
                new = int(length * (percent / 100.0))
                self.player.set_time(new)
        except Exception:
            pass

    @Slot(float)
    def setVolumePercent(self, percent):
        try:
            vol = int(max(0, min(100, percent)))
            self.player.audio_set_volume(vol)
        except Exception:
            pass

    @Slot(int)
    def removeAt(self, index):
        if 0 <= index < len(self.playlist):
            try:
                del self.playlist[index]
                root = self.quick_widget.rootObject()
                if root:
                    root.removeItem(index)
            except Exception:
                pass

    @Slot(int)
    def moveUp(self, index):
        if 1 <= index < len(self.playlist):
            try:
                self.playlist[index-1], self.playlist[index] = self.playlist[index], self.playlist[index-1]
                root = self.quick_widget.rootObject()
                if root:
                    root.moveUp(index)
            except Exception:
                pass

    @Slot(int)
    def moveDown(self, index):
        if 0 <= index < len(self.playlist)-1:
            try:
                self.playlist[index], self.playlist[index+1] = self.playlist[index+1], self.playlist[index]
                root = self.quick_widget.rootObject()
                if root:
                    root.moveDown(index)
            except Exception:
                pass

    @Slot()
    def clearPlaylist(self):
        self.player.stop()
        self.playlist.clear()
        self.current_index = -1
        root = self.quick_widget.rootObject()
        if root:
            try:
                root.clearPlaylist()
            except Exception as e:
                print(f"Error calling QML clearPlaylist: {e}")

    def open_path(self, path):
        if not os.path.exists(path):
            return
        self.player.stop()
        media = self.instance.media_new(path)
        # Parse media to get duration information
        try:
            media.parse()
        except Exception:
            pass
        self.player.set_media(media)
        # set video output window (Windows / Linux / macOS handled by instance)
        try:
            if sys.platform.startswith('win'):
                self.player.set_hwnd(int(self.video_frame.winId()))
            elif sys.platform.startswith('linux'):
                self.player.set_xwindow(int(self.video_frame.winId()))
            elif sys.platform.startswith('darwin'):
                self.player.set_nsobject(int(self.video_frame.winId()))
        except Exception:
            pass
        self.player.play()
        # Force status updates after delays to get time info as media loads
        if self.player_window:
            QtCore.QTimer.singleShot(500, self.player_window.update_status)
            QtCore.QTimer.singleShot(1000, self.player_window.update_status)
            QtCore.QTimer.singleShot(2000, self.player_window.update_status)
        # update current_index if path is in playlist
        try:
            if path in self.playlist:
                self.current_index = self.playlist.index(path)
            else:
                self.current_index = -1
        except Exception:
            self.current_index = -1
        # show toast in QML about current playing
        try:
            root = self.quick_widget.rootObject()
            if root:
                root.showToast('Playing: ' + os.path.basename(path))
                root.setCurrentIndex(self.current_index)
        except Exception:
            pass

class PlayerWindow(QtWidgets.QWidget):
    SEEK_MS = 5000
    VOL_STEP = 10

    def __init__(self):
        super().__init__()
        self.setAcceptDrops(True)
        self.setWindowTitle('Py Video Player (QML Demo)')
        self.resize(1000, 650)
        # Ensure window can receive keyboard events
        self.setFocusPolicy(QtCore.Qt.StrongFocus)
        self.setFocus()

        # VLC
        self.instance = vlc.Instance("--avcodec-hw=none")
        self.player = self.instance.media_player_new()

        # video frame (native widget to host libVLC output)
        self.video_frame = QtWidgets.QFrame()
        self.video_frame.setStyleSheet('background-color: black;')
        self.video_frame.setMouseTracking(True)
        # Allow keyboard events to pass through to parent
        self.video_frame.setFocusPolicy(QtCore.Qt.NoFocus)

        # controls on bottom (basic)
        self.open_btn = QtWidgets.QPushButton('Open File(s)')
        self.open_btn.clicked.connect(self.open_files)
        self.open_folder_btn = QtWidgets.QPushButton('Open Folder')
        self.open_folder_btn.clicked.connect(self.open_folder)
        self.play_btn = QtWidgets.QPushButton('Play')
        self.play_btn.clicked.connect(self.toggle_play)
        # Set focus policy so buttons don't steal keyboard focus for arrow keys
        self.open_btn.setFocusPolicy(QtCore.Qt.TabFocus)
        self.open_folder_btn.setFocusPolicy(QtCore.Qt.TabFocus)
        self.play_btn.setFocusPolicy(QtCore.Qt.TabFocus)

        controls = QtWidgets.QHBoxLayout()
        controls.addWidget(self.open_btn)
        controls.addWidget(self.open_folder_btn)
        controls.addWidget(self.play_btn)
        controls.addStretch(1)

        left_vbox = QtWidgets.QVBoxLayout()
        left_vbox.addWidget(self.video_frame, 1)
        left_vbox.addLayout(controls)

        # bottom control bar (position slider, time, volume)
        self.control_bar = QtWidgets.QWidget()
        cb_layout = QtWidgets.QHBoxLayout()
        cb_layout.setContentsMargins(6,6,6,6)
        self.pos_slider = QtWidgets.QSlider(QtCore.Qt.Horizontal)
        self.pos_slider.setRange(0, 1000)
        self.pos_slider.setSingleStep(1)
        self.pos_slider.setTracking(True)
        self.pos_slider.sliderPressed.connect(self._pos_pressed)
        self.pos_slider.sliderReleased.connect(self._pos_released)
        self.pos_slider.sliderMoved.connect(self._pos_moved)
        # Allow slider to receive focus for mouse interaction, but let arrow keys pass through
        self.pos_slider.setFocusPolicy(QtCore.Qt.ClickFocus)

        self.time_label = QtWidgets.QLabel('00:00 / 00:00')
        self.time_label.setFixedWidth(140)
        # Ensure label is visible and updates properly
        self.time_label.setStyleSheet('color: black; background-color: white; padding: 2px;')
        self.time_label.setAlignment(QtCore.Qt.AlignCenter)

        self.vol_label = QtWidgets.QLabel('Vol: 100')
        self.vol_label.setFixedWidth(60)
        self.vol_slider = QtWidgets.QSlider(QtCore.Qt.Horizontal)
        self.vol_slider.setRange(0,100)
        self.vol_slider.setValue(100)
        self.vol_slider.setFixedWidth(120)
        self.vol_slider.valueChanged.connect(self._vol_changed)
        # Allow slider to receive focus for mouse interaction, but let arrow keys pass through
        self.vol_slider.setFocusPolicy(QtCore.Qt.ClickFocus)

        cb_layout.addWidget(self.pos_slider, 1)
        cb_layout.addWidget(self.time_label)
        cb_layout.addWidget(self.vol_label)
        cb_layout.addWidget(self.vol_slider)
        self.control_bar.setLayout(cb_layout)
        left_vbox.addWidget(self.control_bar)

        # auto-hide control bar
        self.control_bar.setVisible(True)
        self.hide_timer = QtCore.QTimer(self)
        self.hide_timer.setInterval(3000)
        self.hide_timer.setSingleShot(True)
        self.hide_timer.timeout.connect(lambda: self.control_bar.setVisible(False))
        self.setMouseTracking(True)

        # QML playlist (right)
        self.qml_widget = QQuickWidget()
        qml_path = os.path.join(os.path.dirname(__file__), 'qml', 'Main.qml')
        self.qml_widget.engine().rootContext().setContextProperty('pyBackend', None)
        self.qml_widget.setSource(QUrl.fromLocalFile(qml_path))
        self.qml_widget.setResizeMode(QQuickWidget.SizeRootObjectToView)
        self.qml_widget.setMouseTracking(True)
        # Allow keyboard events to pass through to parent
        self.qml_widget.setFocusPolicy(QtCore.Qt.NoFocus)

        # if QML isn't ready immediately, listen for status changes to flush playlist
        try:
            self.qml_widget.statusChanged.connect(self._on_qml_status_changed)
        except Exception:
            pass

        # layout
        hbox = QtWidgets.QHBoxLayout()
        hbox.addLayout(left_vbox, 4)
        hbox.addWidget(self.qml_widget, 1)
        self.setLayout(hbox)

        # backend bridge
        self.backend = Backend(self.instance, self.player, self.qml_widget, self.video_frame, self)
        self.qml_widget.engine().rootContext().setContextProperty('pyBackend', self.backend)

    def _on_qml_status_changed(self, status):
        try:
            # QQuickWidget.Ready enum indicates QML root is available
            if status == QQuickWidget.Ready:
                root = self.qml_widget.rootObject()
                if root and hasattr(self, 'backend'):
                    # flush existing playlist entries into QML
                    try:
                        for idx, p in enumerate(self.backend.playlist):
                            try:
                                root.addItem(os.path.basename(p), p, 0, 0)
                            except Exception:
                                pass
                    except Exception:
                        pass
        except Exception:
            pass

        # timer for status
        self.timer = QtCore.QTimer(self)
        self.timer.setInterval(500)
        self.timer.timeout.connect(self.update_status)
        self.timer.start()

        # note: keyboard shortcuts handled in eventFilter to avoid QShortcut import issues

        # track user interaction with slider
        self._user_dragging = False
        # remember playlist visibility when entering fullscreen
        self._pre_fs_playlist_visible = True

        # install global event filter to detect mouse movement for auto-hide
        QtWidgets.QApplication.instance().installEventFilter(self)

    def _seek_relative(self, ms):
        try:
            # Check if player has media loaded
            if not self.player.get_media():
                return
            t = self.player.get_time()
            if t is None or t < 0:
                t = 0
            length = self.player.get_length()
            if length is None or length < 0:
                length = 0
            new_time = max(0, int(t + ms))
            if length > 0:
                new_time = min(new_time, length - 100)
            self.player.set_time(new_time)
        except Exception as e:
            print(f"Seek error: {e}")

    def _change_volume(self, delta):
        try:
            v = self.player.audio_get_volume()
            if v is None or v < 0:
                v = 100
            v = max(0, min(100, int(v + delta)))
            self.player.audio_set_volume(v)
            # Update UI immediately
            try:
                if hasattr(self, 'vol_slider'):
                    self.vol_slider.setValue(v)
                if hasattr(self, 'vol_label'):
                    self.vol_label.setText(f'Vol: {v}')
            except Exception:
                pass
        except Exception as e:
            print(f"Volume change error in eventFilter: {e}")

    def eventFilter(self, obj, event):
        # show controls on mouse move
        try:
            if event.type() == QtCore.QEvent.MouseMove:
                try:
                    self.control_bar.setVisible(True)
                    self.hide_timer.start()
                except Exception:
                    pass
            # When video frame is clicked, give focus to main window for keyboard input
            if event.type() == QtCore.QEvent.MouseButtonPress:
                if obj == self.video_frame:
                    self.setFocus()
                    return False  # Let the event continue
            # keyboard shortcuts - handle globally for this window and its children
            if event.type() == QtCore.QEvent.KeyPress:
                # Process keyboard events for this window and its child widgets
                if obj == self or obj == self.video_frame or obj == self.qml_widget or obj == self.control_bar or hasattr(obj, 'parent') and obj.parent() == self:
                    key = event.key()
                    if key == QtCore.Qt.Key_Space:
                        self.toggle_play()
                        event.accept()
                        return True
                    if key in (QtCore.Qt.Key_Enter, QtCore.Qt.Key_Return):
                        # toggle fullscreen via centralized handler
                        try:
                            self.toggle_fullscreen()
                        except Exception:
                            pass
                        event.accept()
                        return True
                    if key == QtCore.Qt.Key_Left:
                        self._seek_relative(-self.SEEK_MS)
                        event.accept()
                        return True
                    if key == QtCore.Qt.Key_Right:
                        self._seek_relative(self.SEEK_MS)
                        event.accept()
                        return True
                    if key == QtCore.Qt.Key_Up:
                        self._change_volume(self.VOL_STEP)
                        event.accept()
                        return True
                    if key == QtCore.Qt.Key_Down:
                        self._change_volume(-self.VOL_STEP)
                        event.accept()
                        return True
        except Exception:
            pass
        return super().eventFilter(obj, event)

    def open_files(self):
        paths, _ = QtWidgets.QFileDialog.getOpenFileNames(self, 'Open Video Files')
        if paths:
            self.backend.addFiles(paths)
            # play first
            if not self.player.is_playing():
                self.backend.playAt(len(self.backend.playlist) - len(paths))

    def open_folder(self):
        folder = QtWidgets.QFileDialog.getExistingDirectory(self, 'Open Folder')
        if not folder:
            return
        exts = ('.mp4', '.mkv', '.avi', '.mov', '.wmv', '.flv', '.webm')
        files = []
        for root, _, files_in_dir in os.walk(folder):
            for fn in sorted(files_in_dir):
                if fn.lower().endswith(exts):
                    files.append(os.path.join(root, fn))
        if files:
            self.backend.addFiles(files)
            if not self.player.is_playing():
                self.backend.playAt(len(self.backend.playlist) - len(files))

    def toggle_fullscreen(self):
        # Toggle fullscreen and manage playlist/control visibility
        if not self.isFullScreen():
            # entering fullscreen: hide playlist and controls
            self.qml_widget.setVisible(False)
            self.control_bar.setVisible(False)
            
            # enter fullscreen
            self.showFullScreen()
            # Tell libVLC to use fullscreen mode and reset scaling so it fills the window
            self.player.set_fullscreen(True)
            if hasattr(self.player, 'video_set_scale'):
                self.player.video_set_scale(0)
        else:
            # exiting fullscreen: show playlist and controls
            self.player.set_fullscreen(False)
            self.showNormal()
            self.qml_widget.setVisible(True)
            self.control_bar.setVisible(True)

    # position slider handlers
    def _pos_pressed(self):
        self._user_dragging = True

    def _pos_released(self):
        try:
            val = self.pos_slider.value()
            percent = (val / 1000.0) * 100.0
            self.backend.setPositionPercent(percent)
        except Exception:
            pass
        finally:
            self._user_dragging = False

    def _pos_moved(self, val):
        # live update while dragging
        try:
            percent = (val / 1000.0) * 100.0
            self.backend.setPositionPercent(percent)
            # update time label preview based on backend length if available
            length = self.player.get_length() or 0
            pos_ms = int((percent/100.0) * length) if length>0 else 0
            self.time_label.setText(self._fmt_ms(pos_ms) + ' / ' + self._fmt_ms(length))
        except Exception:
            pass

    def _vol_changed(self, val):
        try:
            self.vol_label.setText(f'Vol: {val}')
            self.backend.setVolumePercent(float(val))
        except Exception:
            pass

    def _fmt_ms(self, ms):
        # Handle None, negative, or invalid values
        if ms is None or ms < 0:
            ms = 0
        s = int(ms/1000)
        h = s // 3600
        m = (s % 3600) // 60
        s = s % 60
        if h:
            return f"{h:02d}:{m:02d}:{s:02d}"
        return f"{m:02d}:{s:02d}"

    # (eventFilter implemented earlier to handle mouse and keyboard)

    def toggle_play(self):
        if self.player.is_playing():
            self.player.pause()
            self.play_btn.setText('Play')
        else:
            self.player.play()
            self.play_btn.setText('Pause')

    def keyPressEvent(self, event):
        key = event.key()
        if key in (QtCore.Qt.Key_Return, QtCore.Qt.Key_Enter):
            try:
                self.toggle_fullscreen()
            except Exception:
                pass
            event.accept()
            return
        if key == QtCore.Qt.Key_Space:
            self.toggle_play()
            event.accept()
            return
        if key == QtCore.Qt.Key_Left:
            self.seek(-self.SEEK_MS)
            event.accept()
            return
        if key == QtCore.Qt.Key_Right:
            self.seek(self.SEEK_MS)
            event.accept()
            return
        if key == QtCore.Qt.Key_Up:
            self.change_volume(self.VOL_STEP)
            event.accept()
            return
        if key == QtCore.Qt.Key_Down:
            self.change_volume(-self.VOL_STEP)
            event.accept()
            return
        super().keyPressEvent(event)

    def seek(self, ms):
        try:
            # Check if player has media loaded
            if not self.player.get_media():
                return
            cur = self.player.get_time()
            if cur is None or cur < 0:
                cur = 0
            length = self.player.get_length()
            if length is None or length < 0:
                length = 0
            new = max(0, cur + ms)
            if length > 0:
                new = min(new, length - 100)
            self.player.set_time(int(new))
        except Exception as e:
            print(f"Seek error: {e}")

    def change_volume(self, delta):
        try:
            vol = self.player.audio_get_volume()
            if vol is None or vol < 0:
                vol = 100
            new = max(0, min(100, int(vol + delta)))
            self.player.audio_set_volume(new)
            # Update UI immediately
            try:
                if hasattr(self, 'vol_slider'):
                    self.vol_slider.setValue(new)
                if hasattr(self, 'vol_label'):
                    self.vol_label.setText(f'Vol: {new}')
            except Exception:
                pass
        except Exception as e:
            print(f"Volume change error: {e}")

    def update_status(self):
        try:
            # Always get volume first (works even without media)
            vol = self.player.audio_get_volume()
            if vol is None or vol < 0:
                vol = 0
            
            # Check if player is actually playing
            is_playing = self.player.is_playing()
            
            # Get player state (0=NothingSpecial, 1=Opening, 2=Buffering, 3=Playing, 4=Paused, 5=Stopped, 6=Ended, 7=Error)
            try:
                state = self.player.get_state()
            except Exception:
                state = None
            
            # Try to get time information from VLC
            # VLC get_time() and get_length() return milliseconds
            pos_raw = self.player.get_time()
            length_raw = self.player.get_length()
            
            # If length is not available from player, try to get it from media object
            if (length_raw is None or length_raw < 0):
                try:
                    media = self.player.get_media()
                    if media:
                        # Try to get duration from media object
                        media_length = media.get_duration()
                        if media_length and media_length > 0:
                            length_raw = media_length
                except Exception:
                    pass

            # Handle None and negative values properly
            # VLC returns -1 when time/length is not available
            # Only use position if it's valid (>= 0) or if we're playing/paused
            if pos_raw is not None and pos_raw >= 0:
                pos = pos_raw
            else:
                pos = 0
            
            if length_raw is not None and length_raw >= 0:
                length = length_raw
            else:
                length = 0
            
            # Debug output (can be removed later)
            if not hasattr(self, '_last_debug_time'):
                self._last_debug_time = 0
            import time
            current_time = time.time()
            if current_time - self._last_debug_time > 2.0:  # Print every 2 seconds
                print(f"Status: state={state}, is_playing={is_playing}, pos={pos}ms, length={length}ms, pos_raw={pos_raw}, length_raw={length_raw}")
                self._last_debug_time = current_time

            # push position, length, volume to QML
            try:
                root = self.qml_widget.rootObject()
                if root:
                    root.updateStatus(pos, length, vol)
            except Exception as e:
                print(f"Error updating QML status: {e}")
            
            # update bottom control bar (only when not user-dragging)
            try:
                if hasattr(self, 'pos_slider') and not getattr(self, '_user_dragging', False):
                    if length > 0:
                        if pos >= 0:
                            val = int((pos/length) * 1000)
                            val = max(0, min(1000, val))  # Clamp to valid range
                        else:
                            val = 0
                    else:
                        val = 0
                    # Always update slider value to reflect current position
                    old_slider_val = self.pos_slider.value()
                    if old_slider_val != val:
                        self.pos_slider.blockSignals(True)  # Prevent triggering events during update
                        self.pos_slider.setValue(val)
                        self.pos_slider.blockSignals(False)
                        # Force slider update
                        self.pos_slider.update()
                        self.pos_slider.repaint()
                        # Debug output (only print significant changes, every 5 seconds)
                        if not hasattr(self, '_last_slider_val') or abs(self._last_slider_val - val) > 10:
                            if not hasattr(self, '_last_slider_debug_time'):
                                self._last_slider_debug_time = 0
                            import time
                            current_time = time.time()
                            if current_time - self._last_slider_debug_time > 5.0:  # Print every 5 seconds
                                print(f"Slider updated: {old_slider_val} -> {val}/1000 (pos={pos}ms, length={length}ms)")
                                self._last_slider_debug_time = current_time
                            self._last_slider_val = val
                    
                    # Always update time label - show actual values even if pos is 0
                    # Format: current_time / total_time
                    if length > 0:
                        time_text = self._fmt_ms(pos) + ' / ' + self._fmt_ms(length)
                    else:
                        time_text = self._fmt_ms(pos) + ' / --:--'
                    # Force update the time label
                    if hasattr(self, 'time_label'):
                        old_text = self.time_label.text()
                        if old_text != time_text:
                            self.time_label.setText(time_text)
                            # Force repaint to ensure label is updated
                            self.time_label.update()
                            self.time_label.repaint()
                            # Ensure label is visible
                            self.time_label.setVisible(True)
                            self.time_label.show()  # Explicitly show the widget
                            # Debug output (only print when text actually changes, once per update)
                            if not hasattr(self, '_last_time_text') or self._last_time_text != time_text:
                                # Only print once when text changes
                                print(f"Time label updated: '{old_text}' -> '{time_text}'")
                                self._last_time_text = time_text
                        # Always ensure label is visible, even if text didn't change
                        if not self.time_label.isVisible():
                            print(f"Time label was hidden! Making it visible...")
                            self.time_label.setVisible(True)
                            self.time_label.show()
                        # Also ensure control_bar is visible and restart hide timer
                        if hasattr(self, 'control_bar'):
                            if not self.control_bar.isVisible():
                                print(f"Control bar was hidden! Making it visible...")
                                self.control_bar.setVisible(True)
                                self.control_bar.show()
                            # Restart the hide timer so control bar stays visible during playback
                            if hasattr(self, 'hide_timer'):
                                self.hide_timer.stop()
                                # Only auto-hide if not playing (to keep controls visible during playback)
                                if not self.player.is_playing():
                                    self.hide_timer.start()
                
                # Always update volume UI
                if hasattr(self, 'vol_slider'):
                    vol_int = int(vol)
                    self.vol_slider.setValue(vol_int)
                if hasattr(self, 'vol_label'):
                    self.vol_label.setText(f'Vol: {int(vol)}')
            except Exception as e:
                print(f"Error updating control bar: {e}")
        except Exception as e:
            print(f"Error in update_status: {e}")

    def dragEnterEvent(self, event: QtGui.QDragEnterEvent):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            event.ignore()

    def dropEvent(self, event: QtGui.QDropEvent):
        if event.mimeData().hasUrls():
            urls = event.mimeData().urls()
            paths = [url.toLocalFile() for url in urls]
            exts = ('.mp4', '.mkv', '.avi', '.mov', '.wmv', '.flv', '.webm')
            video_files = [p for p in paths if os.path.isfile(p) and p.lower().endswith(exts)]
            
            if video_files:
                self.backend.addFiles(video_files)
                # Optionally, play the first dropped file if nothing is playing
                if not self.player.is_playing():
                    # play the first new file, which is at the end of the current playlist
                    new_index = len(self.backend.playlist) - len(video_files)
                    if new_index >= 0:
                        self.backend.playAt(new_index)

def main():
    app = QtWidgets.QApplication(sys.argv)
    w = PlayerWindow()
    w.show()
    sys.exit(app.exec())

if __name__ == '__main__':
    main()
