import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts

// Local session history. Facts about what happened, never a judgement of the
// user's English: no scores, grades or ratings.
FocusScope {
    id: page

    property var rows: []
    property string selectedId: ""
    // Structured readable blocks, never a Markdown document. `transcriptText`
    // used to be the whole of transcript.md, which is how `# Dialogue Trainer
    // Session` and `- Date:` ended up rendered as if they were the transcript.
    property var transcriptBlocks: []
    property bool transcriptOpen: false
    property string confirmId: ""
    property string confirmText: ""
    property bool clearAllOpen: false
    property string clearAllText: ""

    readonly property string transcriptNotice: "Local Whisper output with timestamps. Text was formatted for readability; words were not evaluated."
    // `selectedRow` is null until a row is picked, so this binding has to ask
    // whether there is a selection before reading it.
    readonly property string transcriptEmptyText:
        !page.hasSelection || !page.selectedRow ? "Select a session to see its transcript."
        : page.selectedRow.transcriptionStatus === "succeeded"
        ? "No speech was recognised in this recording."
        : "This session has no transcript yet. Use Transcribe to create one."

    function refresh() {
        rows = app.sessionHistory()
    }

    function select(row) {
        selectedId = row.id
        transcriptOpen = false
        transcriptBlocks = []
    }

    function openTranscript() {
        if (!page.hasSelection) return
        transcriptBlocks = app.transcriptBlocksForSession(page.selectedId)
        transcriptOpen = transcriptBlocks.length > 0
    }

    // Transcribing a stored session asks the controller for it by id; the
    // recording path never travels through QML.
    function transcribe() {
        if (!page.hasSelection) return
        app.transcribeSession(page.selectedId)
    }

    function exportMarkdown() {
        if (!page.hasSelection) return
        app.exportSession(page.selectedId)
    }

    function askDelete(row) {
        page.confirmId = row.id
        page.confirmText = "Delete session " + row.date + "?"
                           + (row.hasRecording ? "\\nIts recording.wav is deleted too." : "")
                           + "\\nThis cannot be undone."
    }

    function confirmDelete() {
        if (page.confirmId === "") return
        var id = page.confirmId
        page.confirmId = ""
        page.confirmText = ""
        if (app.deleteSession(id)) {
            if (page.selectedId === id) {
                page.selectedId = ""
                page.transcriptOpen = false
                page.transcriptBlocks = []
            }
            page.refresh()
        }
    }

    function askClearAll() {
        page.clearAllText = "Delete all " + page.rows.length + " stored session(s)?"
                           + "\nEvery session folder, recording and transcript is deleted."
                           + "\nThis cannot be undone."
        page.clearAllOpen = true
    }

    function runClearAll() {
        page.clearAllOpen = false
        // The returned counts are what the user is told; a partial failure is
        // reported by the controller instead of looking like an empty history.
        app.clearAllSessions()
        page.selectedId = ""
        page.transcriptOpen = false
        page.transcriptBlocks = []
        page.refresh()
    }

    function fmtDuration(seconds) {
        var total = Math.floor(seconds || 0)
        var m = Math.floor(total / 60)
        var s = total % 60
        return m + " min " + (s < 10 ? "0" : "") + s + " s"
    }

    Component.onCompleted: refresh()

    Connections {
        target: app
        function onRefreshRequested() { page.refresh() }
    }

    ColumnLayout {
        anchors.fill: parent
        spacing: theme.gap

        RowLayout {
            Layout.fillWidth: true
            spacing: theme.gap

            Label {
                text: "History"
                font.pixelSize: 30
                font.weight: Font.Bold
                color: theme.text
            }
            Item { Layout.fillWidth: true }
            Button {
                text: "Refresh"
                onClicked: page.refresh()
                ToolTip.visible: hovered
                ToolTip.text: "Re-read the sessions folder from disk."
            }
            Button {
                text: "Clear all"
                enabled: page.rows.length > 0
                onClicked: page.askClearAll()
                ToolTip.visible: hovered
                ToolTip.text: "Delete every stored session in one step."
            }
            Button {
                text: "Back to library"
                highlighted: true
                onClicked: app.showLibrary()
            }
        }

        RowLayout {
            Layout.fillWidth: true
            Layout.fillHeight: true
            spacing: theme.gap

            // ---- list ---------------------------------------------
            Rectangle {
                Layout.preferredWidth: 420
                Layout.fillHeight: true
                color: theme.panel
                border.color: theme.border
                border.width: 1
                radius: theme.radius

                ColumnLayout {
                    anchors.fill: parent
                    anchors.margins: 10
                    spacing: 8

                    Text {
                        text: page.rows.length + " stored session"
                              + (page.rows.length === 1 ? "" : "s")
                              + " · " + app.sessionsPath()
                        color: theme.textDim
                        font.pixelSize: 12
                        elide: Text.ElideMiddle
                        Layout.fillWidth: true
                    }

                    ListView {
                        id: list
                        Layout.fillWidth: true
                        Layout.fillHeight: true
                        clip: true
                        spacing: 4
                        model: page.rows
                        Accessible.role: Accessible.List
                        Accessible.name: "Session history"

                        delegate: Rectangle {
                            width: list.width
                            height: 74
                            radius: 8
                            color: page.selectedId === modelData.id
                                   ? theme.accentDim : "transparent"
                            border.color: page.selectedId === modelData.id
                                          ? theme.accent : "transparent"
                            border.width: 1

                            // Without these the rows expose nothing to
                            // assistive technology, so the whole history list
                            // was unreachable by screen reader and by
                            // automation: Open transcript and Delete session
                            // stayed disabled with no way to select a row.
                            Accessible.role: Accessible.ListItem
                            Accessible.name: modelData.date + ", "
                                             + modelData.source
                                             + ", " + modelData.mode
                                             + ", " + modelData.promptsReached
                                             + " of " + modelData.totalPrompts
                                             + " prompts, Whisper "
                                             + modelData.transcriptionStatus
                                             + ", "
                                             + (modelData.hasRecording ? "audio kept" : "no audio")
                                             + ", "
                                             + (modelData.hasTranscript ? "transcript available" : "no transcript")
                            Accessible.selected: page.selectedId === modelData.id
                            Accessible.onPressAction: page.select(modelData)

                            ColumnLayout {
                                id: rowBody
                                anchors.fill: parent
                                anchors.leftMargin: 12
                                anchors.rightMargin: 12
                                anchors.topMargin: 8
                                spacing: 2

                                // The selection tint is a mid-tone, so the normal dim
                                // and accent greys fall below 4.5:1 on it and the
                                // selected row becomes the least readable row in the
                                // list. The selected row therefore takes its
                                // foregrounds from the onSelection tokens, which are the
                                // values that clear the threshold on accentDim in both
                                // themes.
                                readonly property bool isSelected:
                                    page.selectedId === modelData.id
                                readonly property color primary:
                                    isSelected ? theme.onSelection : theme.text
                                readonly property color secondary:
                                    isSelected ? theme.onSelectionDim : theme.textDim
                                readonly property color badge:
                                    isSelected ? theme.onSelectionAccent : theme.accent

                                RowLayout {
                                    Layout.fillWidth: true
                                    spacing: 8
                                    Text {
                                        text: modelData.date
                                        color: rowBody.primary
                                        font.pixelSize: 15
                                        Accessible.role: Accessible.StaticText
                                        Accessible.name: modelData.date
                                    }
                                    Item { Layout.fillWidth: true }
                                    Text {
                                        text: modelData.hasRecording ? "audio" : ""
                                        color: theme.ok
                                        font.pixelSize: 11
                                    }
                                    Text {
                                        text: modelData.hasTranscript ? "transcript" : ""
                                        color: rowBody.badge
                                        font.pixelSize: 11
                                    }
                                }
                                Text {
                                    Layout.fillWidth: true
                                    text: modelData.source
                                    color: rowBody.secondary
                                    font.pixelSize: 12
                                    elide: Text.ElideMiddle
                                }
                                Text {
                                    Layout.fillWidth: true
                                    text: page.fmtDuration(modelData.durationSeconds)
                                          + " · " + modelData.promptsReached
                                          + (modelData.totalPrompts > 0
                                             ? "/" + modelData.totalPrompts : "")
                                          + " prompts · " + modelData.mode
                                    color: rowBody.secondary
                                    font.pixelSize: 11
                                }
                            }

                            MouseArea {
                                anchors.fill: parent
                                onClicked: page.select(modelData)

                                // The role and the press action live on the
                                // MouseArea, not on the delegate Rectangle:
                                // Qt only surfaces an item to accessibility
                                // once a role is set on a node inside the
                                // delegate, so a role on the Rectangle alone
                                // left the whole row list unreachable.
                                Accessible.role: Accessible.ListItem
                                Accessible.name: modelData.date + ", "
                                                 + modelData.source
                                                 + ", " + modelData.mode
                                                 + ", " + modelData.promptsReached
                                                 + " of " + modelData.totalPrompts
                                                 + " prompts, Whisper "
                                                 + modelData.transcriptionStatus
                                                 + ", "
                                                 + (modelData.hasRecording
                                                    ? "audio kept" : "no audio")
                                                 + ", "
                                                 + (modelData.hasTranscript
                                                    ? "transcript available"
                                                    : "no transcript")
                                Accessible.selected: page.selectedId === modelData.id
                                Accessible.onPressAction: page.select(modelData)
                            }
                        }
                    }
                }
            }

            // ---- detail ------------------------------------------
            ColumnLayout {
                Layout.fillWidth: true
                Layout.fillHeight: true
                spacing: theme.gap

                // Shown for the selected session, whether or not that session kept any
                // audio: a session without a recording says so inside the card
                // instead of leaving a dead button or no card at all. The card
                // is content-sized, so its transport row is always inside it and
                // the details card below always starts after its real bottom
                // edge. Playback stays in its own card and never joins the
                // session-action Flow at the bottom.
                PlaybackBar {
                    id: bar
                    Layout.fillWidth: true
                    visible: page.hasSelection
                    sessionId: page.hasSelection ? page.selectedId : ""
                }

                Rectangle {
                    Layout.fillWidth: true
                    Layout.fillHeight: true
                    color: theme.panel
                    border.color: theme.border
                    border.width: 1
                    radius: theme.radius

                    ColumnLayout {
                        anchors.fill: parent
                        anchors.margins: 16
                        spacing: 10

                        RowLayout {
                            Layout.fillWidth: true
                            Label {
                                text: page.transcriptOpen ? "Transcript" : "Session"
                                color: theme.text
                                font.pixelSize: 17
                            }
                            Item { Layout.fillWidth: true }
                            Text {
                                text: page.transcriptOpen ? page.transcriptNotice : ""
                                color: theme.textDim
                                font.pixelSize: 12
                                elide: Text.ElideRight
                                Layout.maximumWidth: Math.max(140, parent.width * 0.5)
                            }
                        }

                        ScrollView {
                            Layout.fillWidth: true
                            Layout.fillHeight: true
                            visible: !page.transcriptOpen

                            ColumnLayout {
                                width: parent.width
                                spacing: 6

                                Text {
                                    Layout.fillWidth: true
                                    text: page.hasSelection
                                          ? page.selectedRow.date
                                          : "Select a session to see what it contains."
                                    color: theme.text
                                    font.pixelSize: 15
                                    wrapMode: Text.WordWrap
                                }
                                Text {
                                    Layout.fillWidth: true
                                    text: page.hasSelection ? "Source: " + page.selectedRow.source : ""
                                    color: theme.textDim
                                    font.pixelSize: 13
                                    wrapMode: Text.WordWrap
                                }
                                Text {
                                    Layout.fillWidth: true
                                    text: page.hasSelection ? "Duration: " + page.fmtDuration(page.selectedRow.durationSeconds) : ""
                                    color: theme.textDim
                                    font.pixelSize: 13
                                }
                                Text {
                                    Layout.fillWidth: true
                                    text: page.hasSelection ? "Prompts reached: " + page.selectedRow.promptsReached
                                          + (page.selectedRow.totalPrompts > 0 ? " of " + page.selectedRow.totalPrompts : "")
                                          : ""
                                    color: theme.textDim
                                    font.pixelSize: 13
                                }
                                Text {
                                    Layout.fillWidth: true
                                    text: page.hasSelection ? "Recording: "
                                          + (page.selectedRow.hasRecording ? "kept in the session folder" : "not kept")
                                          : ""
                                    color: page.hasSelection && page.selectedRow.hasRecording ? theme.ok : theme.textDim
                                    font.pixelSize: 13
                                }
                                Text {
                                    Layout.fillWidth: true
                                    text: page.hasSelection ? "Whisper: " + page.selectedRow.transcriptionStatus : ""
                                    color: theme.textDim
                                    font.pixelSize: 13
                                }
                                Text {
                                    Layout.fillWidth: true
                                    visible: page.hasSelection && !page.selectedRow.hasSessionJson
                                    text: "session.json is missing or unreadable for this folder."
                                    color: theme.warn
                                    font.pixelSize: 13
                                    wrapMode: Text.WordWrap
                                }
                            }
                        }

                        ScrollView {
                            Layout.fillWidth: true
                            Layout.fillHeight: true
                            visible: page.transcriptOpen
                            objectName: "historyTranscriptScroll"

                            TranscriptView {
                                id: historyTranscript
                                objectName: "historyTranscriptView"
                                width: parent.width
                                blocks: page.transcriptBlocks
                                notice: page.transcriptNotice
                                emptyText: page.transcriptEmptyText
                            }
                        }
                    }
                }

                // Flow wraps the actions instead of pushing the last ones out
                // of a normal-width window.
                Flow {
                    Layout.fillWidth: true
                    spacing: 10

                    Button {
                        text: page.transcriptOpen ? "Back to details" : "Open transcript"
                        enabled: page.hasSelection
                                 && page.selectedRow.hasTranscript
                        onClicked: page.transcriptOpen ? page.transcriptOpen = false : page.openTranscript()
                    }
                    Button {
                        text: page.hasSelection && page.selectedRow.transcriptionStatus === "succeeded"
                              ? "Transcribe again" : "Transcribe"
                        visible: page.hasSelection
                        enabled: page.hasSelection
                                 && page.selectedRow.hasRecording
                                 && !app.transcribing
                        onClicked: page.transcribe()
                        ToolTip.visible: hovered
                        ToolTip.text: "Run local Whisper over this session's kept recording."
                    }
                    Button {
                        text: "Export Markdown"
                        visible: page.hasSelection
                        enabled: page.hasSelection && page.selectedRow.hasSessionJson
                        onClicked: page.exportMarkdown()
                        ToolTip.visible: hovered
                        ToolTip.text: "Choose where to save this transcript."
                    }
                    Button {
                        text: "Delete session"
                        enabled: page.hasSelection
                        onClicked: {
                            for (var i = 0; i < page.rows.length; i++) {
                                if (page.rows[i].id === page.selectedId) { page.askDelete(page.rows[i]); return }
                            }
                        }
                    }
                }
            }
        }

        Item { Layout.fillHeight: true }
    }

    readonly property var selectedRow: {
        for (var i = 0; i < rows.length; i++) {
            if (rows[i].id === selectedId)
                return rows[i]
        }
        return null
    }

    // The single source of truth for "a row is really loaded". Testing
    // selectedId alone is not enough: Rescan or a deletion can leave a
    // non-empty selectedId whose row no longer exists, and every detail
    // binding below then dereferenced null.
    readonly property bool hasSelection: selectedRow !== null

    // ---- deletion requires an explicit confirmation -------------------

    Dialog {
        id: confirmDialog
        anchors.centerIn: parent
        modal: true
        title: "Delete session?"
        standardButtons: Dialog.Cancel | Dialog.Discard
        onDiscarded: page.confirmDelete()
        onRejected: { page.confirmId = ""; page.confirmText = "" }

        Label {
            text: page.confirmText
            color: theme.text
            wrapMode: Text.WordWrap
            width: 380
            Accessible.role: Accessible.StaticText
            Accessible.name: text
        }
    }

    Connections {
        target: confirmDialog
        function onOpened() { }
    }

    onConfirmTextChanged: {
        if (confirmText !== "" && !confirmDialog.opened)
            confirmDialog.open()
    }

    // ---- clearing everything needs its own confirmation ----------------
    // Deleting one session is a mistake the user can see and undo by not
    // clicking; deleting all of them at once is not recoverable at all.

    Dialog {
        id: clearAllDialog
        anchors.centerIn: parent
        modal: true
        title: "Delete all sessions?"
        width: 440

        onOpened: page.clearAllOpen = true
        onClosed: page.clearAllOpen = false
        onAccepted: page.runClearAll()
        onRejected: page.clearAllOpen = false

        ColumnLayout {
            anchors.fill: parent
            spacing: 12

            Label {
                Layout.fillWidth: true
                text: page.clearAllText
                color: theme.text
                font.pixelSize: 14
                wrapMode: Text.WordWrap
                // A confirmation nobody can read is not a confirmation.
                Accessible.role: Accessible.StaticText
                Accessible.name: text
            }
            RowLayout {
                Layout.fillWidth: true
                spacing: 10
                Item { Layout.fillWidth: true }
                Button {
                    text: "Cancel"
                    onClicked: clearAllDialog.close()
                }
                Button {
                    text: "Delete all"
                    highlighted: true
                    onClicked: clearAllDialog.accept()
                }
            }
        }
    }

    onClearAllOpenChanged: {
        if (clearAllOpen && !clearAllDialog.opened)
            clearAllDialog.open()
    }
}