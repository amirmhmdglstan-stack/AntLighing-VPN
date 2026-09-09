pragma ComponentBehavior: Bound

import QtQuick
import QtQuick.Shapes

// 🐜 The AntLighting mascot.
//
// A coherent vector character drawn entirely from paths — no bitmap artwork, so
// it scales crisply and can be recoloured per theme.  Three expressive states
// driven by the `state` property:
//
//   offline     eyes closed, slow breathing, antennae drifting, a floating "z"
//   connecting  head lifts, eyes narrow, sparks flicker around the body
//   connected   upright pose, lightning-bolt eyes, aura ring and orbiting bolts
//
// Performance: the aura and sparks are drawn on a single Canvas that only
// repaints on a timer while those states are active, and stops entirely when
// the mascot is asleep.

Item {
    id: root

    // NB: not called `state` or `palette` — QQuickItem already defines those,
    // and shadowing them silently breaks QML state handling.
    property string mood: "offline"   // offline | connecting | connected | error
    property var colors: null
    property bool reduceMotion: false

    readonly property bool awake: mood === "connected"
    readonly property bool working: mood === "connecting"
    readonly property color bodyColor: colors ? colors.antBody : "#26313F"
    readonly property color bodyLight: colors ? colors.antBodyLight : "#384859"
    readonly property color outline: colors ? colors.antOutline : "#0A0E13"
    readonly property color boltColor: awake ? (colors ? colors.bolt : "#FFE9A8")
                                             : (colors ? colors.accent : "#FFC61A")

    implicitWidth: 320
    implicitHeight: 320

    // ---------------------------------------------------------------- aura ring
    Shape {
        id: aura
        anchors.centerIn: parent
        width: parent.width * 0.86
        height: parent.height * 0.86
        opacity: root.awake ? 0.9 : (root.working ? 0.35 : 0.0)
        visible: opacity > 0.01
        Behavior on opacity { NumberAnimation { duration: 420; easing.type: Easing.OutCubic } }

        ShapePath {
            strokeWidth: root.awake ? 3 : 2
            strokeColor: root.boltColor
            fillColor: "transparent"
            PathAngleArc {
                centerX: aura.width / 2
                centerY: aura.height / 2
                radiusX: aura.width / 2 - 4
                radiusY: aura.height / 2 - 4
                startAngle: 0
                sweepAngle: 360
            }
        }

        RotationAnimation on rotation {
            running: root.awake && !root.reduceMotion
            from: 0
            to: 360
            duration: 9000
            loops: Animation.Infinite
        }
    }

    // ------------------------------------------------------- lightning effects
    Canvas {
        id: sparks
        anchors.fill: parent
        // Repaint only while the ant is awake or working, and only a few times
        // a second — enough to read as energy without burning CPU.
        property real phase: 0
        Timer {
            id: sparkTimer
            interval: 110
            repeat: true
            running: (root.awake || root.working) && !root.reduceMotion && sparks.visible
            onTriggered: { sparks.phase += 0.7; sparks.requestPaint() }
        }
        opacity: root.awake ? 1.0 : (root.working ? 0.55 : 0.0)
        visible: opacity > 0.01
        Behavior on opacity { NumberAnimation { duration: 380 } }
        renderStrategy: Canvas.Cooperative

        onPaint: {
            var ctx = getContext("2d")
            ctx.reset()
            var cx = width / 2
            var cy = height / 2
            var radius = Math.min(width, height) * 0.40
            var count = root.awake ? 5 : 3
            for (var i = 0; i < count; i++) {
                var angle = (phase * 0.6 + i * (Math.PI * 2 / count))
                var jitter = Math.sin(phase * 1.7 + i * 2.1) * 0.35
                var r0 = radius + jitter * 12
                var x = cx + Math.cos(angle) * r0
                var y = cy + Math.sin(angle) * r0
                drawBolt(ctx, x, y, 15 + (i % 3) * 5, angle, root.boltColor)
            }
        }

        function drawBolt(ctx, x, y, size, angle, color) {
            ctx.save()
            ctx.translate(x, y)
            ctx.rotate(angle + Math.PI / 2)
            // Three passes: wide+soft, medium, thin+bright — a cheap glow.
            var passes = [[size * 0.55, 0.14], [size * 0.32, 0.30], [size * 0.15, 0.95]]
            for (var p = 0; p < passes.length; p++) {
                ctx.beginPath()
                ctx.moveTo(0, -size)
                ctx.lineTo(size * 0.28, -size * 0.15)
                ctx.lineTo(size * 0.06, -size * 0.10)
                ctx.lineTo(size * 0.20, size * 0.85)
                ctx.lineTo(-size * 0.10, size * 0.02)
                ctx.lineTo(size * 0.10, 0)
                ctx.closePath()
                ctx.globalAlpha = passes[p][1]
                ctx.fillStyle = color
                ctx.strokeStyle = color
                ctx.lineWidth = passes[p][0]
                ctx.lineJoin = "round"
                ctx.stroke()
                ctx.fill()
            }
            ctx.restore()
        }
    }

    // ---------------------------------------------------------------- the ant
    Item {
        id: ant
        anchors.centerIn: parent
        width: parent.width * 0.62
        height: parent.height * 0.62
        // Sleeping: a slow breath.  Awake: sits up and forward.
        scale: root.awake ? 1.04 : 1.0
        y: root.awake ? -6 : (root.working ? -2 : 0)
        Behavior on scale { NumberAnimation { duration: 420; easing.type: Easing.OutBack } }
        Behavior on y { NumberAnimation { duration: 420; easing.type: Easing.OutCubic } }

        SequentialAnimation on scale {
            running: !root.awake && !root.working && !root.reduceMotion
            loops: Animation.Infinite
            NumberAnimation { to: 1.018; duration: 2100; easing.type: Easing.InOutSine }
            NumberAnimation { to: 1.0; duration: 2100; easing.type: Easing.InOutSine }
        }

        // ground shadow
        Shape {
            id: shadow
            anchors.horizontalCenter: parent.horizontalCenter
            y: parent.height * 0.86
            width: parent.width * 0.62
            height: parent.height * 0.09
            opacity: 0.28
            ShapePath {
                fillColor: root.outline
                PathAngleArc {
                    centerX: shadow.width / 2; centerY: shadow.height / 2
                    radiusX: shadow.width / 2; radiusY: shadow.height / 2
                    startAngle: 0; sweepAngle: 360
                }
            }
        }

        // ---- legs -------------------------------------------------------
        Repeater {
            id: legs
            model: [
                { side: -1, index: 0 }, { side: -1, index: 1 }, { side: -1, index: 2 },
                { side: 1, index: 0 },  { side: 1, index: 1 },  { side: 1, index: 2 }
            ]
            delegate: Item {
                id: legRoot
                readonly property int side: modelData.side
                readonly property int index: modelData.index
                x: ant.width * 0.5 + side * ant.width * 0.02 - ant.width * 0.012
                y: ant.height * (0.44 + index * 0.10)
                width: ant.width * 0.024
                height: ant.height * 0.30
                transformOrigin: Item.Top
                rotation: side * (28 + index * 9) + (root.awake ? side * 6 : 0)
                Behavior on rotation { NumberAnimation { duration: 420 } }

                SequentialAnimation on rotation {
                    running: !root.reduceMotion && legRoot.visible
                    loops: Animation.Infinite
                    // a subtle idle shuffle, offset per leg so it reads as walking
                    NumberAnimation {
                        to: legRoot.rotation + (legRoot.index % 2 === 0 ? 3.5 : -3.5)
                        duration: 1400 + legRoot.index * 260
                        easing.type: Easing.InOutSine
                    }
                    NumberAnimation {
                        to: legRoot.rotation
                        duration: 1400 + legRoot.index * 260
                        easing.type: Easing.InOutSine
                    }
                }

                Shape {
                    anchors.fill: parent
                    ShapePath {
                        strokeColor: root.outline
                        strokeWidth: 3
                        fillColor: root.bodyLight
                        startX: legRoot.width / 2; startY: 0
                        PathLine { x: legRoot.width * 0.55; y: legRoot.height * 0.55 }
                        PathLine { x: legRoot.width * 0.5 + legRoot.side * legRoot.width * 0.6; y: legRoot.height }
                    }
                }
            }
        }

        // ---- abdomen ----------------------------------------------------
        Shape {
            id: abdomen
            x: ant.width * 0.08
            y: ant.height * 0.44
            width: ant.width * 0.42
            height: ant.height * 0.44
            ShapePath {
                strokeWidth: 3
                strokeColor: root.outline
                fillColor: root.bodyColor
                PathAngleArc {
                    centerX: abdomen.width / 2; centerY: abdomen.height / 2
                    radiusX: abdomen.width / 2; radiusY: abdomen.height / 2
                    startAngle: 0; sweepAngle: 360
                }
            }
        }
        // abdomen stripes
        Repeater {
            model: 2
            delegate: Shape {
                x: abdomen.x + abdomen.width * (0.24 + index * 0.22)
                y: abdomen.y + abdomen.height * 0.14
                width: abdomen.width * 0.06
                height: abdomen.height * 0.72
                opacity: 0.55
                ShapePath {
                    fillColor: root.outline
                    PathAngleArc {
                        centerX: width / 2; centerY: height / 2
                        radiusX: width / 2; radiusY: height / 2
                        startAngle: 0; sweepAngle: 360
                    }
                }
            }
        }

        // ---- thorax -----------------------------------------------------
        Shape {
            id: thorax
            x: ant.width * 0.36
            y: ant.height * 0.42
            width: ant.width * 0.26
            height: ant.height * 0.26
            ShapePath {
                strokeWidth: 3
                strokeColor: root.outline
                fillColor: root.bodyLight
                PathAngleArc {
                    centerX: thorax.width / 2; centerY: thorax.height / 2
                    radiusX: thorax.width / 2; radiusY: thorax.height / 2
                    startAngle: 0; sweepAngle: 360
                }
            }
        }

        // ---- head -------------------------------------------------------
        Item {
            id: head
            x: ant.width * 0.56
            y: ant.height * (root.awake ? 0.26 : 0.32)
            width: ant.width * 0.34
            height: ant.height * 0.32
            Behavior on y { NumberAnimation { duration: 420; easing.type: Easing.OutCubic } }

            // antennae
            Repeater {
                model: [-1, 1]
                delegate: Item {
                    id: antennaRoot
                    readonly property int side: modelData
                    x: head.width * 0.42 + side * head.width * 0.10
                    y: head.height * 0.06
                    width: 4
                    height: head.height * 0.52
                    transformOrigin: Item.Bottom
                    rotation: side * 22

                    SequentialAnimation on rotation {
                        running: !root.reduceMotion
                        loops: Animation.Infinite
                        NumberAnimation {
                            to: side * (root.awake ? 34 : 30)
                            duration: root.awake ? 700 : 2400
                            easing.type: Easing.InOutSine
                        }
                        NumberAnimation {
                            to: side * (root.awake ? 14 : 16)
                            duration: root.awake ? 700 : 2400
                            easing.type: Easing.InOutSine
                        }
                    }

                    Shape {
                        anchors.fill: parent
                        ShapePath {
                            strokeColor: root.outline
                            strokeWidth: 3.5
                            fillColor: "transparent"
                            startX: antennaRoot.width / 2; startY: antennaRoot.height
                            PathQuad {
                                x: antennaRoot.width / 2 + antennaRoot.side * antennaRoot.width * 2.2
                                y: 0
                                controlX: antennaRoot.width / 2 + antennaRoot.side * antennaRoot.width * 0.4
                                controlY: antennaRoot.height * 0.3
                            }
                        }
                    }
                    Shape {
                        x: antennaRoot.side * antennaRoot.width * 2.2 - 4
                        y: -8
                        width: 11
                        height: 11
                        ShapePath {
                            fillColor: root.awake ? root.boltColor : root.outline
                            PathAngleArc {
                                centerX: width / 2; centerY: height / 2
                                radiusX: width / 2; radiusY: height / 2
                                startAngle: 0; sweepAngle: 360
                            }
                        }
                    }
                }
            }

            // head capsule
            Shape {
                id: headShape
                anchors.fill: parent
                ShapePath {
                    strokeWidth: 3
                    strokeColor: root.outline
                    fillColor: root.bodyColor
                    PathAngleArc {
                        centerX: headShape.width / 2; centerY: headShape.height / 2
                        radiusX: headShape.width / 2; radiusY: headShape.height / 2
                        startAngle: 0; sweepAngle: 360
                    }
                }
            }

            // mandibles
            Repeater {
                model: [-1, 1]
                delegate: Shape {
                    x: head.width * 0.80
                    y: head.height * 0.52 + modelData * head.height * 0.13
                    width: head.width * 0.20
                    height: 5
                    rotation: modelData * 22
                    ShapePath {
                        strokeColor: root.outline
                        strokeWidth: 4
                        fillColor: "transparent"
                        startX: 0; startY: height / 2
                        PathQuad {
                            x: width; y: height / 2 + modelData * 5
                            controlX: width * 0.6; controlY: height / 2
                        }
                    }
                }
            }

            // ---- eyes ---------------------------------------------------
            // closed (asleep)
            Repeater {
                model: [-1, 1]
                delegate: Rectangle {
                    visible: !root.awake && !root.working
                    x: head.width * 0.62
                    y: head.height * (0.36 + (modelData > 0 ? 0.20 : 0))
                    width: head.width * 0.15
                    height: 4
                    radius: 2
                    color: root.outline
                }
            }
            // worried, narrowing (connecting)
            Repeater {
                model: [-1, 1]
                delegate: Item {
                    visible: root.working
                    x: head.width * 0.60
                    y: head.height * (0.32 + (modelData > 0 ? 0.22 : 0))
                    width: head.width * 0.20
                    height: head.height * 0.13
                    Rectangle {
                        anchors.fill: parent
                        radius: width / 2
                        color: colors ? colors.accentSoft : "#FFE08A"
                    }
                    // angled brow
                    Rectangle {
                        x: -2
                        y: -4
                        width: parent.width + 4
                        height: 4
                        radius: 2
                        color: root.outline
                        rotation: modelData > 0 ? 18 : -18
                    }
                }
            }
            // glowing lightning eyes (connected)
            Repeater {
                model: [-1, 1]
                delegate: Item {
                    visible: root.awake
                    x: head.width * 0.58
                    y: head.height * (0.26 + (modelData > 0 ? 0.24 : 0))
                    width: head.width * 0.22
                    height: head.height * 0.22

                    SequentialAnimation on opacity {
                        running: root.awake && !root.reduceMotion
                        loops: Animation.Infinite
                        NumberAnimation { to: 0.72; duration: 180 }
                        NumberAnimation { to: 1.0; duration: 180 }
                    }

                    Canvas {
                        anchors.fill: parent
                        property real t: 0
                        Timer {
                            interval: 130
                            repeat: true
                            running: root.awake && !root.reduceMotion
                            onTriggered: { parent.t += 1; parent.requestPaint() }
                        }
                        onPaint: {
                            var ctx = getContext("2d")
                            ctx.reset()
                            var w = width, h = height
                            var glow = 0.65 + 0.35 * Math.abs(Math.sin(t * 0.9))
                            var passes = [[w * 0.30, 0.18 * glow], [w * 0.16, 0.40 * glow],
                                          [w * 0.07, 0.95]]
                            for (var p = 0; p < passes.length; p++) {
                                ctx.beginPath()
                                ctx.moveTo(w * 0.55, 0)
                                ctx.lineTo(w * 0.20, h * 0.52)
                                ctx.lineTo(w * 0.45, h * 0.52)
                                ctx.lineTo(w * 0.30, h)
                                ctx.lineTo(w * 0.82, h * 0.40)
                                ctx.lineTo(w * 0.55, h * 0.40)
                                ctx.closePath()
                                ctx.globalAlpha = passes[p][1]
                                ctx.fillStyle = root.boltColor
                                ctx.strokeStyle = root.boltColor
                                ctx.lineWidth = passes[p][0]
                                ctx.lineJoin = "round"
                                ctx.stroke()
                                ctx.fill()
                            }
                        }
                    }
                }
            }
        }
    }

    // --------------------------------------------------------- sleep indicator
    Item {
        id: sleepZ
        anchors.horizontalCenter: parent.horizontalCenter
        y: parent.height * 0.10
        opacity: (!root.awake && !root.working) ? 1.0 : 0.0
        visible: opacity > 0.01
        Behavior on opacity { NumberAnimation { duration: 420 } }

        Repeater {
            model: 3
            delegate: Text {
                id: zText
                readonly property int idx: index
                x: idx * 22
                y: -idx * 16
                text: "z"
                font.pixelSize: 14 + idx * 5
                font.bold: true
                color: colors ? colors.textFaint : "#5C6B7C"
                opacity: 0.0

                SequentialAnimation on opacity {
                    running: sleepZ.visible && !root.reduceMotion
                    loops: Animation.Infinite
                    // staggered so the three z's drift upward in turn
                    PauseAnimation { duration: idx * 620 }
                    NumberAnimation { from: 0.0; to: 0.75; duration: 700 }
                    NumberAnimation { from: 0.75; to: 0.0; duration: 900 }
                    PauseAnimation { duration: (2 - idx) * 620 }
                }
            }
        }
    }

    // ------------------------------------------------------------- error badge
    Shape {
        id: errorBadge
        anchors.horizontalCenter: parent.horizontalCenter
        y: parent.height * 0.06
        width: 30
        height: 30
        opacity: root.mood === "error" ? 1.0 : 0.0
        visible: opacity > 0.01
        Behavior on opacity { NumberAnimation { duration: 300 } }
        ShapePath {
            fillColor: colors ? colors.danger : "#FF5C5C"
            PathAngleArc {
                centerX: errorBadge.width / 2; centerY: errorBadge.height / 2
                radiusX: errorBadge.width / 2; radiusY: errorBadge.height / 2
                startAngle: 0; sweepAngle: 360
            }
        }
        Text {
            anchors.centerIn: parent
            text: "!"
            font.pixelSize: 20
            font.bold: true
            color: "#FFFFFF"
        }
    }
}
