import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts

FocusScope {
    id: page

    property var info: ({
        "durationSeconds": 0,
        "promptsReached": 0,
        "totalPrompts": 0,
        "hasRecording": false,
        "recordingRetained": false,
        "recordingIsTemporary": false,
        "recordingPath": "",
        "transcriptionStatus": "notRequested",
        "transcriptBlocks": [],
        "transcriptNotice": "",
        "hasTranscript": false,
        "transcriptError": "",
        "sessionDir": "",
        "keepRecording": false
    })

    property bool detailsOpen: false

    // Keep audio only makes sense for audio that is still in the temporary
    // location: it exists, it has not been deleted, and no transcription is
    // running that could race with the move.
    readonly property bool canKeepAudio: info.hasRecording
                                       && info.recordingIsTemporary
                                       && !info.recordingRetained
                                       && !app.transcribing

    readonly property bool canDeleteAudio: info.hasRecording
                                         && !app.transcribing

    readonly property string recordingState: !info.hasRecording
        ? "not recorded"
        : (info.recordingRetained
           ? "kept in the session folder"
           : (info.recordingIsTemporary ? "temporary, will be deleted after transcription" : "not kept"))

    function refresh() {
        var fresh = app.finishedInfo()
        for (var key in page.info) {
            if (fresh[key] === undefined) fresh[key] = page.info[key]
        }
        page.info = fresh
    }

    function fmtDuration(seconds) {
        var total = Math.max(0, Math.floor(seconds || 0))
        var m = Math.floor(total / 60)
        var s = total % 60
        return m + " min " + (s < 10 ? "0" : "") + s + " s"
    }

    Component.onCompleted: refresh()

    Connections {
        target: app
        function onRefreshRequested() { page.refresh() }
    }

    ScrollView {
        anchors.fill: parent
        contentWidth: availableWidth

        ColumnLayout {
            width: page.width
            spacing: theme.gap

            RowLayout {
                Layout.fillWidth: true
                Layout.leftMargin: 6
                Layout.rightMargin: 6
                Label {
                    text: "Session finished"
                    font.pixelSize: 30
                    font.weight: Font.Bold
                    color: theme.text
                }
                Item { Layout.fillWidth: true }
            }

            // ---- metrics ------------------------------------------------
            // Three equal columns with no fixed height: the old fixed 128 px
            // card clipped its own contents and the three columns drifted to
            // different widths, so the metrics did not read as one row.
            Rectangle {
                Layout.fillWidth: true
                Layout.leftMargin: 6
                Layout.rightMargin: 6
                implicitHeight: metrics.implicitHeight + 40
                color: theme.panel
                border.color: theme.border
                radius: theme.radius

                RowLayout {
                    id: metrics
                    anchors.fill: parent
                    anchors.margins: 20
                    spacing: 24

                    ColumnLayout {
                        Layout.fillWidth: true
                        Layout.alignment: Qt.AlignTop
                        spacing: 4
                        Text {
                            text: "Duration"
                            color: theme.textDim
                            font.pixelSize: 13
                        }
                        Text {
                            text: page.fmtDuration(page.info.durationSeconds)
                            color: theme.text
                            font.pixelSize: 22
                            font.weight: Font.DemiBold
                            wrapMode: Text.WordWrap
                        }
                    }

                    ColumnLayout {
                        Layout.fillWidth: true
                        Layout.alignment: Qt.AlignTop
                        spacing: 4
                        Text {
                            text: "Prompts reached"
                            color: theme.textDim
                            font.pixelSize: 13
                        }
                        Text {
                            text: (page.info.promptsReached || 0) + " / " + (page.info.totalPrompts || 0)
                            color: theme.text
                            font.pixelSize: 22
                            font.weight: Font.DemiBold
                            wrapMode: Text.WordWrap
                        }
                    }

                    ColumnLayout {
                        Layout.fillWidth: true
                        Layout.alignment: Qt.AlignTop
                        spacing: 4
                        Text {
                            text: "Recording"
                            color: theme.textDim
                            font.pixelSize: 13
                        }
                        Text {
                            text: page.recordingState
                            color: page.info.hasRecording ? theme.ok : theme.textDim
                            font.pixelSize: 14
                            wrapMode: Text.WordWrap
                            Layout.fillWidth: true
                        }
                        Text {
                            text: "Whisper: " + page.info.transcriptionStatus
                            color: page.info.transcriptionStatus === "succeeded" ? theme.ok : theme.textDim
                            font.pixelSize: 12
                        }
                    }
                }
            }

            // ---- transcript ---------------------------------------------
            // Height follows the content instead of a fixed 240 px, so a long
            // transcript scrolls inside the card rather than being cut off.

            Rectangle {
                Layout.fillWidth: true
                Layout.leftMargin: 6
                Layout.rightMargin: 6
                implicitHeight: transcriptCard.implicitHeight + 32
                color: theme.panel
                border.color: theme.border
                radius: theme.radius

                ColumnLayout {
                    id: transcriptCard
                    width: parent.width
                    anchors.top: parent.top
                    anchors.left: parent.left
                    anchors.right: parent.right
                    anchors.margins: 16
                    spacing: 10

                    RowLayout {
                        Layout.fillWidth: true
                        Label {
                            text: "Transcript"
                            color: theme.text
                            font.pixelSize: 17
                        }
                        Item { Layout.fillWidth: true }
                        Text {
                            text: page.info.transcriptNotice
                            color: theme.textDim
                            font.pixelSize: 12
                            elide: Text.ElideRight
                            Layout.maximumWidth: Math.max(120, transcriptCard.width * 0.45)
                        }
                    }

                    Text {
                        Layout.fillWidth: true
                        visible: page.info.transcriptError !== ""
                        text: page.info.transcriptError
                        color: theme.warn
                        font.pixelSize: 13
                        wrapMode: Text.WordWrap
                    }

                    // The same component History uses. One renderer, one view
                    // model: two implementations is how the two screens came to
                    // disagree about what a transcript looks like.
                    TranscriptView {
                        id: finishedTranscript
                        objectName: "finishedTranscriptView"
                        Layout.fillWidth: true
                        Layout.topMargin: page.info.transcriptError !== "" ? 4 : 0
                        Layout.bottomMargin: 6
                        blocks: page.info.transcriptBlocks || []
                        notice: page.info.transcriptNotice
                        emptyText: "No transcript yet. The recording stays local until you run transcription."
                    }
                }
            }

            // ---- playback ----------------------------------------------
            // The card stays on screen even when this session has no audio: it
            // then says "No recording for this session" instead of vanishing,
            // so the user learns why there is nothing to play. It sizes itself
            // from its own content, so the transport row is always inside it and
            // the action Flow below always starts after its real bottom edge.

            PlaybackBar {
                Layout.fillWidth: true
                Layout.leftMargin: 6
                Layout.rightMargin: 6
                visible: page.info.sessionDir !== ""
                currentSession: true
            }

            // ---- audio / transcript actions ------------------------------
            // Flow, not a RowLayout: four buttons of that size do not fit side
            // by side in a normal window, and a RowLayout silently pushed the
            // last ones outside the visible area.

            Flow {
                Layout.fillWidth: true
                Layout.leftMargin: 6
                Layout.rightMargin: 6
                Layout.topMargin: 4
                spacing: 10

                Button {
                    text: page.info.transcriptionStatus === "succeeded" ? "Transcribe again" : "Transcribe now"
                    enabled: page.info.hasRecording && !app.transcribing
                    onClicked: app.retryTranscription()
                    ToolTip.visible: hovered
                    ToolTip.text: "Run local Whisper over the kept recording."
                }
                Button {
                    text: "Export Markdown"
                    enabled: page.info.sessionDir !== "" && !app.transcribing
                    onClicked: app.exportCurrentSession()
                    ToolTip.visible: hovered
                    ToolTip.text: "Choose where to save the transcript."
                }
                Button {
                    // Only while it can still do something: the old screen kept
                    // an "Audio already kept" button around as a dead primary
                    // action.
                    text: "Keep audio"
                    visible: page.canKeepAudio
                    enabled: page.canKeepAudio
                    onClicked: app.keepOrDeleteAudio()
                    ToolTip.visible: hovered
                    ToolTip.text: "Move the temporary recording into the session folder."
                }
                Button {
                    text: "Delete audio"
                    visible: page.canDeleteAudio
                    enabled: page.canDeleteAudio
                    onClicked: app.discardAudio()
                    ToolTip.visible: hovered
                    ToolTip.text: "Delete the only copy of this recording."
                }
            }

            // The session folder is a detail, not the headline.
            ColumnLayout {
                Layout.fillWidth: true
                Layout.leftMargin: 6
                Layout.rightMargin: 6
                spacing: 4

                Button {
                    text: page.detailsOpen ? "Hide details" : "Details"
                    flat: true
                    onClicked: page.detailsOpen = !page.detailsOpen
                    ToolTip.visible: hovered
                    ToolTip.text: "Session folder and file paths."
                }

                Text {
                    Layout.fillWidth: true
                    visible: page.detailsOpen && page.info.sessionDir !== ""
                    text: page.info.sessionDir === "" ? "" : "Session folder: " + page.info.sessionDir
                    color: theme.textDim
                    font.pixelSize: 12
                    wrapMode: Text.WrapAnywhere
                }
                Text {
                    Layout.fillWidth: true
                    visible: page.detailsOpen && page.info.transcriptMarkdownPath !== ""
                    text: page.info.transcriptMarkdownPath === "" ? "" : "Markdown: " + page.info.transcriptMarkdownPath
                    color: theme.textDim
                    font.pixelSize: 12
                    wrapMode: Text.WrapAnywhere
                }
            }

            Item { Layout.fillHeight: true }

            RowLayout {
                Layout.fillWidth: true
                Layout.margins: 6
                Button {
                    text: "Back to library"
                    Layout.preferredHeight: 48
                    highlighted: true
                    onClicked: app.showLibrary()
                }
                Item { Layout.fillWidth: true }
            }
        }
    }
}