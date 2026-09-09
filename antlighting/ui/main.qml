import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts
import QtQuick.Window
import "theme.js" as Theme

ApplicationWindow {
    id: window
    visible: true
    width: 420
    height: 720
    minimumWidth: 360
    minimumHeight: 600
    title: qsTr("AntLighting VPN")
    color: pal.bg

    // ------------------------------------------------------------------ palette
    readonly property bool systemDark: Qt.styleHints.colorScheme === Qt.Light
            ? false : true
    readonly property var pal: Theme.theme(app.setting("ui.theme"), systemDark)
    readonly property var fnt: Theme.fontSizes()
    readonly property bool reduceMotion: !app.setting("ui.animations")
    readonly property bool connected: app.state === "connected"
    readonly property bool connecting: app.state === "connecting"
                                || app.state === "disconnecting"
    readonly property bool errored: app.state === "error"

    // Repaint the palette when the theme setting changes.
    Connections {
        target: app
        function onStateChanged() { paletteDirty = true }
    }
    property bool paletteDirty: false
    onPaletteDirtyChanged: if (paletteDirty) paletteDirty = false

    function mascotState() {
        if (connected) return "connected";
        if (connecting) return "connecting";
        if (errored) return "error";
        return "offline";
    }

    function connectLabel() {
        switch (app.state) {
        case "connected": return qsTr("DISCONNECT");
        case "connecting": return qsTr("CONNECTING…");
        case "disconnecting": return qsTr("DISCONNECTING…");
        case "error": return qsTr("TRY AGAIN");
        default: return qsTr("CONNECT");
        }
    }

    // -------------------------------------------------------------- background
    Rectangle {
        anchors.fill: parent
        color: pal.bg
    }
    Rectangle {
        // a soft accent wash behind the mascot so it sits in the composition
        anchors.horizontalCenter: parent.horizontalCenter
        y: -140
        width: 520
        height: 520
        radius: 260
        color: Theme.stateAccent(pal, app.state)
        opacity: connected ? 0.10 : (connecting ? 0.07 : 0.03)
        Behavior on opacity { NumberAnimation { duration: 500 } }
        Behavior on color { ColorAnimation { duration: 500 } }
    }

    // ----------------------------------------------------------------- header
    Item {
        id: header
        anchors.top: parent.top
        anchors.left: parent.left
        anchors.right: parent.right
        height: 56

        Text {
            anchors.verticalCenter: parent.verticalCenter
            anchors.left: parent.left
            anchors.leftMargin: 20
            text: "🐜⚡"
            font.pixelSize: 20
        }

        RoundButton {
            id: settingsButton
            anchors.verticalCenter: parent.verticalCenter
            anchors.right: parent.right
            anchors.rightMargin: 12
            width: 40
            height: 40
            radius: 20
            flat: true
            focusPolicy: Qt.TabFocus
            Accessible.name: qsTr("Advanced settings")
            ToolTip.visible: hovered
            ToolTip.text: qsTr("Advanced settings")
            ToolTip.delay: 600

            background: Rectangle {
                radius: 20
                color: settingsButton.hovered ? pal.surfaceHover : "transparent"
                border.color: settingsButton.hovered ? pal.border : "transparent"
                Behavior on color { ColorAnimation { duration: 150 } }
            }
            contentItem: Text {
                text: "⚙"
                color: settingsButton.hovered ? pal.text : pal.textDim
                font.pixelSize: 20
                horizontalAlignment: Text.AlignHCenter
                verticalAlignment: Text.AlignVCenter
            }
            RotationAnimation on rotation {
                running: settingsButton.hovered && !reduceMotion
                from: 0; to: 90; duration: 400
            }
            onClicked: settingsWindow.open()
        }
    }

    // -------------------------------------------------------------- main column
    ColumnLayout {
        anchors.top: header.bottom
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.bottom: parent.bottom
        anchors.margins: 24
        spacing: 18

        Item { Layout.fillHeight: true; Layout.preferredHeight: 8 }

        // ---- mascot -----------------------------------------------------
        AntMascot {
            id: mascot
            Layout.alignment: Qt.AlignHCenter
            Layout.preferredWidth: Math.min(280, window.width - 80)
            Layout.preferredHeight: Layout.preferredWidth
            mood: window.mascotState()
            colors: pal
            reduceMotion: window.reduceMotion
        }

        // ---- status text ------------------------------------------------
        ColumnLayout {
            Layout.alignment: Qt.AlignHCenter
            Layout.fillWidth: true
            spacing: 4

            Text {
                Layout.alignment: Qt.AlignHCenter
                text: app.message
                color: errored ? pal.danger : (connected ? pal.success : pal.text)
                font.pixelSize: fnt.title
                font.bold: true
                horizontalAlignment: Text.AlignHCenter
                wrapMode: Text.WordWrap
                Layout.fillWidth: true
                Behavior on color { ColorAnimation { duration: 300 } }
            }
            Text {
                Layout.alignment: Qt.AlignHCenter
                Layout.fillWidth: true
                text: app.detail
                color: pal.textDim
                font.pixelSize: fnt.body
                horizontalAlignment: Text.AlignHCenter
                wrapMode: Text.WordWrap
                visible: text.length > 0
            }
        }

        // ---- connect button ---------------------------------------------
        Button {
            id: connectButton
            Layout.alignment: Qt.AlignHCenter
            Layout.preferredWidth: 210
            Layout.preferredHeight: 62
            enabled: !connecting
            focusPolicy: Qt.TabFocus
            Accessible.name: window.connectLabel()
            Accessible.description: connected
                    ? qsTr("Turn the VPN off")
                    : qsTr("Turn the VPN on")

            background: Rectangle {
                radius: 31
                color: connectButton.down ? Qt.darker(accentColor, 1.15)
                                          : (connectButton.hovered ? Qt.lighter(accentColor, 1.1)
                                                                    : accentColor)
                readonly property color accentColor: window.connected ? pal.success
                        : (window.errored ? pal.danger : pal.accent)
                border.width: connectButton.activeFocus ? 3 : 0
                border.color: pal.text
                Behavior on color { ColorAnimation { duration: 220 } }
                scale: connectButton.down ? 0.97 : 1.0
                Behavior on scale { NumberAnimation { duration: 120 } }

                SequentialAnimation on opacity {
                    running: window.connecting && !window.reduceMotion
                    loops: Animation.Infinite
                    NumberAnimation { to: 0.78; duration: 700; easing.type: Easing.InOutSine }
                    NumberAnimation { to: 1.0; duration: 700; easing.type: Easing.InOutSine }
                }
            }

            contentItem: RowLayout {
                spacing: 10
                Item {
                    Layout.preferredWidth: 20
                    Layout.preferredHeight: 20
                    visible: window.connecting
                    BusyIndicator {
                        anchors.fill: parent
                        running: window.connecting
                        contentItem: Rectangle {
                            width: 20; height: 20
                            radius: 10
                            color: "transparent"
                            border.color: "#1A1A1A"
                            border.width: 3
                        }
                    }
                }
                Text {
                    Layout.fillWidth: true
                    text: window.connectLabel()
                    color: "#10161D"
                    font.pixelSize: fnt.subtitle
                    font.bold: true
                    font.letterSpacing: 1.2
                    horizontalAlignment: Text.AlignHCenter
                    verticalAlignment: Text.AlignVCenter
                }
            }

            onClicked: app.toggle()
            Keys.onEnterPressed: app.toggle()
            Keys.onReturnPressed: app.toggle()
        }

        // ---- progress bar -----------------------------------------------
        Rectangle {
            Layout.alignment: Qt.AlignHCenter
            Layout.preferredWidth: 210
            Layout.preferredHeight: 4
            radius: 2
            color: pal.border
            visible: window.connecting
            opacity: window.connecting ? 1.0 : 0.0
            Behavior on opacity { NumberAnimation { duration: 250 } }

            Rectangle {
                height: parent.height
                radius: 2
                width: parent.width * Math.max(0.04, app.progress)
                color: pal.accent
                Behavior on width { NumberAnimation { duration: 300; easing.type: Easing.OutCubic } }
            }
        }

        // ---- server selector --------------------------------------------
        Button {
            id: serverButton
            Layout.alignment: Qt.AlignHCenter
            Layout.preferredWidth: Math.min(300, window.width - 60)
            Layout.preferredHeight: 48
            flat: true
            focusPolicy: Qt.TabFocus
            Accessible.name: qsTr("Choose a server")
            ToolTip.visible: hovered
            ToolTip.text: qsTr("Choose a server")
            ToolTip.delay: 600

            background: Rectangle {
                radius: Theme.radius.lg
                color: serverButton.hovered ? pal.surfaceHover : pal.surface
                border.color: serverButton.activeFocus ? pal.text : pal.border
                border.width: serverButton.activeFocus ? 2 : 1
                Behavior on color { ColorAnimation { duration: 150 } }
            }

            contentItem: RowLayout {
                spacing: 10
                Text {
                    text: app.serverFlag
                    font.pixelSize: 18
                }
                ColumnLayout {
                    Layout.fillWidth: true
                    spacing: 0
                    Text {
                        Layout.fillWidth: true
                        text: qsTr("Server: ") + app.serverName
                        color: pal.text
                        font.pixelSize: fnt.body
                        font.bold: true
                        elide: Text.ElideRight
                    }
                    Text {
                        Layout.fillWidth: true
                        text: window.connected ? qsTr("Protected")
                              : (app.poolTotal > 0
                                 ? qsTr("%1 servers available").arg(app.poolTotal)
                                 : qsTr("Tap to refresh the list"))
                        color: pal.textDim
                        font.pixelSize: fnt.tiny
                        elide: Text.ElideRight
                    }
                }
                Text {
                    text: app.latencyText
                    color: window.connected ? pal.success : pal.textFaint
                    font.pixelSize: fnt.small
                    font.bold: true
                }
                Text {
                    text: "›"
                    color: pal.textFaint
                    font.pixelSize: 20
                }
            }
            onClicked: serverSheet.open()
        }

        Item { Layout.fillHeight: true; Layout.preferredHeight: 8 }

        // ---- footer -----------------------------------------------------
        RowLayout {
            Layout.alignment: Qt.AlignHCenter
            spacing: 12

            Text {
                text: window.connected
                      ? qsTr("🔒 Encrypted via %1").arg(app.mode === "tun" ? qsTr("VPN tunnel") : qsTr("system proxy"))
                      : qsTr("🔓 Not protected")
                color: window.connected ? pal.textDim : pal.textFaint
                font.pixelSize: fnt.tiny
            }
            Rectangle { Layout.preferredWidth: 1; Layout.preferredHeight: 12; color: pal.border }
            Text {
                text: app.coreState === "running" ? qsTr("Core running") : qsTr("Core idle")
                color: pal.textFaint
                font.pixelSize: fnt.tiny
            }
        }

        Text {
            Layout.alignment: Qt.AlignHCenter
            text: qsTr("AntLighting VPN")
            color: pal.textFaint
            font.pixelSize: fnt.small
            font.bold: true
            font.letterSpacing: 2.0
        }
    }

    // ------------------------------------------------------------- server sheet
    ServerSheet {
        id: serverSheet
        anchors.fill: parent
    }

    SettingsWindow {
        id: settingsWindow
    }

    DiagnosticsWindow {
        id: diagnosticsWindow
    }

    // ----------------------------------------------------------- notifications
    Rectangle {
        id: toast
        anchors.horizontalCenter: parent.horizontalCenter
        anchors.bottom: parent.bottom
        anchors.bottomMargin: 90
        width: Math.min(toastText.implicitWidth + 32, window.width - 48)
        height: 42
        radius: 21
        color: pal.surface
        border.color: pal.border
        opacity: 0
        visible: opacity > 0.01
        Behavior on opacity { NumberAnimation { duration: 220 } }

        Text {
            id: toastText
            anchors.centerIn: parent
            text: ""
            color: pal.text
            font.pixelSize: fnt.small
        }

        function show(message) {
            toastText.text = message;
            toast.opacity = 1;
            toastTimer.restart();
        }
        Timer {
            id: toastTimer
            interval: 2600
            onTriggered: toast.opacity = 0
        }
    }

    Connections {
        target: app
        function onMessageChanged() {
            if (app.state === "error") toast.show(app.message);
        }
    }

    Shortcut {
        sequence: "Ctrl+,"
        onActivated: settingsWindow.open()
    }
    Shortcut {
        sequence: "Ctrl+D"
        onActivated: diagnosticsWindow.open()
    }
    Shortcut {
        sequence: "Space"
        enabled: !connectButton.activeFocus
        onActivated: app.toggle()
    }

    onClosing: function (close) {
        app.shutdown();
    }
}
