import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts

FocusScope {
    id: page

    property var s: ({})
    property var voiceList: []
    property var presets: []

    function refresh() {
        s = app.setupState()
        voiceList = app.voices()
        presets = app.delayPresets()
        syncChecks()
    }

    // Qt permanently drops a declarative `checked` binding the moment the user
    // toggles a checkable control, after which the control keeps whatever state
    // the click gave it. In a group like Mode that leaves two options checked at
    // once, and the screen stops describing the session that will actually run.
    // The controller is the only source of truth, so re-assert every control
    // from it whenever the setup changes.
    function syncChecks() {
        if (s.mode === undefined) return
        spokenMode.checked = s.mode === "spoken"
        writtenMode.checked = s.mode === "written"
        sequentialOrder.checked = s.order === "sequential"
        randomOrder.checked = s.order === "randomWithoutRepetition"
        fixedTimer.checked = s.progression === "fixedTimer"
        manualAdvance.checked = s.progression === "manual"
        audioPrompt.checked = s.writtenPrompt === "audio"
        textPrompt.checked = s.writtenPrompt === "text"
        if (s.rate !== undefined) {
            rateSlider.value = s.rate
            rateLabel.text = Math.round(s.rate) + " wpm"
        }
        var voiceIndex = voiceList.indexOf(s.voice)
        voiceBox.currentIndex = voiceIndex >= 0 ? voiceIndex : 0
        recordBox.checked = s.record === true
        keepRecordingBox.checked = s.keepRecording === true
        saveTypedBox.checked = s.saveTyped === true
        for (var i = 0; i < delayPresetsRow.count; i++) {
            var item = delayPresetsRow.itemAt(i)
            if (item) item.checked = (s.delay === presets[i])
        }
    }

    Component.onCompleted: refresh()

    Connections {
        target: app
        function onSetupChanged() { page.refresh() }
    }

    ScrollView {
        anchors.fill: parent
        contentWidth: availableWidth

        ColumnLayout {
            width: page.width
            spacing: theme.gap

            RowLayout {
                Layout.fillWidth: true
                Layout.margins: 6
                spacing: theme.gap

                Label {
                    text: "Session setup"
                    font.pixelSize: 30
                    font.weight: Font.Bold
                    color: theme.text
                }
                Item { Layout.fillWidth: true }
                Button {
                    text: "Back"
                    onClicked: app.showLibrary()
                }
            }

            Rectangle {
                Layout.fillWidth: true
                Layout.leftMargin: 6
                Layout.rightMargin: 6
                Layout.preferredHeight: 54
                color: theme.panel
                border.color: theme.border
                radius: theme.radius
                Label {
                    anchors.left: parent.left
                    anchors.leftMargin: 16
                    anchors.verticalCenter: parent.verticalCenter
                    text: "Source"
                    color: theme.textDim
                    font.pixelSize: 14
                }
                Label {
                    anchors.left: parent.left
                    anchors.leftMargin: 96
                    anchors.right: parent.right
                    anchors.rightMargin: 16
                    anchors.verticalCenter: parent.verticalCenter
                    text: page.s.sourceName === "" ? "none selected" : page.s.sourceName
                    color: theme.text
                    font.pixelSize: 16
                    elide: Text.ElideMiddle
                }
            }

            // ---- mode ---------------------------------------------------

            Rectangle {
                Layout.fillWidth: true
                Layout.leftMargin: 6
                Layout.rightMargin: 6
                Layout.preferredHeight: 128
                color: theme.panel
                border.color: theme.border
                radius: theme.radius

                ColumnLayout {
                    anchors.fill: parent
                    anchors.margins: 14
                    spacing: 10

                    RowLayout {
                        Label { text: "Mode"; color: theme.textDim; font.pixelSize: 14 }
                        Item { Layout.fillWidth: true }
                        Button {
                            id: spokenMode
                            text: "Spoken"
                            checkable: true
                            onClicked: app.updateSetup({ "mode": "spoken" })
                        }
                        Button {
                            id: writtenMode
                            text: "Written"
                            checkable: true
                            onClicked: app.updateSetup({ "mode": "written" })
                        }
                    }

                    RowLayout {
                        Label { text: "Order"; color: theme.textDim; font.pixelSize: 14 }
                        Item { Layout.fillWidth: true }
                        Button {
                            id: sequentialOrder
                            text: "Sequential"
                            checkable: true
                            onClicked: app.updateSetup({ "order": "sequential" })
                        }
                        Button {
                            id: randomOrder
                            text: "Random, no repeats"
                            checkable: true
                            onClicked: app.updateSetup({ "order": "randomWithoutRepetition" })
                        }
                    }
                }
            }

            // ---- spoken options ------------------------------------------

            Rectangle {
                Layout.fillWidth: true
                Layout.leftMargin: 6
                Layout.rightMargin: 6
                Layout.preferredHeight: page.s.mode === "spoken" ? 150 : 0
                visible: page.s.mode === "spoken"
                color: theme.panel
                border.color: theme.border
                radius: theme.radius

                ColumnLayout {
                    anchors.fill: parent
                    anchors.margins: 14
                    spacing: 10

                    RowLayout {
                        Label { text: "Progression"; color: theme.textDim; font.pixelSize: 14 }
                        Item { Layout.fillWidth: true }
                        Button {
                            id: fixedTimer
                            text: "Fixed Timer"
                            checkable: true
                            onClicked: app.updateSetup({ "progression": "fixedTimer" })
                        }
                        Button {
                            id: manualAdvance
                            text: "Manual"
                            checkable: true
                            onClicked: app.updateSetup({ "progression": "manual" })
                        }
                    }

                    RowLayout {
                        visible: page.s.progression === "fixedTimer"
                        Label {
                            text: "Answer window"
                            color: theme.textDim
                            font.pixelSize: 14
                        }
                        Item { Layout.fillWidth: true }
                        Repeater {
                            id: delayPresetsRow
                            model: page.presets
                            delegate: Button {
                                required property var modelData
                                text: modelData + "s"
                                checkable: true
                                onClicked: app.updateSetup({ "delay": modelData })
                            }
                        }
                        TextField {
                            id: customDelay
                            text: String(page.s.delay)
                            validator: DoubleValidator { bottom: 0.2; top: 300 }
                            Layout.preferredWidth: 76
                            onEditingFinished: app.updateSetup({ "delay": parseFloat(text) })
                        }
                    }
                }
            }

            // ---- written options ----------------------------------------

            Rectangle {
                Layout.fillWidth: true
                Layout.leftMargin: 6
                Layout.rightMargin: 6
                Layout.preferredHeight: page.s.mode === "written" ? 108 : 0
                visible: page.s.mode === "written"
                color: theme.panel
                border.color: theme.border
                radius: theme.radius

                ColumnLayout {
                    anchors.fill: parent
                    anchors.margins: 14
                    spacing: 10

                    RowLayout {
                        Label { text: "Prompt"; color: theme.textDim; font.pixelSize: 14 }
                        Item { Layout.fillWidth: true }
                        Button {
                            id: audioPrompt
                            text: "Audio (hidden)"
                            checkable: true
                            onClicked: app.updateSetup({ "writtenPrompt": "audio" })
                        }
                        Button {
                            id: textPrompt
                            text: "Text (visible)"
                            checkable: true
                            onClicked: app.updateSetup({ "writtenPrompt": "text" })
                        }
                    }

                    Label {
                        text: "Audio Prompt: the question is spoken and hidden. Text Prompt: the question is shown."
                        color: theme.textDim
                        font.pixelSize: 12
                        wrapMode: Text.WordWrap
                        Layout.fillWidth: true
                    }
                }
            }

            // ---- voice ---------------------------------------------------

            Rectangle {
                Layout.fillWidth: true
                Layout.leftMargin: 6
                Layout.rightMargin: 6
                Layout.preferredHeight: 108
                color: theme.panel
                border.color: theme.border
                radius: theme.radius

                ColumnLayout {
                    anchors.fill: parent
                    anchors.margins: 14
                    spacing: 10

                    RowLayout {
                        Label { text: "Voice"; color: theme.textDim; font.pixelSize: 14 }
                        Item { Layout.fillWidth: true }
                        ComboBox {
                            id: voiceBox
                            model: page.voiceList
                            Layout.preferredWidth: 240
                            onActivated: app.updateSetup({ "voice": currentText })
                            enabled: app.ttsAvailable
                        }
                        Label {
                            text: "Rate"
                            color: theme.textDim
                            font.pixelSize: 14
                        }
                        Slider {
                            id: rateSlider
                            Layout.preferredWidth: 200
                            from: 110
                            to: 320
                            stepSize: 5
                            onMoved: {
                                rateLabel.text = Math.round(value) + " wpm"
                                app.updateSetup({ "rate": Math.round(value) })
                            }
                        }
                        Label {
                            id: rateLabel
                            text: Math.round(page.s.rate) + " wpm"
                            color: theme.text
                            font.pixelSize: 14
                            Layout.preferredWidth: 72
                        }
                    }

                    Label {
                        text: app.ttsAvailable
                              ? "Local macOS voices only. Nothing is sent to a network service."
                              : "macOS 'say' was not found on this system."
                        color: app.ttsAvailable ? theme.textDim : theme.warn
                        font.pixelSize: 12
                        wrapMode: Text.WordWrap
                        Layout.fillWidth: true
                    }
                }
            }

            // ---- recording -----------------------------------------------

            Rectangle {
                Layout.fillWidth: true
                Layout.leftMargin: 6
                Layout.rightMargin: 6
                Layout.preferredHeight: 158
                color: theme.panel
                border.color: theme.border
                radius: theme.radius

                ColumnLayout {
                    anchors.fill: parent
                    anchors.margins: 14
                    spacing: 8

                    RowLayout {
                        CheckBox {
                            id: recordBox
                            text: "Record my voice during the session"
                            font.pixelSize: 15
                            palette.buttonText: theme.text
                            onToggled: app.updateSetup({ "record": checked })
                        }
                        Item { Layout.fillWidth: true }
                    }

                    RowLayout {
                        CheckBox {
                            id: keepRecordingBox
                            text: "Keep recording after transcription"
                            font.pixelSize: 15
                            palette.buttonText: theme.text
                            enabled: page.s.record === true
                            onToggled: app.updateSetup({ "keepRecording": checked })
                        }
                    }

// Hidden in Spoken mode rather than shown disabled: nothing is typed in
                     // Spoken mode, so a greyed-out checkbox here reads as a
                     // mistake or as a setting the user cannot reach. The stored
                     // preference is left untouched either way, so switching
                     // modes does not lose it.
                     RowLayout {
                         objectName: "saveTypedRow"
                         visible: page.s.mode === "written"
                         Layout.preferredHeight: page.s.mode === "written" ? implicitHeight : 0
                         CheckBox {
                             id: saveTypedBox
                             objectName: "saveTypedCheck"
                             text: "Keep the answers I type"
                             font.pixelSize: 15
                             palette.buttonText: theme.text
                             onToggled: app.updateSetup({ "saveTyped": checked })
                         }
                         Item { Layout.fillWidth: true }
                     }

                     Label {
                         text: page.s.mode === "written"
                               ? "Off, typed answers are discarded when the session ends. On, they are kept in the session file and can be exported. Recording is separate: off by default, and deleted after a successful transcription unless you keep it."
                               : "Recording is off by default. Audio is written to a temporary file and deleted after a successful transcription unless you keep it."
                         color: theme.textDim
                         font.pixelSize: 12
                         wrapMode: Text.WordWrap
                         Layout.fillWidth: true
                     }
                }
            }

            Item { Layout.fillHeight: true }

            RowLayout {
                Layout.fillWidth: true
                Layout.margins: 6
                spacing: theme.gap

                Button {
                    text: "Back"
                    onClicked: app.showLibrary()
                }
                Item { Layout.fillWidth: true }
                Button {
                    text: "Start session"
                    highlighted: true
                    Layout.preferredWidth: 190
                    Layout.preferredHeight: 52
                    onClicked: app.startSession()
                }
            }
        }
    }
}
