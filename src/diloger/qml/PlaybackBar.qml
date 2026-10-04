import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts

// Reusable Play / Pause / Stop card for an existing local recording.wav.
// Playback never copies or modifies the audio.
//
// The bar asks for audio by session, never by path: `sessionId` names a folder
// inside the sessions directory and `currentSession` means the session that
// just finished. QML cannot hand the player an arbitrary file.
//
// Height follows the content. A fixed height here is what used to clip the
// transport row: the card claimed 84 px while its three rows need more, so the
// buttons were painted below the card and the session actions that follow took
// their place. Nothing here may reintroduce a literal height.
Rectangle {
    id: bar

    // Named so the geometry probe can address this exact card instead of
    // guessing from captions.
    objectName: "playbackBar"

    property string sessionId: ""
    property bool currentSession: false

    readonly property int cardMargin: 14

    // QML does not track dependencies inside method calls, so a binding on
    // player.positionMs() is evaluated once and never refreshed. These
    // counters are the dependency the player bumps when it emits the matching
    // signal; each binding reads its counter first, so Qt re-evaluates it.
    property int _positionTick: 0
    property int _durationTick: 0
    property int _stateTick: 0
    property string _sourceTick: ""
    property string _requestTick: ""

    // The player's own state string, kept here because a paused recording has to
    // be distinguishable from a stopped one: Pause then Play has to resume, and
    // the visible status has to say which of the two the user is in.
    property string _playbackState: "stopped"

    readonly property string requestedSource: {
        _requestTick
        if (bar.currentSession)
            return app.playbackSource()
        return bar.sessionId === "" ? "" : app.sessionRecordingPath(bar.sessionId)
    }
    readonly property int position: {
        _positionTick
        return player.positionMs()
    }
    readonly property int duration: {
        _durationTick
        return player.durationMs()
    }
    readonly property bool playing: {
        _stateTick
        return _playbackState === "playing"
    }
    readonly property bool paused: {
        _stateTick
        return _playbackState === "paused"
    }
    readonly property string loadedSource: {
        _sourceTick
        return player.sourcePath()
    }
    readonly property bool canPlay: requestedSource !== ""

    // One visible sentence for all three cases. A missing recording says so
    // instead of leaving a dead button or disappearing from the screen.
    readonly property string statusText: !bar.canPlay
        ? "No recording for this session"
        : (bar.playing ? "Playing" : (bar.paused ? "Paused" : "Stopped"))

    // Load only when the source actually changed. Re-loading an already loaded
    // recording would reset it to the start, so Pause followed by Play would
    // replay from the beginning instead of continuing where the user stopped.
    function load() {
        if (bar.requestedSource === "")
            return
        if (bar.loadedSource === bar.requestedSource)
            return
        if (bar.currentSession)
            app.playCurrentRecording()
        else
            app.playSessionRecording(bar.sessionId)
    }

    function fmt(ms) {
        var total = Math.floor(ms / 1000)
        var m = Math.floor(total / 60)
        var s = total % 60
        return (m < 10 ? "0" : "") + m + ":" + (s < 10 ? "0" : "") + s
    }

    // Content-driven. The ColumnLayout reports the height of its header row,
    // slider and transport row; the card adds its own margins on top. A parent
    // that gives this less than this is a layout bug, so the minimum is the
    // same number rather than a smaller magic constant.
    implicitHeight: content.implicitHeight + 2 * cardMargin
    Layout.minimumHeight: implicitHeight
    color: theme.panel
    border.color: theme.border
    border.width: 1
    radius: theme.radius

    // Play after Pause continues from the playhead, because nothing rewinds it.
    // This is deliberately not the same as stopPlayback(), which does rewind.
    function resumePlayback() {
        bar.load()
        player.play()
    }

    // Stop, then rewind explicitly. The seek is part of the contract, not
    // something to infer from the backend: without it, pressing Stop and then
    // Play replayed the tail instead of the whole recording.
    function stopPlayback() {
        player.stop()
        player.seekTo(0)
    }

    ColumnLayout {
        id: content
        anchors.fill: parent
        anchors.margins: bar.cardMargin
        spacing: 10

        RowLayout {
            Layout.fillWidth: true
            spacing: 10

            Label {
                text: "Recording"
                color: theme.text
                font.pixelSize: 15
            }
            Item { Layout.fillWidth: true }
            Text {
                text: bar.duration > 0 ? bar.fmt(bar.position) + " / " + bar.fmt(bar.duration) : "--:-- / --:--"
                color: theme.textDim
                font.pixelSize: 13
                font.family: "Menlo"
            }
        }

        Slider {
            id: seek
            Layout.fillWidth: true
            enabled: bar.duration > 0
            from: 0
            to: Math.max(1, bar.duration)
            value: bar.position
            onMoved: player.seekTo(Math.round(value))
        }

        // The transport row. It is a row of its own, below the timeline, and it
        // is never merged into the session-action row of the screen above it.
        // Each button carries an objectName so a probe can measure it: the three
        // controls used to be invisible behind the session actions, and a
        // caption-based search cannot tell them apart from the other buttons.
        RowLayout {
            Layout.fillWidth: true
            spacing: 10

            Button {
                objectName: "playbackPlayButton"
                text: bar.playing ? "Resume" : "Play"
                // Stopped or paused: both start or continue the recording.
                enabled: bar.canPlay && !bar.playing
                onClicked: bar.resumePlayback()
            }
            Button {
                objectName: "playbackPauseButton"
                text: "Pause"
                enabled: bar.playing
                onClicked: player.pause()
            }
            Button {
                objectName: "playbackStopButton"
                text: "Stop"
                enabled: bar.canPlay && (bar.playing || bar.paused)
                onClicked: bar.stopPlayback()
            }
            Item { Layout.fillWidth: true }
            Text {
                text: bar.statusText
                color: bar.canPlay ? theme.textDim : theme.warn
                font.pixelSize: 12
                elide: Text.ElideRight
                Layout.maximumWidth: Math.max(120, Math.min(260, content.width * 0.4))
            }
        }
    }

    // Player -> bar. Every signal bumps a counter so the bindings above
    // re-evaluate; without this the Pause button stayed disabled and the
    // clock never moved while the recording was actually playing.
    Connections {
        target: player
        function onPositionChanged() { bar._positionTick++ }
        function onDurationChanged() { bar._durationTick++ }
        function onSourceChanged() { bar._sourceTick++ }
        function onPlaybackStateChanged(state) {
            bar._playbackState = state === undefined || state === null ? "stopped" : String(state)
            bar._stateTick++
        }
    }

    // A slot call is invisible to the binding engine, so a session that only
    // just became playable would stay disabled until something else re-created
    // this bar. The refresh signal is the one moment that changes.
    Connections {
        target: app
        function onRefreshRequested() { bar._requestTick++; bar.syncSource() }
    }

    // Keep the loaded source aligned with the requested one.
    function syncSource() {
        if (bar.requestedSource === "" || bar.loadedSource === bar.requestedSource)
            return
        if (bar.playing)
            return
        bar.load()
    }

    onSessionIdChanged: { _requestTick++; syncSource() }
    onCurrentSessionChanged: { _requestTick++; syncSource() }
    Component.onCompleted: { _requestTick++; syncSource() }
}