import QtQuick 2.15
import QtQuick.Controls 2.15
import QtQuick.Layouts 1.15

Rectangle {
    id: root
    anchors.fill: parent
    color: bgColor

    ListModel {
        id: playlistModel
    }

    function addItem(name, path, duration, size) {
        if (duration === undefined) duration = 0
        if (size === undefined) size = 0
        playlistModel.append({"name": name, "path": path, "duration": duration, "size": size})
    }

    function removeItem(index) {
        if (index >= 0 && index < playlistModel.count) playlistModel.remove(index)
    }

    function moveUp(index) {
        if (index > 0 && index < playlistModel.count) {
            var obj = playlistModel.get(index)
            playlistModel.remove(index)
            playlistModel.insert(index-1, {"name": obj.name, "path": obj.path})
        }
    }

    function moveDown(index) {
        if (index >= 0 && index < playlistModel.count-1) {
            var obj = playlistModel.get(index)
            playlistModel.remove(index)
            playlistModel.insert(index+1, {"name": obj.name, "path": obj.path})
        }
    }

    function clearPlaylist() {
        playlistModel.clear()
    }

    property int backendPosMs: 0
    property int backendLengthMs: 0
    property bool userDragging: false
    // When False, hide the QML playback/volume controls (we use native QtWidgets controls)
    property bool showQmlControls: false
    property color bgColor: "#2b2b2b"
    property color surface: "#333333"
    property color accent: "#2196F3"

    ColumnLayout {
        anchors.fill: parent
        anchors.margins: 8

        Row {
            spacing: 12
            Layout.alignment: Qt.AlignHCenter
        }
        Row {
            id: playlistHeader
            spacing: 8
            Layout.alignment: Qt.AlignHCenter
            Text { text: "Playlist"; color: "white"; font.bold: true; font.pointSize: 12 }
            Button {
                text: "Remove All"
                onClicked: {
                    if (pyBackend) pyBackend.clearPlaylist()
                }
            }
        }

        // Progress and time (only shown when `showQmlControls` is true)
        Column {
            visible: showQmlControls
            spacing: 6
            Layout.fillWidth: true
            
            Row {
                spacing: 8
                Text { id: timeText; text: "00:00 / 00:00"; color: "white" }
            }
            Slider {
                id: progressSlider
                from: 0; to: 100; value: 0
                Layout.fillWidth: true
                onPositionChanged: {
                    if (userDragging) {
                        if (pyBackend) pyBackend.setPositionPercent(value)
                        var l = backendLengthMs
                        var pos = Math.round((value/100.0) * l)
                        timeText.text = fmtMs(pos) + " / " + fmtMs(l)
                    }
                }
                MouseArea {
                    anchors.fill: parent
                    onPressed: {
                        userDragging = true
                    }
                    onReleased: {
                        userDragging = false
                        if (pyBackend) pyBackend.setPositionPercent(progressSlider.value)
                    }
                }
            }
            Image {
                id: thumbPreview
                visible: false
                width: 160
                height: 90
                fillMode: Image.PreserveAspectFit
                Layout.alignment: Qt.AlignHCenter
                source: ""
                Rectangle { anchors.fill: parent; color: "transparent" }
            }
            Row {
                spacing: 8
                Text { text: "Vol"; color: "white" }
                Slider {
                    id: volSlider
                    from: 0; to: 100; value: 100
                    onPositionChanged: { /* live update */ }
                    MouseArea {
                        anchors.fill: parent
                        onReleased: { if (pyBackend) pyBackend.setVolumePercent(volSlider.value) }
                    }
                }
            }
        }

        ListView {
            id: listView
            model: playlistModel
            Layout.fillWidth: true
            Layout.fillHeight: true
            clip: true
            delegate: Rectangle {
                width: parent.width
                height: 40
                color: index % 2 === 0 ? "#333" : "#3a3a3a"
                Row {
                    anchors.fill: parent
                    spacing: 8
                    Column { 
                        width: parent.width - 110; 
                        spacing: 2
                        Text { text: name; color: "white"; elide: Text.ElideRight }
                        Row { 
                            spacing: 8
                            Text { text: (duration>0?fmtMs(duration):"--:--"); color: "#cccccc"; font.pixelSize: 11 }
                            Text { text: (size>0?Math.round(size/1024) + " KB":""); color: "#aaaaaa"; font.pixelSize: 11 }
                        }
                    }
                    Rectangle {
                        width: 100
                        height: parent.height
                        color: "transparent"
                        anchors.right: parent.right
                        
                        Button {
                            id: removeBtn
                            text: "✕"
                            anchors.right: parent.right
                            anchors.verticalCenter: parent.verticalCenter
                            onClicked: { if (pyBackend) pyBackend.removeAt(index) }
                        }
                        Button {
                            id: downBtn
                            text: "⬇"
                            anchors.right: removeBtn.left
                            anchors.verticalCenter: parent.verticalCenter
                            onClicked: { if (pyBackend) pyBackend.moveDown(index) }
                            enabled: index < playlistModel.count-1
                        }
                        Button {
                            id: upBtn
                            text: "⬆"
                            anchors.right: downBtn.left
                            anchors.verticalCenter: parent.verticalCenter
                            onClicked: { if (pyBackend) pyBackend.moveUp(index) }
                            enabled: index > 0
                        }
                    }
                }
                MouseArea {
                    anchors.fill: parent
                    anchors.rightMargin: 100
                    acceptedButtons: Qt.LeftButton
                    onDoubleClicked: {
                        if (pyBackend) pyBackend.playAt(index)
                    }
                }
            }
        }
    }

    // small toast at top-right
    Rectangle {
        id: toast
        width: parent.width * 0.9
        height: 36
        color: "#222"
        radius: 6
        anchors.top: parent.top
        anchors.horizontalCenter: parent.horizontalCenter
        anchors.topMargin: 6
        opacity: 0.0
        z: 999
        Row {
            anchors.fill: parent
            anchors.margins: 8
            spacing: 8
            Text { id: toastText; text: ""; color: "white" }
        }
        Behavior on opacity { NumberAnimation { duration: 300 } }
        Timer {
            id: toastTimer
            interval: 1800
            repeat: false
            onTriggered: toast.opacity = 0.0
        }
    }

    // Called from Python to update current playback status
    function updateStatus(posMs, lengthMs, volPercent) {
        var pos = Math.max(0, posMs)
        var length = Math.max(0, lengthMs)
        backendPosMs = pos
        backendLengthMs = length
        // only update slider if user is not dragging
        if (!userDragging) {
            if (length > 0) {
                progressSlider.value = Math.round((pos / length) * 100)
            } else {
                progressSlider.value = 0
            }
            timeText.text = fmtMs(pos) + " / " + fmtMs(length)
        }
        volSlider.value = volPercent
    }

    // update metadata for playlist item (called from Python)
    function updateItemMetadata(index, durationMs, sizeBytes) {
        if (index >= 0 && index < playlistModel.count) {
            var it = playlistModel.get(index)
            it.duration = durationMs
            it.size = sizeBytes
            playlistModel.set(index, it)
        }
    }

    function showThumb(path) {
        if (!path) { thumbPreview.visible = false; return }
        thumbPreview.source = path
        thumbPreview.visible = true
        // hide after short time
        Qt.createQmlObject('import QtQuick 2.0; Timer { interval: 1500; repeat: false; onTriggered: thumbPreview.visible=false }', root, 'tmpTimer').start()
    }

    function fmtMs(ms) {
        var s = Math.floor(ms/1000)
        var h = Math.floor(s/3600)
        var m = Math.floor((s%3600)/60)
        var ss = s%60
        if (h>0) return (h<10?"0"+h:h)+":"+(m<10?"0"+m:m)+":"+(ss<10?"0"+ss:ss)
        return (m<10?"0"+m:m)+":"+(ss<10?"0"+ss:ss)
    }

    function showToast(msg) {
        toastText.text = msg
        toast.opacity = 1.0
        toastTimer.restart()
    }
}