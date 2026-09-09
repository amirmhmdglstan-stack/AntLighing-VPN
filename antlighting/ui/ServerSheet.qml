import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts
import "theme.js" as Theme

// The server picker.  Deliberately free of protocol internals: name, flag,
// latency and a one-word verdict are all a normal user needs.

Item {
    id: sheet
    property alias opened: popup.opened

    readonly property var P: Theme.theme(app.setting("ui.theme"),
                                         Qt.styleHints.colorScheme === Qt.Dark)
    readonly property var F: Theme.fontSizes()

    function open() {
        app.servers.refresh();
        popup.open();
    }

    Popup {
        id: popup
        anchors.centerIn: parent
        width: Math.min(400, sheet.width - 24)
        height: Math.min(600, sheet.height - 60)
        padding: 0
        modal: true
        closePolicy: Popup.CloseOnEscape | Popup.CloseOnPressOutside

        background: Rectangle {
            radius: Theme.radius.lg
            color: sheet.P.surface
            border.color: sheet.P.border
        }

        contentItem: ColumnLayout {
            spacing: 0

            // ---- header -------------------------------------------------
            Item {
                Layout.fillWidth: true
                Layout.preferredHeight: 58

                Text {
                    anchors.verticalCenter: parent.verticalCenter
                    anchors.left: parent.left
                    anchors.leftMargin: 20
                    text: qsTr("🌍 Servers")
                    color: sheet.P.text
                    font.pixelSize: sheet.F.title
                    font.bold: true
                }
                Text {
                    anchors.verticalCenter: parent.verticalCenter
                    anchors.left: parent.left
                    anchors.leftMargin: 20
                    anchors.top: parent.verticalCenter
                    text: qsTr("%1 available · %2 working").arg(app.poolTotal).arg(app.poolWorking)
                    color: sheet.P.textFaint
                    font.pixelSize: sheet.F.tiny
                }

                RoundButton {
                    anchors.verticalCenter: parent.verticalCenter
                    anchors.right: parent.right
                    anchors.rightMargin: 10
                    width: 34; height: 34; radius: 17
                    flat: true
                    focusPolicy: Qt.TabFocus
                    Accessible.name: qsTr("Close")
                    background: Rectangle { radius: 17; color: hoverBg.color }
                    Rectangle {
                        id: hoverBg
                        anchors.fill: parent
                        radius: 17
                        color: "transparent"
                    }
                    contentItem: Text {
                        text: "✕"; color: sheet.P.textDim
                        font.pixelSize: 14
                        horizontalAlignment: Text.AlignHCenter
                        verticalAlignment: Text.AlignVCenter
                    }
                    onClicked: popup.close()
                }
            }

            Rectangle { Layout.fillWidth: true; Layout.preferredHeight: 1; color: sheet.P.border }

            // ---- list ---------------------------------------------------
            ListView {
                id: list
                Layout.fillWidth: true
                Layout.fillHeight: true
                clip: true
                model: app.servers.servers
                boundsBehavior: Flickable.StopAtBounds
                ScrollBar.vertical: ScrollBar { policy: ScrollBar.AsNeeded }

                delegate: ItemDelegate {
                    id: row
                    required property var modelData
                    width: ListView.view.width
                    height: 58
                    focusPolicy: Qt.TabFocus
                    highlighted: modelData.selected
                    Accessible.name: modelData.name + " " + modelData.statusText

                    background: Rectangle {
                        color: row.hovered ? Theme.theme(app.setting("ui.theme"),
                                                         Qt.styleHints.colorScheme === Qt.Dark).surfaceHover
                                           : "transparent"
                        Rectangle {
                            visible: modelData.selected
                            width: 3
                            height: parent.height
                            color: sheet.P.accent
                        }
                    }

                    contentItem: RowLayout {
                        spacing: 12
                        Text {
                            text: modelData.flag
                            font.pixelSize: 20
                            Layout.preferredWidth: 28
                            horizontalAlignment: Text.AlignHCenter
                        }
                        ColumnLayout {
                            Layout.fillWidth: true
                            spacing: 1
                            Text {
                                Layout.fillWidth: true
                                text: modelData.name
                                color: sheet.P.text
                                font.pixelSize: sheet.F.body
                                font.bold: modelData.selected
                                elide: Text.ElideRight
                            }
                            Text {
                                Layout.fillWidth: true
                                text: modelData.subtitle
                                color: sheet.P.textFaint
                                font.pixelSize: sheet.F.tiny
                                elide: Text.ElideRight
                            }
                        }
                        ColumnLayout {
                            spacing: 1
                            Layout.alignment: Qt.AlignRight
                            Text {
                                Layout.alignment: Qt.AlignRight
                                text: modelData.latencyText
                                color: sheet.P.textDim
                                font.pixelSize: sheet.F.small
                                font.bold: true
                            }
                            Text {
                                Layout.alignment: Qt.AlignRight
                                text: modelData.statusText
                                color: Theme.statusColor(sheet.P, modelData.status)
                                font.pixelSize: sheet.F.tiny
                            }
                        }
                    }
                    onClicked: {
                        app.selectServer(modelData.identity);
                        popup.close();
                    }
                }
            }

            Rectangle { Layout.fillWidth: true; Layout.preferredHeight: 1; color: sheet.P.border }

            // ---- footer actions -----------------------------------------
            RowLayout {
                Layout.fillWidth: true
                Layout.margins: 14
                spacing: 10

                Button {
                    Layout.fillWidth: true
                    Layout.preferredHeight: 44
                    text: app.testing ? qsTr("Stop testing") : qsTr("Test servers")
                    focusPolicy: Qt.TabFocus
                    Accessible.name: text
                    background: Rectangle {
                        radius: Theme.radius.md
                        color: testBtn.pressed ? sheet.P.surfaceHover : sheet.P.surface
                        border.color: sheet.P.border
                    }
                    contentItem: Text {
                        text: parent.text
                        color: sheet.P.text
                        font.pixelSize: sheet.F.body
                        font.bold: true
                        horizontalAlignment: Text.AlignHCenter
                        verticalAlignment: Text.AlignVCenter
                    }
                    id: testBtn
                    onClicked: app.testServers()
                }

                Button {
                    id: refreshBtn
                    Layout.preferredWidth: 44
                    Layout.preferredHeight: 44
                    focusPolicy: Qt.TabFocus
                    Accessible.name: qsTr("Refresh the server list")
                    ToolTip.visible: hovered
                    ToolTip.text: qsTr("Refresh the server list")
                    background: Rectangle {
                        radius: Theme.radius.md
                        color: refreshBtn.pressed ? sheet.P.surfaceHover : sheet.P.surface
                        border.color: sheet.P.border
                    }
                    contentItem: Text {
                        text: "⟳"
                        color: sheet.P.text
                        font.pixelSize: 18
                        horizontalAlignment: Text.AlignHCenter
                        verticalAlignment: Text.AlignVCenter
                    }
                    onClicked: app.refreshSources()
                }
            }

            // ---- test progress ------------------------------------------
            Rectangle {
                Layout.fillWidth: true
                Layout.leftMargin: 14
                Layout.rightMargin: 14
                Layout.bottomMargin: 12
                Layout.preferredHeight: app.testing || app.collecting ? 34 : 0
                visible: Layout.preferredHeight > 0
                radius: Theme.radius.sm
                color: sheet.P.bgAlt
                Behavior on Layout.preferredHeight { NumberAnimation { duration: 200 } }

                RowLayout {
                    anchors.fill: parent
                    anchors.leftMargin: 10
                    anchors.rightMargin: 10
                    spacing: 8
                    BusyIndicator {
                        Layout.preferredWidth: 18
                        Layout.preferredHeight: 18
                        running: app.testing || app.collecting
                    }
                    Text {
                        Layout.fillWidth: true
                        text: app.collecting ? qsTr("Collecting servers…")
                                             : qsTr("Testing servers…")
                        color: sheet.P.textDim
                        font.pixelSize: sheet.F.tiny
                        elide: Text.ElideRight
                    }
                }
            }
        }
    }
}
