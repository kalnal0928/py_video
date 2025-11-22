import sys
import os
import platform
from PyQt5 import QtWidgets, QtGui, QtCore
import vlc

class VideoPlayer(QtWidgets.QMainWindow):
    SEEK_MS = 5000  # 5 seconds
    VOL_STEP = 10   # percent

    def __init__(self, video_path=None):
        super().__init__()
        self.setWindowTitle('Py Video Player')
        self.resize(900, 600)

        self.instance = vlc.Instance()
        self.player = self.instance.media_player_new()

        # central widget
        self.widget = QtWidgets.QWidget(self)
        self.setCentralWidget(self.widget)

        # video frame
        self.videoframe = QtWidgets.QFrame()
        self.videoframe.setStyleSheet('background-color: black;')

        # control buttons: Open File(s), Open Folder, Play/Pause
        self.open_btn = QtWidgets.QPushButton('Open File(s)')
        self.open_btn.clicked.connect(self.open_file)

        self.open_folder_btn = QtWidgets.QPushButton('Open Folder')
        self.open_folder_btn.clicked.connect(self.open_folder)

        self.play_btn = QtWidgets.QPushButton('Play')
        self.play_btn.clicked.connect(self.toggle_play)

        # playlist (right side)
        self.playlist = QtWidgets.QListWidget()
        self.playlist.itemDoubleClicked.connect(self.play_selected)

        # left: video + controls; right: playlist
        controls_layout = QtWidgets.QHBoxLayout()
        controls_layout.addWidget(self.open_btn)
        controls_layout.addWidget(self.open_folder_btn)
        controls_layout.addWidget(self.play_btn)
        controls_layout.addStretch(1)

        left_vbox = QtWidgets.QVBoxLayout()
        left_vbox.addWidget(self.videoframe)
        left_vbox.addLayout(controls_layout)

        main_hbox = QtWidgets.QHBoxLayout()
        main_hbox.addLayout(left_vbox, 4)
        main_hbox.addWidget(self.playlist, 1)

        self.widget.setLayout(main_hbox)

        # status bar
        self.status = self.statusBar()

        # full screen flag
        self._is_fullscreen = False

        # timer to update UI
        self.timer = QtCore.QTimer(self)
        self.timer.setInterval(500)
        self.timer.timeout.connect(self.update_ui)
        self.timer.start()

        if video_path:
            self.open_path(video_path)

        # ensure focus to receive key events
        self.setFocusPolicy(QtCore.Qt.StrongFocus)

    def open_file(self):
        paths, _ = QtWidgets.QFileDialog.getOpenFileNames(self, "Open Video Files")
        if paths:
            for p in paths:
                self.add_to_playlist(p)
            # play the first selected
            self.open_path(paths[0])

    def open_folder(self):
        folder = QtWidgets.QFileDialog.getExistingDirectory(self, "Open Folder")
        if folder:
            exts = ('.mp4', '.mkv', '.avi', '.mov', '.wmv', '.flv', '.webm')
            files = []
            for root, _, filenames in os.walk(folder):
                for fn in sorted(filenames):
                    if fn.lower().endswith(exts):
                        files.append(os.path.join(root, fn))
            for f in files:
                self.add_to_playlist(f)
            if files:
                self.open_path(files[0])

    def open_path(self, path):
        if not os.path.exists(path):
            QtWidgets.QMessageBox.critical(self, 'Error', 'File not found: ' + path)
            return
        media = self.instance.media_new(path)
        self.player.set_media(media)
        self._set_video_widget()
        self.player.play()
        self.play_btn.setText('Pause')
        self.status.showMessage(os.path.basename(path))
        # mark current playing in playlist if present
        for i in range(self.playlist.count()):
            item = self.playlist.item(i)
            if item.data(QtCore.Qt.UserRole) == path:
                item.setSelected(True)
            else:
                item.setSelected(False)

    def _set_video_widget(self):
        if sys.platform.startswith('win'):
            self.player.set_hwnd(int(self.videoframe.winId()))
        elif sys.platform.startswith('linux'):
            self.player.set_xwindow(int(self.videoframe.winId()))
        elif sys.platform.startswith('darwin'):
            self.player.set_nsobject(int(self.videoframe.winId()))

    def toggle_play(self):
        if self.player.is_playing():
            self.player.pause()
            self.play_btn.setText('Play')
        else:
            self.player.play()
            self.play_btn.setText('Pause')

    def add_to_playlist(self, path):
        if not os.path.exists(path):
            return
        # prevent duplicates
        existing = [self.playlist.item(i).data(QtCore.Qt.UserRole) for i in range(self.playlist.count())]
        if path in existing:
            return
        item = QtWidgets.QListWidgetItem(os.path.basename(path))
        item.setData(QtCore.Qt.UserRole, path)
        self.playlist.addItem(item)

    def play_selected(self, item):
        path = item.data(QtCore.Qt.UserRole)
        if path:
            self.open_path(path)

    def update_ui(self):
        # update status or detect end
        if self.player:
            try:
                length = self.player.get_length()  # ms
                pos = self.player.get_time()
                if length > 0 and pos >= 0:
                    self.status.showMessage(f"{self._format_time(pos)} / {self._format_time(length)}")
            except Exception:
                pass

    def _format_time(self, ms):
        s = int(ms / 1000)
        h = s // 3600
        m = (s % 3600) // 60
        s = s % 60
        if h:
            return f"{h:02d}:{m:02d}:{s:02d}"
        return f"{m:02d}:{s:02d}"

    def keyPressEvent(self, event):
        key = event.key()
        if key in (QtCore.Qt.Key_Return, QtCore.Qt.Key_Enter):
            self.toggle_fullscreen()
            return
        if key == QtCore.Qt.Key_Space:
            self.toggle_play()
            return
        if key == QtCore.Qt.Key_Left:
            self.seek(-self.SEEK_MS)
            return
        if key == QtCore.Qt.Key_Right:
            self.seek(self.SEEK_MS)
            return
        if key == QtCore.Qt.Key_Up:
            self.change_volume(self.VOL_STEP)
            return
        if key == QtCore.Qt.Key_Down:
            self.change_volume(-self.VOL_STEP)
            return
        super().keyPressEvent(event)

    def mouseDoubleClickEvent(self, event):
        self.toggle_fullscreen()

    def toggle_fullscreen(self):
        if not self._is_fullscreen:
            self._old_geometry = self.geometry()
            self.showFullScreen()
            self._is_fullscreen = True
        else:
            self.showNormal()
            if hasattr(self, '_old_geometry'):
                self.setGeometry(self._old_geometry)
            self._is_fullscreen = False

    def seek(self, ms):
        try:
            cur = self.player.get_time()
            if cur == -1:
                return
            new = max(0, cur + ms)
            length = self.player.get_length()
            if length > 0:
                new = min(new, length - 100)
            self.player.set_time(int(new))
        except Exception:
            pass

    def change_volume(self, delta):
        try:
            vol = self.player.audio_get_volume()
            if vol < 0:
                vol = 100
            new = max(0, min(100, vol + delta))
            self.player.audio_set_volume(int(new))
            self.status.showMessage(f"Volume: {new}%")
        except Exception:
            pass


def main():
    app = QtWidgets.QApplication(sys.argv)
    video = None
    if len(sys.argv) > 1:
        video = sys.argv[1]
    player = VideoPlayer(video)
    player.show()
    sys.exit(app.exec_())

if __name__ == '__main__':
    main()
