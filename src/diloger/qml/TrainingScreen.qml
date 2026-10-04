import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts

FocusScope {
    id: page

    focus: true

    // Derived from the live focus item instead of a manually synced flag: a
    // stale flag would arm the shortcuts while the user is typing.
    readonly property bool editingAnswer: answerField.activeFocus

    function refresh() { /* state arrives through signals */ }

    // The engine clears its own typed answer on next/skip, but the text field
    // keeps its own copy. Without this the previous answer stays on screen for
    // the next prompt and is captured again as part of it.
    // positionChanged fires on *every* state change, so the clear is keyed on
    // the prompt position instead: that only changes on next/skip/repeat, so
    // an answer being typed is never wiped by a pause or a timer tick.
    property string promptKey: ""

    function syncAnswerField() {
        var key = app.progressText
        if (key === page.promptKey) return
        page.promptKey = key
        answerField.text = ""
        answerField.cursorPosition = 0
    }

    Connections {
        target: app
        function onStateChanged() { page.refresh(); page.syncAnswerField() }
        function onPositionChanged() { page.refresh(); page.syncAnswerField() }
        function onPromptVisibilityChanged() { page.refresh() }
        function onTimerChanged() { page.refresh() }
    }

    // ---- shortcuts ------------------------------------------------------
    // While the answer field has focus, only Escape is intercepted. Every
    // other key (Space, letters, Enter for a new line) belongs to the editor.

    Keys.onPressed: function (event) {
        if (page.editingAnswer) {
            if (event.key === Qt.Key_Escape) {
                app.stopSession()
                event.accepted = true
            }
            return
        }
        switch (event.key) {
        case Qt.Key_Space: app.togglePause(); event.accepted = true; break
        case Qt.Key_Return:
        case Qt.Key_Enter: app.next(); event.accepted = true; break
        case Qt.Key_R: app.repeat(); event.accepted = true; break
        case Qt.Key_V: app.reveal(); event.accepted = true; break
        case Qt.Key_S: app.skip(); event.accepted = true; break
        case Qt.Key_Escape: app.stopSession(); event.accepted = true; break
        }
    }

    ColumnLayout {
        anchors.fill: parent
        spacing: theme.gap

        // ---- header ----------------------------------------------------

        RowLayout {
            Layout.fillWidth: true
            spacing: 18

            ColumnLayout {
                spacing: 3
                Text {
                    text: app.progressText
                    color: theme.text
                    font.pixelSize: 24
                    font.weight: Font.DemiBold
                }
                Text {
                    text: app.modeText
                    color: theme.textDim
                    font.pixelSize: 13
                }
            }

            Item { Layout.fillWidth: true }

            Rectangle {
                visible: app.isRecording
                Layout.preferredWidth: 132
                Layout.preferredHeight: 34
                radius: 17
                color: theme.dangerSurface
                border.color: theme.record
                border.width: 1

                Row {
                    anchors.centerIn: parent
                    spacing: 8
                    Rectangle {
                        width: 11; height: 11; radius: 6
                        color: theme.record
                        anchors.verticalCenter: parent.verticalCenter
                    }
                    Text {
                        text: "RECORDING"
                        color: theme.text
                        font.pixelSize: 12
                        font.letterSpacing: 1
                        anchors.verticalCenter: parent.verticalCenter
                    }
                }
            }

            Rectangle {
                visible: app.isPaused
                Layout.preferredWidth: 96
                Layout.preferredHeight: 34
                radius: 17
                color: theme.warnSurface
                border.color: theme.warn
                border.width: 1
                Text {
                    anchors.centerIn: parent
                    text: "PAUSED"
                    color: theme.text
                    font.pixelSize: 12
                    font.letterSpacing: 1
                }
            }
        }

        // ---- timer -----------------------------------------------------

        Rectangle {
            Layout.fillWidth: true
            Layout.preferredHeight: 8
            visible: app.modeText.indexOf("Fixed Timer") >= 0
            color: theme.panelAlt
            radius: 4

            Rectangle {
                width: parent.width * app.timerFraction
                height: parent.height
                radius: 4
                color: app.timerFraction < 0.25 ? theme.warn : theme.accent
                Behavior on width { NumberAnimation { duration: 120 } }
            }
        }

        RowLayout {
            Layout.fillWidth: true
            visible: app.modeText.indexOf("Fixed Timer") >= 0
            Text {
                text: app.timerRemaining.toFixed(1) + " s left to answer"
                color: theme.textDim
                font.pixelSize: 13
            }
            Item { Layout.fillWidth: true }
        }

        // ---- prompt area -----------------------------------------------

        Rectangle {
            Layout.fillWidth: true
            Layout.fillHeight: true
            color: theme.panel
            border.color: app.isLastPrompt ? theme.warn : theme.border
            border.width: 1
            radius: theme.radius

            StackLayout {
                id: promptStack
                anchors.fill: parent
                anchors.margins: 26
                currentIndex: {
                    if (app.isLastPrompt) return 2
                    if (app.promptVisible) return 1
                    return 0
                }

                // hidden
                ColumnLayout {
                    spacing: 10
                    Text {
                        Layout.alignment: Qt.AlignHCenter
                        text: "Listen to the prompt"
                        color: theme.text
                        font.pixelSize: 22
                    }
                    Text {
                        Layout.alignment: Qt.AlignHCenter
                        text: "Text is hidden. Press V to reveal it."
                        color: theme.textDim
                        font.pixelSize: 14
                    }
                }

                // revealed / text prompt
                ScrollView {
                    Text {
                        id: promptLabel
                        text: app.promptText
                        color: theme.text
                        font.pixelSize: 24
                        wrapMode: Text.WordWrap
                        width: parent.width
                    }
                }

                // at end
                ColumnLayout {
                    spacing: 12
                    Text {
                        Layout.alignment: Qt.AlignHCenter
                        text: "Last prompt"
                        color: theme.text
                        font.pixelSize: 22
                    }
                    Text {
                        Layout.alignment: Qt.AlignHCenter
                        Layout.maximumWidth: 420
                        text: "This is the final item. The session does not loop on its own — Stop here, or start a new pass from the library."
                        color: theme.textDim
                        font.pixelSize: 14
                        wrapMode: Text.WordWrap
                        horizontalAlignment: Text.AlignHCenter
                    }
                }
            }
        }

        // ---- answer field (written mode) --------------------------------

        Rectangle {
            Layout.fillWidth: true
            Layout.preferredHeight: 140
            visible: app.isTyping
            color: theme.panel
            border.color: answerField.activeFocus ? theme.accent : theme.border
            radius: theme.radius

            ColumnLayout {
                anchors.fill: parent
                anchors.margins: 10
                spacing: 8

                ScrollView {
                    Layout.fillWidth: true
                    Layout.fillHeight: true

                    TextArea {
                        id: answerField
                        focus: true
                        placeholderText: "Type your answer… Return adds a new line."
                        color: theme.text
                        placeholderTextColor: theme.textDim
                        font.pixelSize: 16
                        selectByMouse: true
                        wrapMode: TextArea.Wrap
                        background: null
                        onTextChanged: app.setTypedAnswer(text)
                        Keys.onEscapePressed: app.stopSession()
                        Keys.onReturnPressed: function (event) {
                            if (event.modifiers & Qt.ControlModifier) {
                                app.setTypedAnswer(answerField.text)
                                app.next()
                                event.accepted = true
                            }
                        }
                        Keys.onEnterPressed: function (event) {
                            if (event.modifiers & Qt.ControlModifier) {
                                app.setTypedAnswer(answerField.text)
                                app.next()
                                event.accepted = true
                            }
                        }
                    }
                }

                RowLayout {
                    Layout.fillWidth: true
                    spacing: 10

                    Text {
                        Layout.fillWidth: true
                        text: answerField.text.length + " characters · stored locally only"
                        color: theme.textDim
                        font.pixelSize: 12
                        elide: Text.ElideRight
                    }
                    Button {
                        text: "Submit & next  (Ctrl+Return)"
                        enabled: app.canNext
                        onClicked: {
                            app.setTypedAnswer(answerField.text)
                            app.next()
                        }
                    }
                }
            }
        }

        // ---- controls ----------------------------------------------------

        RowLayout {
            Layout.fillWidth: true
            spacing: 10

            Button {
                text: "Repeat  (R)"
                Layout.preferredHeight: 54
                Layout.fillWidth: true
                enabled: !app.isPaused && !app.isAtEnd
                onClicked: app.repeat()
            }
            Button {
                text: "Reveal  (V)"
                Layout.preferredHeight: 54
                Layout.fillWidth: true
                enabled: !app.isAtEnd
                onClicked: {
                    if (app.promptVisible && app.modeText.indexOf("Text Prompt") < 0) {
                        app.hidePrompt()
                    } else {
                        app.reveal()
                    }
                }
            }
            Button {
                text: "Skip  (S)"
                Layout.preferredHeight: 54
                Layout.fillWidth: true
                enabled: !app.isPaused && !app.isAtEnd
                onClicked: app.skip()
            }
            Button {
                text: "Pause  (Space)"
                Layout.preferredHeight: 54
                Layout.fillWidth: true
                enabled: !app.isAtEnd
                onClicked: app.togglePause()
            }
        }

        RowLayout {
            Layout.fillWidth: true
            spacing: 10

            Button {
                text: "Stop  (Esc)"
                Layout.preferredHeight: 54
                Layout.preferredWidth: 170
                onClicked: app.stopSession()
            }
            Item { Layout.fillWidth: true }
            Button {
                text: "Next  (Return)"
                highlighted: true
                Layout.preferredHeight: 54
                Layout.preferredWidth: 210
                enabled: app.canNext
                onClicked: app.next()
            }
        }
    }
}
