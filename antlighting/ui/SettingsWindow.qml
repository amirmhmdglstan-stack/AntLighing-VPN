import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts
import "theme.js" as Theme

// Advanced settings.  Grouped by concern, with plain-language explanations —
// the raw Xray option surface is deliberately not exposed here.

Window {
    id: win
    width: 560
    height: 660
    minimumWidth: 420
    minimumHeight: 480
    title: qsTr("Advanced settings")
    color: P.bg

    readonly property var P: Theme.theme(app.setting("ui.theme"),
                                         Qt.styleHints.colorScheme === Qt.Dark)
    readonly property var F: Theme.fontSizes()

    function open() { show(); raise(); requestActivate() }

    // ------------------------------------------------------------------ helpers
    component Section: ColumnLayout {
        spacing: 4
        Layout.fillWidth: true
        Layout.topMargin: 10
        property string heading: ""
        property string explanation: ""
        Text {
            text: parent.heading
            color: win.P.text
            font.pixelSize: win.F.subtitle
            font.bold: true
        }
        Text {
            Layout.fillWidth: true
            text: parent.explanation
            color: win.P.textDim
            font.pixelSize: win.F.small
            wrapMode: Text.WordWrap
            visible: text.length > 0
        }
    }

    component Toggle: RowLayout {
        Layout.fillWidth: true
        spacing: 12
        property string label: ""
        property string help: ""
        property string key: ""
        ColumnLayout {
            Layout.fillWidth: true
            spacing: 0
            Text { text: parent.parent.label; color: win.P.text; font.pixelSize: win.F.body }
            Text {
                Layout.fillWidth: true
                text: parent.parent.help
                color: win.P.textFaint
                font.pixelSize: win.F.tiny
                wrapMode: Text.WordWrap
                visible: text.length > 0
            }
        }
        Switch {
            checked: app.setting(parent.parent.key) === true
            onToggled: app.setSetting(parent.parent.key, checked)
            focusPolicy: Qt.TabFocus
            Accessible.name: parent.parent.label
        }
    }

    component Picker: RowLayout {
        Layout.fillWidth: true
        spacing: 12
        property string label: ""
        property string help: ""
        property string key: ""
        property var options: []
        ColumnLayout {
            Layout.fillWidth: true
            spacing: 0
            Text { text: parent.parent.label; color: win.P.text; font.pixelSize: win.F.body }
            Text {
                Layout.fillWidth: true
                text: parent.parent.help
                color: win.P.textFaint
                font.pixelSize: win.F.tiny
                wrapMode: Text.WordWrap
                visible: text.length > 0
            }
        }
        ComboBox {
            id: combo
            Layout.preferredWidth: 170
            model: parent.parent.options
            currentIndex: Math.max(0, parent.parent.options.indexOf(String(app.setting(parent.parent.key))))
            onActivated: app.setSetting(parent.parent.key, parent.parent.options[currentIndex])
            focusPolicy: Qt.TabFocus
            Accessible.name: parent.parent.label
        }
    }

    // -------------------------------------------------------------------- body
    ColumnLayout {
        anchors.fill: parent
        spacing: 0

        TabBar {
            id: tabs
            Layout.fillWidth: true
            currentIndex: 0
            Repeater {
                model: [qsTr("Connection"), qsTr("Servers"), qsTr("Sources"), qsTr("Appearance")]
                TabButton {
                    text: modelData
                    width: implicitWidth
                    Accessible.name: modelData
                }
            }
        }

        StackLayout {
            Layout.fillWidth: true
            Layout.fillHeight: true
            currentIndex: tabs.currentIndex

            // =============================================== connection
            Flickable {
                contentHeight: connCol.implicitHeight + 40
                clip: true
                ScrollBar.vertical: ScrollBar {}
                ColumnLayout {
                    id: connCol
                    anchors.left: parent.left
                    anchors.right: parent.right
                    anchors.margins: 20
                    spacing: 12

                    Section {
                        heading: qsTr("Tunnel")
                        explanation: qsTr("How AntLighting routes your traffic.")
                    }
                    Picker {
                        label: qsTr("Connection mode")
                        help: qsTr("Auto uses the full VPN tunnel when it can, and falls back to the system proxy. The tunnel needs administrator rights.")
                        key: "connection.mode"
                        options: ["auto", "tun", "proxy"]
                    }
                    RowLayout {
                        Layout.fillWidth: true
                        Text {
                            Layout.fillWidth: true
                            text: app.elevated ? qsTr("✓ Running with administrator rights — full VPN available")
                                               : qsTr("ⓘ Not elevated — the system proxy will be used")
                            color: app.elevated ? win.P.success : win.P.textDim
                            font.pixelSize: win.F.small
                            wrapMode: Text.WordWrap
                        }
                    }

                    Section {
                        heading: qsTr("Privacy of your traffic")
                    }
                    Toggle {
                        label: qsTr("Keep local network traffic direct")
                        help: qsTr("Printers, NAS boxes and other devices on your own network stay reachable.")
                        key: "connection.bypass_lan"
                    }
                    Toggle {
                        label: qsTr("Block ads and trackers")
                        help: qsTr("Uses the ad category from the geo data files shipped with the core.")
                        key: "connection.block_ads"
                    }
                    Toggle {
                        label: qsTr("Enable IPv6")
                        help: qsTr("Leave off unless you know your network has working IPv6.")
                        key: "connection.ipv6"
                    }
                    Toggle {
                        label: qsTr("TLS fragmentation")
                        help: qsTr("Splits the TLS handshake into smaller pieces. Helps a lot on networks that interfere with VPNs.")
                        key: "connection.fragment"
                    }

                    Section {
                        heading: qsTr("Performance")
                    }
                    Toggle {
                        label: qsTr("Multiplex connections")
                        help: qsTr("Packs several connections into one tunnel. Can help on high-latency links, but some servers reject it.")
                        key: "connection.mux_enabled"
                    }
                    Toggle {
                        label: qsTr("Reconnect automatically")
                        help: qsTr("Restart the tunnel if it drops.")
                        key: "connection.auto_reconnect"
                    }

                    Section {
                        heading: qsTr("Core")
                    }
                    Picker {
                        label: qsTr("Core log level")
                        help: qsTr("Higher levels write more to the log file. Leave at warning unless you are diagnosing a problem.")
                        key: "core.log_level"
                        options: ["none", "error", "warning", "info", "debug"]
                    }
                    RowLayout {
                        Layout.fillWidth: true
                        Text {
                            Layout.fillWidth: true
                            text: qsTr("Core: %1").arg(app.coreVersion)
                            color: win.P.textDim
                            font.pixelSize: win.F.small
                        }
                    }
                }
            }

            // ==================================================== servers
            Flickable {
                contentHeight: srvCol.implicitHeight + 40
                clip: true
                ScrollBar.vertical: ScrollBar {}
                ColumnLayout {
                    id: srvCol
                    anchors.left: parent.left
                    anchors.right: parent.right
                    anchors.margins: 20
                    spacing: 12

                    Section {
                        heading: qsTr("Automatic selection")
                        explanation: qsTr("AntLighting ranks servers by reliability first and speed second: a slightly slower server that keeps working beats a fast one that drops.")
                    }
                    RowLayout {
                        Layout.fillWidth: true
                        Text {
                            text: qsTr("Test timeout")
                            color: win.P.text
                            font.pixelSize: win.F.body
                            Layout.fillWidth: true
                        }
                        SpinBox {
                            id: timeoutSpin
                            from: 3; to: 30; stepSize: 1
                            value: app.setting("servers.test_timeout")
                            editable: true
                            onValueModified: app.setSetting("servers.test_timeout", value)
                            textFromValue: function (v) { return v + " s" }
                            focusPolicy: Qt.TabFocus
                            Accessible.name: qsTr("Test timeout in seconds")
                        }
                    }
                    RowLayout {
                        Layout.fillWidth: true
                        Text {
                            text: qsTr("Slow threshold")
                            color: win.P.text
                            font.pixelSize: win.F.body
                            Layout.fillWidth: true
                        }
                        SpinBox {
                            from: 200; to: 5000; stepSize: 100
                            value: app.setting("servers.slow_threshold_ms")
                            editable: true
                            onValueModified: app.setSetting("servers.slow_threshold_ms", value)
                            textFromValue: function (v) { return v + " ms" }
                            focusPolicy: Qt.TabFocus
                            Accessible.name: qsTr("Latency above which a server counts as slow")
                        }
                    }
                    RowLayout {
                        Layout.fillWidth: true
                        Text {
                            text: qsTr("Parallel tests")
                            color: win.P.text
                            font.pixelSize: win.F.body
                            Layout.fillWidth: true
                        }
                        SpinBox {
                            from: 1; to: 64; stepSize: 4
                            value: app.setting("servers.test_concurrency")
                            editable: true
                            onValueModified: app.setSetting("servers.test_concurrency", value)
                            focusPolicy: Qt.TabFocus
                            Accessible.name: qsTr("How many servers to test at once")
                        }
                    }

                    Section {
                        heading: qsTr("Pool")
                    }
                    RowLayout {
                        Layout.fillWidth: true
                        spacing: 10
                        Text {
                            Layout.fillWidth: true
                            text: qsTr("%1 servers stored, %2 confirmed working")
                                    .arg(app.poolTotal).arg(app.poolWorking)
                            color: win.P.textDim
                            font.pixelSize: win.F.small
                        }
                        Button {
                            text: qsTr("Test now")
                            onClicked: app.testServers()
                            focusPolicy: Qt.TabFocus
                        }
                        Button {
                            text: qsTr("Refresh")
                            onClicked: app.refreshSources()
                            focusPolicy: Qt.TabFocus
                        }
                    }

                    Section {
                        heading: qsTr("Import your own servers")
                        explanation: qsTr("Paste share links (vless://, vmess://, trojan://, ss://…). Imported servers are marked as trusted and are never overwritten by public sources.")
                    }
                    TextArea {
                        id: importBox
                        Layout.fillWidth: true
                        Layout.preferredHeight: 90
                        placeholderText: qsTr("vless://…")
                        wrapMode: TextArea.Wrap
                        color: win.P.text
                        background: Rectangle {
                            radius: Theme.radius.md
                            color: win.P.bgAlt
                            border.color: win.P.border
                        }
                        focusPolicy: Qt.TabFocus
                        Accessible.name: qsTr("Server share links to import")
                    }
                    RowLayout {
                        Layout.fillWidth: true
                        Button {
                            text: qsTr("Import")
                            focusPolicy: Qt.TabFocus
                            onClicked: {
                                var n = app.importConfigs(importBox.text);
                                importBox.text = "";
                                importStatus.text = qsTr("%1 server(s) imported").arg(n);
                            }
                        }
                        Text {
                            id: importStatus
                            Layout.fillWidth: true
                            color: win.P.success
                            font.pixelSize: win.F.small
                        }
                    }
                }
            }

            // ==================================================== sources
            Flickable {
                contentHeight: srcCol.implicitHeight + 40
                clip: true
                ScrollBar.vertical: ScrollBar {}
                ColumnLayout {
                    id: srcCol
                    anchors.left: parent.left
                    anchors.right: parent.right
                    anchors.margins: 20
                    spacing: 12

                    Section {
                        heading: qsTr("Where servers come from")
                        explanation: qsTr("AntLighting collects publicly posted configurations. Any source can disappear or change format — the app keeps working with whatever it can reach, and you can switch sources off here.")
                    }

                    Repeater {
                        id: sourceList
                        model: app.settingSources()
                        RowLayout {
                            Layout.fillWidth: true
                            spacing: 10
                            ColumnLayout {
                                Layout.fillWidth: true
                                spacing: 0
                                Text {
                                    text: modelData.label
                                    color: win.P.text
                                    font.pixelSize: win.F.body
                                }
                                Text {
                                    Layout.fillWidth: true
                                    text: modelData.kind + (modelData.urls && modelData.urls.length
                                                            ? " · " + modelData.urls.length + " endpoint(s)" : "")
                                    color: win.P.textFaint
                                    font.pixelSize: win.F.tiny
                                    elide: Text.ElideRight
                                }
                            }
                            Switch {
                                checked: modelData.enabled === true
                                focusPolicy: Qt.TabFocus
                                Accessible.name: qsTr("Enable ") + modelData.label
                                onToggled: {
                                    var specs = app.settingSources();
                                    for (var i = 0; i < specs.length; i++) {
                                        if (specs[i].id === modelData.id) specs[i].enabled = checked;
                                    }
                                    app.saveSources(specs);
                                }
                            }
                        }
                    }

                    Rectangle { Layout.fillWidth: true; Layout.preferredHeight: 1; color: win.P.border }

                    RowLayout {
                        Layout.fillWidth: true
                        Text {
                            Layout.fillWidth: true
                            text: qsTr("Refresh every")
                            color: win.P.text
                            font.pixelSize: win.F.body
                        }
                        SpinBox {
                            from: 1; to: 168; stepSize: 1
                            value: Math.round(app.setting("sources.refresh_interval_minutes") / 60)
                            editable: true
                            onValueModified: app.setSetting("sources.refresh_interval_minutes", value * 60)
                            textFromValue: function (v) { return v + " h" }
                            focusPolicy: Qt.TabFocus
                            Accessible.name: qsTr("Refresh interval in hours")
                        }
                    }
                    Toggle {
                        label: qsTr("Refresh automatically")
                        help: qsTr("Refreshes in the background. AntLighting never tests servers continuously.")
                        key: "sources.auto_refresh"
                    }

                    RowLayout {
                        Layout.fillWidth: true
                        spacing: 10
                        Button {
                            text: qsTr("Refresh now")
                            onClicked: app.refreshSources()
                            focusPolicy: Qt.TabFocus
                        }
                        Button {
                            text: qsTr("Restore defaults")
                            onClicked: app.restoreDefaultSources()
                            focusPolicy: Qt.TabFocus
                        }
                    }
                }
            }

            // ================================================== appearance
            Flickable {
                contentHeight: appCol.implicitHeight + 40
                clip: true
                ScrollBar.vertical: ScrollBar {}
                ColumnLayout {
                    id: appCol
                    anchors.left: parent.left
                    anchors.right: parent.right
                    anchors.margins: 20
                    spacing: 12

                    Section {
                        heading: qsTr("Appearance")
                    }
                    Picker {
                        label: qsTr("Theme")
                        key: "ui.theme"
                        options: ["system", "dark", "light"]
                    }
                    Toggle {
                        label: qsTr("Animations")
                        help: qsTr("Turn off to reduce motion and CPU use.")
                        key: "ui.animations"
                    }
                    Toggle {
                        label: qsTr("Start minimised")
                        key: "ui.start_minimized"
                    }

                    Section {
                        heading: qsTr("Privacy")
                        explanation: qsTr("AntLighting has no telemetry, no analytics and no crash reporting. Nothing about your browsing, DNS queries or server credentials is ever uploaded. Configuration collection only downloads the public lists you enable above.")
                    }

                    Section {
                        heading: qsTr("Danger zone")
                    }
                    RowLayout {
                        Layout.fillWidth: true
                        spacing: 10
                        Button {
                            text: qsTr("Reset settings")
                            focusPolicy: Qt.TabFocus
                            onClicked: app.resetSettings()
                        }
                        Button {
                            text: qsTr("Diagnostics")
                            focusPolicy: Qt.TabFocus
                            onClicked: diagnosticsWindow.open()
                        }
                    }
                }
            }
        }

        Rectangle { Layout.fillWidth: true; Layout.preferredHeight: 1; color: P.border }

        // ----------------------------------------------------------------- footer
        Item {
            Layout.fillWidth: true
            Layout.preferredHeight: 52
            Button {
                anchors.verticalCenter: parent.verticalCenter
                anchors.right: parent.right
                anchors.rightMargin: 16
                text: qsTr("Done")
                focusPolicy: Qt.TabFocus
                onClicked: win.close()
            }
        }
    }
}
