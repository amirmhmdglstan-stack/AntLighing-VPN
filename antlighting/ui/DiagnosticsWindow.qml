import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts
import "theme.js" as Theme

// Diagnostics.  Normal users see a readable summary; the raw logs are one tab
// away for anyone who needs them.

Window {
    id: win
    width: 640
    height: 560
    minimumWidth: 420
    minimumHeight: 360
    title: qsTr("Diagnostics")
    color: pal.bg

    readonly property var pal: Theme.theme(app.setting("ui.theme"),
                                         Qt.styleHints.colorScheme === Qt.Dark)
    readonly property var fnt: Theme.fontSizes()
    property var snapshot: ({})

    function open() {
        refresh();
        show(); raise(); requestActivate();
    }

    function refresh() {
        snapshot = app.diagnostics();
        summaryModel = [
            [qsTr("Core"), coreLabel()],
            [qsTr("Server"), app.serverName + (app.latencyText !== "--" ? " · " + app.latencyText : "")],
            [qsTr("Tunnel"), tunnelLabel()],
            [qsTr("Mode"), app.mode === "tun" ? qsTr("Full VPN tunnel") : qsTr("System proxy")],
            [qsTr("Administrator"), app.elevated ? qsTr("Yes") : qsTr("No")],
            [qsTr("Servers stored"), String(app.poolTotal)],
            [qsTr("Servers working"), String(app.poolWorking)],
            [qsTr("State"), app.state],
        ];
    }

    property var summaryModel: []

    function coreLabel() {
        var s = snapshot && snapshot.core ? snapshot.core : {};
        if (s.state === "running") {
            var up = Math.round(s.uptime_seconds || 0);
            return qsTr("Running") + (up > 0 ? " · " + up + " s" : "")
                   + (s.restarts ? qsTr(" · %1 restart(s)").arg(s.restarts) : "");
        }
        return s.error ? String(s.error).substring(0, 90) : qsTr("Stopped");
    }

    function tunnelLabel() {
        var t = snapshot && snapshot.tunnel ? snapshot.tunnel : {};
        if (app.mode !== "tun") return qsTr("Not in use");
        if (t.active) return qsTr("Connected");
        return t.error ? String(t.error).substring(0, 90) : qsTr("Inactive");
    }

    ColumnLayout {
        anchors.fill: parent
        spacing: 0

        TabBar {
            id: tabs
            Layout.fillWidth: true
            Repeater {
                model: [qsTr("Summary"), qsTr("Sources"), qsTr("Logs"), qsTr("Core log")]
                TabButton { text: modelData; width: implicitWidth; Accessible.name: modelData }
            }
        }

        StackLayout {
            Layout.fillWidth: true
            Layout.fillHeight: true
            currentIndex: tabs.currentIndex

            // --------------------------------------------------------- summary
            ColumnLayout {
                Layout.margins: 18
                spacing: 8

                Repeater {
                    model: win.summaryModel
                    RowLayout {
                        Layout.fillWidth: true
                        spacing: 12
                        Text {
                            Layout.preferredWidth: 150
                            text: modelData[0]
                            color: win.pal.textDim
                            font.pixelSize: win.fnt.small
                        }
                        Text {
                            Layout.fillWidth: true
                            text: modelData[1]
                            color: win.pal.text
                            font.pixelSize: win.fnt.body
                            wrapMode: Text.WordWrap
                            textFormat: Text.PlainText
                        }
                    }
                }

                Item { Layout.fillHeight: true }

                RowLayout {
                    Layout.fillWidth: true
                    spacing: 10
                    Button {
                        text: qsTr("Refresh")
                        onClicked: win.refresh()
                        focusPolicy: Qt.TabFocus
                    }
                    Button {
                        text: qsTr("Copy to clipboard")
                        focusPolicy: Qt.TabFocus
                        onClicked: {
                            var lines = [];
                            for (var i = 0; i < win.summaryModel.length; i++)
                                lines.push(win.summaryModel[i][0] + ": " + win.summaryModel[i][1]);
                            Qt.application.clipboard = lines.join("\n");
                        }
                    }
                }
            }

            // --------------------------------------------------------- sources
            ListView {
                Layout.margins: 12
                clip: true
                model: app.sourceRows()
                ScrollBar.vertical: ScrollBar {}
                delegate: Rectangle {
                    width: ListView.view.width
                    height: 56
                    radius: Theme.radius.sm
                    color: win.pal.bgAlt
                    border.color: win.pal.border
                    Layout.bottomMargin: 6
                    RowLayout {
                        anchors.fill: parent
                        anchors.margins: 12
                        spacing: 10
                        ColumnLayout {
                            Layout.fillWidth: true
                            spacing: 0
                            Text {
                                text: modelData.label
                                color: win.pal.text
                                font.pixelSize: win.fnt.body
                                elide: Text.ElideRight
                                Layout.fillWidth: true
                            }
                            Text {
                                text: (modelData.enabled ? qsTr("enabled") : qsTr("disabled"))
                                      + " · " + qsTr("last fetch: %1 servers").arg(modelData.lastCount)
                                      + (modelData.lastError ? " · " + modelData.lastError : "")
                                color: modelData.lastError ? win.pal.warning : win.pal.textFaint
                                font.pixelSize: win.fnt.tiny
                                elide: Text.ElideRight
                                Layout.fillWidth: true
                            }
                        }
                    }
                }
            }

            // ------------------------------------------------------------ logs
            ScrollView {
                Layout.fillWidth: true
                Layout.fillHeight: true
                TextArea {
                    id: logArea
                    readOnly: true
                    wrapMode: TextArea.NoWrap
                    color: win.pal.textDim
                    font.family: "monospace"
                    font.pixelSize: 11
                    text: ""
                    background: Rectangle { color: win.pal.bgAlt }
                }
            }

            // -------------------------------------------------------- core log
            ScrollView {
                Layout.fillWidth: true
                Layout.fillHeight: true
                TextArea {
                    id: coreLogArea
                    readOnly: true
                    wrapMode: TextArea.NoWrap
                    color: win.pal.textDim
                    font.family: "monospace"
                    font.pixelSize: 11
                    text: ""
                    background: Rectangle { color: win.pal.bgAlt }
                }
            }
        }

        Rectangle { Layout.fillWidth: true; Layout.preferredHeight: 1; color: pal.border }

        Item {
            Layout.fillWidth: true
            Layout.preferredHeight: 48
            Button {
                anchors.verticalCenter: parent.verticalCenter
                anchors.right: parent.right
                anchors.rightMargin: 14
                text: qsTr("Close")
                focusPolicy: Qt.TabFocus
                onClicked: win.close()
            }
        }
    }

    onVisibleChanged: {
        if (visible) {
            logArea.text = app.logTail();
            coreLogArea.text = app.coreLogTail();
        }
    }
}
