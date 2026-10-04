import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts

// Settings is a form over a draft copy of the configuration. No control here
// writes config.json: everything lands in the draft and only "Apply changes"
// persists it, so "Back" can honestly mean "throw my edits away".
FocusScope {
    id: page

    property var cfg: ({
        "libraryPath": "",
        "sessionsPath": "",
        "exportDirectory": "",
        "whisperCLIPath": "",
        "whisperModelPath": "",
        "ttsVoice": "Kokoro Heart",
        "ttsRate": 175,
        "defaultDelaySeconds": 5,
        "recordingEnabledByDefault": false,
        "keepRecordingByDefault": false,
        "saveTypedAnswersByDefault": false,
        "theme": "dark",
        "themeModes": ["system", "dark", "light"],
        "voices": [],
        "shortcuts": {},
        "whisperStatus": "",
        "ttsAvailable": true,
        "orphanFiles": 0,
        "dirty": false,
        "problems": []
    })

    // Guards the re-assert in syncControls: assigning `checked` emits
    // `toggled`, which would otherwise be read as a user edit.
    property bool syncing: false

    function refresh() {
        cfg = app.settingsState()
        syncControls()
    }

    function syncControls() {
        if (cfg.recordingEnabledByDefault === undefined) return
        page.syncing = true
        recordingOffByDefault.checked = cfg.recordingEnabledByDefault === true
        keepRecordingByDefault.checked = cfg.keepRecordingByDefault === true
        saveTypedByDefault.checked = cfg.saveTypedAnswersByDefault === true
        defaultRateSlider.value = cfg.ttsRate
        defaultDelayField.text = String(cfg.defaultDelaySeconds)
        page.syncing = false
    }

    // The label column follows the window width, so the values keep a usable
    // share of a narrow window instead of being squeezed off the card.
    readonly property int labelWidth: Math.max(104, Math.min(168, Math.round(page.width * 0.24)))

    function leave() {
        // The controller answers by either navigating away (nothing pending) or
        // asking the user what to do with the draft, so the decision lives in
        // one place instead of in this button.
        app.leaveSettings()
    }

    Component.onCompleted: refresh()

    Connections {
        target: app
        function onSettingsChanged() { page.refresh() }
    }

    ColumnLayout {
        anchors.fill: parent
        spacing: 0

        // ---- header --------------------------------------------------

        RowLayout {
            Layout.fillWidth: true
            Layout.margins: 6
            Layout.bottomMargin: 0
            spacing: 10

            Label {
                text: "Settings"
                font.pixelSize: 30
                font.weight: Font.Bold
                color: theme.text
            }
            Text {
                text: "unsaved changes"
                color: theme.warn
                font.pixelSize: 13
                visible: app.settingsDirty
            }
            Item { Layout.fillWidth: true }
            Button {
                text: "Back"
                onClicked: page.leave()
            }
        }

        // ---- scrolling form ------------------------------------------

        ScrollView {
            Layout.fillWidth: true
            Layout.fillHeight: true
            contentWidth: availableWidth

            ColumnLayout {
                width: parent.width
                spacing: theme.gap

                // Each card takes the height its content asks for. Fixed
                // preferredHeight values were what cut the last row off: the
                // card could be shorter than its own contents.

                // ---- Storage -------------------------------------------

                Rectangle {
                    Layout.fillWidth: true
                    Layout.leftMargin: 6
                    Layout.rightMargin: 6
                    implicitHeight: storageBody.implicitHeight + 28
                    color: theme.panel
                    border.color: theme.border
                    radius: theme.radius

                    ColumnLayout {
                        id: storageBody
                        anchors.fill: parent
                        anchors.margins: 14
                        spacing: 10

                        RowLayout {
                            Layout.fillWidth: true
                            spacing: 10
                            Label {
                                text: "Library folder"
                                color: theme.textDim
                                font.pixelSize: 14
                                Layout.preferredWidth: page.labelWidth
                                Layout.minimumWidth: 90
                            }
                            Text {
                                Layout.fillWidth: true
                                Layout.minimumWidth: 60
                                text: page.cfg.libraryPath || "not selected"
                                color: page.cfg.libraryPath ? theme.text : theme.warn
                                font.pixelSize: 14
                                elide: Text.ElideMiddle
                                HoverHandler { id: pathHover1 }
                                ToolTip.visible: pathHover1.hovered
                                ToolTip.text: text
                            }
                            Button {
                                text: "Choose…"
                                onClicked: app.chooseFolder("library")
                            }
                        }

                        RowLayout {
                            Layout.fillWidth: true
                            spacing: 10
                            Label {
                                text: "Sessions folder"
                                color: theme.textDim
                                font.pixelSize: 14
                                Layout.preferredWidth: page.labelWidth
                                Layout.minimumWidth: 90
                            }
                            Text {
                                Layout.fillWidth: true
                                Layout.minimumWidth: 60
                                text: page.cfg.sessionsPath || "default: ~/Documents/Diloger/sessions"
                                color: theme.text
                                font.pixelSize: 14
                                elide: Text.ElideMiddle
                                HoverHandler { id: pathHover2 }
                                ToolTip.visible: pathHover2.hovered
                                ToolTip.text: text
                            }
                            Button {
                                text: "Choose…"
                                // A folder that already holds sessions is not a
                                // detail: History would change what it shows.
                                onClicked: page.cfg.sessionsPath === ""
                                      ? app.chooseFolder("sessions")
                                      : sessionsDialog.open()
                            }
                        }

                        Text {
                            Layout.fillWidth: true
                            text: "The sessions folder holds saved sessions, recordings and transcripts. Changing it does not move or delete anything."
                            color: theme.textDim
                            font.pixelSize: 12
                            wrapMode: Text.WordWrap
                        }

                        RowLayout {
                            Layout.fillWidth: true
                            spacing: 10
                            Label {
                                text: "Export folder"
                                color: theme.textDim
                                font.pixelSize: 14
                                Layout.preferredWidth: page.labelWidth
                                Layout.minimumWidth: 90
                            }
                            Text {
                                Layout.fillWidth: true
                                Layout.minimumWidth: 60
                                text: page.cfg.exportDirectory || "default: the sessions folder"
                                color: theme.text
                                font.pixelSize: 14
                                elide: Text.ElideMiddle
                                HoverHandler { id: exportHover }
                                ToolTip.visible: exportHover.hovered
                                ToolTip.text: text
                            }
                            Button {
                                text: "Choose…"
                                onClicked: app.chooseFolder("export")
                            }
                        }

                        Text {
                            Layout.fillWidth: true
                            text: "Where Export Markdown opens its Save dialog. Only this folder is remembered, and it is updated after a successful export."
                            color: theme.textDim
                            font.pixelSize: 12
                            wrapMode: Text.WordWrap
                        }
                    }
                }

                // ---- Voice ---------------------------------------------

                Rectangle {
                    Layout.fillWidth: true
                    Layout.leftMargin: 6
                    Layout.rightMargin: 6
                    implicitHeight: voiceBody.implicitHeight + 28
                    color: theme.panel
                    border.color: theme.border
                    radius: theme.radius

                    ColumnLayout {
                        id: voiceBody
                        anchors.fill: parent
                        anchors.margins: 14
                        spacing: 10

                        RowLayout {
                            Layout.fillWidth: true
                            spacing: 10
                            Label {
                                text: "Default voice"
                                color: theme.textDim
                                font.pixelSize: 14
                                Layout.preferredWidth: page.labelWidth
                                Layout.minimumWidth: 90
                            }
                            ComboBox {
                                id: voiceBox
                                Layout.fillWidth: true
                                Layout.minimumWidth: 120
                                model: page.cfg.voices || []
                                currentIndex: Math.max(0, (page.cfg.voices || []).indexOf(page.cfg.ttsVoice))
                                onActivated: app.updateSettingsDraft({ "ttsVoice": currentText })
                            }
                        }

                        RowLayout {
                            Layout.fillWidth: true
                            spacing: 10
                            Label {
                                text: "Default rate"
                                color: theme.textDim
                                font.pixelSize: 14
                                Layout.preferredWidth: page.labelWidth
                                Layout.minimumWidth: 90
                            }
                            Slider {
                                id: defaultRateSlider
                                from: 110
                                to: 320
                                stepSize: 5
                                Layout.fillWidth: true
                                Layout.minimumWidth: 120
                                onMoved: app.updateSettingsDraft({ "ttsRate": Math.round(value) })
                            }
                            Text {
                                Layout.preferredWidth: 74
                                text: (page.cfg.ttsRate || 175) + " wpm"
                                color: theme.text
                                font.pixelSize: 14
                            }
                        }

                        RowLayout {
                            Layout.fillWidth: true
                            spacing: 10
                            Label {
                                text: "Default answer window"
                                color: theme.textDim
                                font.pixelSize: 14
                                Layout.preferredWidth: page.labelWidth
                                Layout.minimumWidth: 90
                            }
                            TextField {
                                id: defaultDelayField
                                Layout.preferredWidth: 90
                                validator: DoubleValidator { bottom: 0.2; top: 300 }
                                onEditingFinished: {
                                    var value = parseFloat(text)
                                    if (!isNaN(value))
                                        app.updateSettingsDraft({ "defaultDelaySeconds": value })
                                }
                            }
                            Text {
                                text: "seconds to wait before the next prompt in timed mode"
                                color: theme.textDim
                                font.pixelSize: 12
                                Layout.fillWidth: true
                                wrapMode: Text.WordWrap
                            }
                        }

                        Text {
                            Layout.fillWidth: true
                            visible: !page.cfg.ttsAvailable
                            text: "No speech voice was found on this Mac, so prompts cannot be spoken yet."
                            color: theme.warn
                            font.pixelSize: 13
                            wrapMode: Text.WordWrap
                        }
                    }
                }

                // ---- Appearance -----------------------------------------
                // Placed next to Voice: both are how the app sounds and looks
                // while it works, not where it stores anything.

                Rectangle {
                    Layout.fillWidth: true
                    Layout.leftMargin: 6
                    Layout.rightMargin: 6
                    implicitHeight: appearanceBody.implicitHeight + 28
                    color: theme.panel
                    border.color: theme.border
                    radius: theme.radius

                    ColumnLayout {
                        id: appearanceBody
                        anchors.fill: parent
                        anchors.margins: 14
                        spacing: 10

                        Text {
                            text: "Appearance"
                            color: theme.text
                            font.pixelSize: 17
                        }

                        RowLayout {
                            Layout.fillWidth: true
                            spacing: 10
                            Label {
                                text: "Theme"
                                color: theme.textDim
                                font.pixelSize: 14
                                Layout.preferredWidth: page.labelWidth
                                Layout.minimumWidth: 90
                            }
                            ComboBox {
                                id: themeBox
                                Layout.preferredWidth: 150
                                model: page.cfg.themeModes || []
                                currentIndex: Math.max(0, (page.cfg.themeModes || []).indexOf(page.cfg.theme))
                                onActivated: app.updateSettingsDraft({ "theme": currentText })
                            }
                            Item { Layout.fillWidth: true }
                        }

                        // "System" is a promise to follow macOS, so say what it
                        // currently resolves to rather than leaving the user to
                        // guess whether the switch worked.
                        Text {
                            Layout.fillWidth: true
                            text: page.cfg.theme === "system"
                                  ? "Following macOS — currently using the " + theme.resolvedTheme + " theme."
                                  : "Fixed to the " + page.cfg.theme + " theme, regardless of macOS appearance."
                            color: theme.textDim
                            font.pixelSize: 12
                            wrapMode: Text.WordWrap
                            Accessible.role: Accessible.StaticText
                            Accessible.name: text
                        }

                        Text {
                            Layout.fillWidth: true
                            text: "Theme applies when you select Apply changes, like every other setting here."
                            color: theme.textDim
                            font.pixelSize: 12
                            wrapMode: Text.WordWrap
                        }
                    }
                }

                // ---- Recording -----------------------------------------

                Rectangle {
                    Layout.fillWidth: true
                    Layout.leftMargin: 6
                    Layout.rightMargin: 6
                    implicitHeight: recordBody.implicitHeight + 28
                    color: theme.panel
                    border.color: theme.border
                    radius: theme.radius

                    ColumnLayout {
                        id: recordBody
                        anchors.fill: parent
                        anchors.margins: 14
                        spacing: 10

                        Text {
                            text: "Recording"
                            color: theme.text
                            font.pixelSize: 17
                        }

                        // Audio always comes from the system default input, so
                        // there is no device list here: the app records what the
                        // Mac is set to use, and a session works without a
                        // microphone at all.
                        Text {
                            Layout.fillWidth: true
                            text: "Audio is taken from the system default input. Diloger works without a microphone; a session without recording never creates a file."
                            color: theme.textDim
                            font.pixelSize: 13
                            wrapMode: Text.WordWrap
                        }

                        CheckBox {
                            id: recordingOffByDefault
                            text: "Record my voice during new sessions by default"
                            palette.buttonText: theme.text
                            font.pixelSize: 15
                            onToggled: {
                                if (!page.syncing)
                                    app.updateSettingsDraft({ "recordingEnabledByDefault": checked })
                            }
                        }

                        CheckBox {
                            id: keepRecordingByDefault
                            text: "Keep the recording file after transcription by default"
                            palette.buttonText: theme.text
                            font.pixelSize: 15
                            enabled: recordingOffByDefault.checked
                            onToggled: {
                                if (!page.syncing)
                                    app.updateSettingsDraft({ "keepRecordingByDefault": checked })
                            }
                        }

                        Text {
                            Layout.fillWidth: true
                            text: "A recording is written to a temporary file and deleted once Whisper has finished with it, unless keeping it is selected. Retained recordings stay inside their session folder."
                            color: theme.textDim
                            font.pixelSize: 12
                            wrapMode: Text.WordWrap
                        }

                        CheckBox {
                            id: saveTypedByDefault
                            objectName: "saveTypedAnswersCheck"
                            // "In written mode" is what it does; what the user
                            // needs is to know where the text ends up. The old
                            // wording said only that it was saved, and not that
                            // it goes into the session's own file and can be
                            // exported as Markdown.
                            text: "Keep the answers I type in Written mode"
                            palette.buttonText: theme.text
                            font.pixelSize: 15
                            onToggled: {
                                if (!page.syncing)
                                    app.updateSettingsDraft({ "saveTypedAnswersByDefault": checked })
                            }
                        }
                    }
                }

                // ---- Written answers ---------------------------------------
                // Its own card, not a checkbox inside the Recording one: this has
                // nothing to do with audio, and a user looking for how their
                // typed text is stored should not have to read about the
                // microphone to find out.

                Rectangle {
                    Layout.fillWidth: true
                    Layout.leftMargin: 6
                    Layout.rightMargin: 6
                    implicitHeight: typedBody.implicitHeight + 28
                    color: theme.panel
                    border.color: theme.border
                    radius: theme.radius

                    ColumnLayout {
                        id: typedBody
                        anchors.fill: parent
                        anchors.margins: 14
                        spacing: 8

                        Text {
                            text: "Written answers"
                            color: theme.text
                            font.pixelSize: 17
                        }

                        Text {
                            Layout.fillWidth: true
                            text: "Off, answers are typed and thrown away when a session ends. On, each answer is kept in that session's own file so you can read it back in History and include it in an exported transcript. Nothing is sent anywhere."
                            color: theme.textDim
                            font.pixelSize: 12
                            wrapMode: Text.WordWrap
                        }
                    }
                }

                // ---- Whisper --------------------------------------------

                Rectangle {
                    Layout.fillWidth: true
                    Layout.leftMargin: 6
                    Layout.rightMargin: 6
                    implicitHeight: whisperBody.implicitHeight + 28
                    color: theme.panel
                    border.color: theme.border
                    radius: theme.radius

                    ColumnLayout {
                        id: whisperBody
                        anchors.fill: parent
                        anchors.margins: 14
                        spacing: 10

                        RowLayout {
                            Layout.fillWidth: true
                            spacing: 10
                            Label {
                                text: "whisper-cli"
                                color: theme.textDim
                                font.pixelSize: 14
                                Layout.preferredWidth: page.labelWidth
                                Layout.minimumWidth: 90
                            }
                            Text {
                                Layout.fillWidth: true
                                Layout.minimumWidth: 60
                                text: page.cfg.whisperCLIPath || "auto-discover"
                                color: theme.text
                                font.pixelSize: 14
                                elide: Text.ElideMiddle
                                HoverHandler { id: pathHover3 }
                                ToolTip.visible: pathHover3.hovered
                                ToolTip.text: text
                            }
                            Button {
                                text: "Choose…"
                                onClicked: app.chooseFile("whisperCLI")
                            }
                        }

                        RowLayout {
                            Layout.fillWidth: true
                            spacing: 10
                            Label {
                                text: "Model file"
                                color: theme.textDim
                                font.pixelSize: 14
                                Layout.preferredWidth: page.labelWidth
                                Layout.minimumWidth: 90
                            }
                            Text {
                                Layout.fillWidth: true
                                Layout.minimumWidth: 60
                                text: page.cfg.whisperModelPath || "auto-discover"
                                color: theme.text
                                font.pixelSize: 14
                                elide: Text.ElideMiddle
                                HoverHandler { id: pathHover4 }
                                ToolTip.visible: pathHover4.hovered
                                ToolTip.text: text
                            }
                            Button {
                                text: "Choose…"
                                onClicked: app.chooseFile("whisperModel")
                            }
                        }

                        RowLayout {
                            Layout.fillWidth: true
                            spacing: 10
                            Button {
                                text: "Auto-detect local Whisper"
                                onClicked: app.autoDetectWhisper()
                            }
                            Item { Layout.fillWidth: true }
                            Text {
                                text: page.cfg.whisperStatus
                                color: theme.textDim
                                font.pixelSize: 12
                                Layout.maximumWidth: page.width - 260
                                elide: Text.ElideMiddle
                                HoverHandler { id: pathHover5 }
                                ToolTip.visible: pathHover5.hovered
                                ToolTip.text: text
                            }
                        }

                        Text {
                            Layout.fillWidth: true
                            text: "Models are never downloaded. Whisper runs locally after a session ends, never during one."
                            color: theme.textDim
                            font.pixelSize: 12
                            wrapMode: Text.WordWrap
                        }
                    }
                }

                // ---- Keyboard -------------------------------------------

                Rectangle {
                    Layout.fillWidth: true
                    Layout.leftMargin: 6
                    Layout.rightMargin: 6
                    implicitHeight: keysBody.implicitHeight + 28
                    color: theme.panel
                    border.color: theme.border
                    radius: theme.radius

                    ColumnLayout {
                        id: keysBody
                        anchors.fill: parent
                        anchors.margins: 14
                        spacing: 8

                        Text {
                            text: "Keyboard"
                            color: theme.text
                            font.pixelSize: 17
                            Accessible.role: Accessible.StaticText
                            Accessible.name: text
                        }
                        // A shortcut list is exactly what a screen-reader user
                        // cannot do without, so every label states its own text
                        // instead of relying on Qt to expose a bare Text item.
                        GridLayout {
                            Layout.fillWidth: true
                            columns: page.width < 620 ? 2 : 4
                            columnSpacing: 16
                            rowSpacing: 6

                            component ShortcutLabel: Text {
                                color: theme.textDim
                                font.pixelSize: 13
                                Accessible.role: Accessible.StaticText
                                Accessible.name: text
                            }
                            component ShortcutKey: Text {
                                color: theme.text
                                font.pixelSize: 13
                                font.family: "Menlo"
                                Accessible.role: Accessible.StaticText
                                Accessible.name: text
                            }

                            ShortcutLabel { text: "Pause / Resume" }
                            ShortcutKey { text: "Space" }
                            ShortcutLabel { text: "Repeat" }
                            ShortcutKey { text: "R" }

                            ShortcutLabel { text: "Next" }
                            ShortcutKey { text: "Return" }
                            ShortcutLabel { text: "Reveal" }
                            ShortcutKey { text: "V" }

                            ShortcutLabel { text: "Skip" }
                            ShortcutKey { text: "S" }
                            ShortcutLabel { text: "Stop" }
                            ShortcutKey { text: "Esc" }
                        }
                    }
                }

                // ---- Privacy and cleanup --------------------------------

                Rectangle {
                    Layout.fillWidth: true
                    Layout.leftMargin: 6
                    Layout.rightMargin: 6
                    implicitHeight: privacyBody.implicitHeight + 28
                    color: theme.panel
                    border.color: theme.border
                    radius: theme.radius

                    ColumnLayout {
                        id: privacyBody
                        anchors.fill: parent
                        anchors.margins: 14
                        spacing: 8

                        Text {
                            text: "Privacy"
                            color: theme.text
                            font.pixelSize: 17
                            Accessible.role: Accessible.StaticText
                            Accessible.name: text
                        }
                        Text {
                            Layout.fillWidth: true
                            text: "All content, audio, transcripts and settings stay on this Mac. Diloger has no accounts, no sync, no telemetry and makes no network requests. Your answers are never graded, corrected or analysed."
                            color: theme.textDim
                            font.pixelSize: 13
                            wrapMode: Text.WordWrap
                            Accessible.role: Accessible.StaticText
                            Accessible.name: text
                        }

                        RowLayout {
                            Layout.fillWidth: true
                            spacing: 10
                            visible: page.cfg.orphanFiles > 0
                            Label {
                                Layout.fillWidth: true
                                text: page.cfg.orphanFiles + " leftover recording file(s) in the temporary folder"
                                color: theme.warn
                                font.pixelSize: 13
                                wrapMode: Text.WordWrap
                            }
                            Button {
                                text: "Delete them"
                                onClicked: orphanDialog.open()
                            }
                        }
                    }
                }

                // Validation problems are shown here, not only in a banner that
                // can disappear before it is read.
                Rectangle {
                    Layout.fillWidth: true
                    Layout.leftMargin: 6
                    Layout.rightMargin: 6
                    Layout.bottomMargin: 6
                    visible: (page.cfg.problems || []).length > 0
                    implicitHeight: visible ? problemsBody.implicitHeight + 28 : 0
                    color: theme.panel
                    border.color: theme.warn
                    radius: theme.radius

                    ColumnLayout {
                        id: problemsBody
                        anchors.fill: parent
                        anchors.margins: 14
                        spacing: 6

                        Text {
                            text: "Fix before applying"
                            color: theme.warn
                            font.pixelSize: 15
                        }
                        Repeater {
                            model: page.cfg.problems || []
                            Text {
                                required property string modelData
                                Layout.fillWidth: true
                                text: "· " + modelData
                                color: theme.warn
                                font.pixelSize: 13
                                wrapMode: Text.WordWrap
                            }
                        }
                    }
                }
            }
        }

        // ---- fixed action bar -----------------------------------------
        // Outside the ScrollView, so Apply stays reachable when the form is
        // longer than the window.

        Rectangle {
            Layout.fillWidth: true
            Layout.preferredHeight: 52
            color: theme.panel
            border.color: theme.border

            RowLayout {
                anchors.fill: parent
                anchors.leftMargin: 14
                anchors.rightMargin: 14
                spacing: 10

                Text {
                    Layout.fillWidth: true
                    text: app.settingsDirty
                          ? "Your changes are not saved yet."
                          : "Everything here is saved."
                    color: app.settingsDirty ? theme.warn : theme.textDim
                    font.pixelSize: 13
                    wrapMode: Text.WordWrap
                }
                Button {
                    objectName: "discardButton"
                    text: "Discard changes"
                    enabled: app.settingsDirty
                    onClicked: app.discardSettings()
                }
                Button {
                    objectName: "applyButton"
                    text: "Apply changes"
                    highlighted: true
                    enabled: app.settingsDirty && (page.cfg.problems || []).length === 0
                    onClicked: {
                        if (app.applySettings())
                            app.showLibrary()
                    }
                }
            }
        }
    }

    // ---- dialogs ------------------------------------------------------

    Dialog {
        id: leaveDialog
        anchors.centerIn: parent
        modal: true
        title: "Unsaved settings"
        width: 440

        ColumnLayout {
            anchors.fill: parent
            spacing: 12

            Text {
                Layout.fillWidth: true
                text: "Your settings changes have not been applied. Leaving now discards them."
                color: theme.text
                font.pixelSize: 14
                wrapMode: Text.WordWrap
                Accessible.role: Accessible.StaticText
                Accessible.name: text
            }
            RowLayout {
                Layout.fillWidth: true
                spacing: 10
                Item { Layout.fillWidth: true }
                Button {
                    text: "Keep editing"
                    onClicked: leaveDialog.close()
                }
                Button {
                    text: "Discard and leave"
                    onClicked: {
                        leaveDialog.close()
                        app.discardAndLeaveSettings()
                    }
                }
                Button {
                    text: "Apply and leave"
                    highlighted: true
                    enabled: (page.cfg.problems || []).length === 0
                    onClicked: {
                        leaveDialog.close()
                        app.applyAndLeaveSettings()
                    }
                }
            }
        }
    }

    Dialog {
        id: sessionsDialog
        anchors.centerIn: parent
        modal: true
        title: "Change the sessions folder?"
        width: 440

        ColumnLayout {
            anchors.fill: parent
            spacing: 12

            Text {
                Layout.fillWidth: true
                text: "History will be read from the folder you choose next. Sessions already saved stay in this one."
                color: theme.text
                font.pixelSize: 14
                wrapMode: Text.WordWrap
                Accessible.role: Accessible.StaticText
                Accessible.name: text
            }
            RowLayout {
                Layout.fillWidth: true
                spacing: 10
                Item { Layout.fillWidth: true }
                Button {
                    text: "Cancel"
                    onClicked: sessionsDialog.close()
                }
                Button {
                    text: "Choose folder"
                    highlighted: true
                    onClicked: {
                        sessionsDialog.close()
                        app.chooseFolder("sessions")
                    }
                }
            }
        }
    }

    Dialog {
        id: orphanDialog
        anchors.centerIn: parent
        modal: true
        title: "Delete leftover recordings?"
        width: 440

        ColumnLayout {
            anchors.fill: parent
            spacing: 12

            Text {
                Layout.fillWidth: true
                text: page.cfg.orphanFiles + " temporary recording file(s) are left over in the system temporary folder. They are not part of any saved session, and nothing in your library, sessions or retained recordings is touched."
                color: theme.text
                font.pixelSize: 14
                wrapMode: Text.WordWrap
                Accessible.role: Accessible.StaticText
                Accessible.name: text
            }
            RowLayout {
                Layout.fillWidth: true
                spacing: 10
                Item { Layout.fillWidth: true }
                Button {
                    text: "Cancel"
                    onClicked: orphanDialog.close()
                }
                Button {
                    text: "Delete"
                    highlighted: true
                    onClicked: {
                        orphanDialog.close()
                        app.deleteTemporaryRecordings()
                    }
                }
            }
        }
    }

    Connections {
        target: app
        function onSettingsCloseRequested() {
            if (!leaveDialog.opened)
                leaveDialog.open()
        }
    }
}