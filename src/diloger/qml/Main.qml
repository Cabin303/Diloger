import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts

ApplicationWindow {
    id: root
    width: 1000
    height: 700
    minimumWidth: 780
    minimumHeight: 560
    visible: true
    title: "Diloger"
    color: theme.bg

    // The palette every Qt Basic control resolves against. Binding it here, on
    // the root window, is what makes Button, CheckBox, ComboBox, Slider,
    // TextField, Dialog and ScrollBar follow the chosen theme: without it they
    // resolve against the macOS system palette and stay light-coloured inside a
    // dark window. Setting only the QApplication palette is not enough, because
    // QQuickWindow keeps its own.
    //
    // These bindings mirror build_palette() in app/theme.py role for role, so
    // the QML controls and the Python-built palette for native dialogs cannot
    // disagree. Change both together.
    palette.window: theme.bg
    palette.windowText: theme.text
    palette.base: theme.panel
    palette.alternateBase: theme.sunk
    palette.text: theme.text
    palette.placeholderText: theme.textFaint
    palette.button: theme.panelAlt
    palette.buttonText: theme.text
    palette.brightText: theme.onAccent
    palette.light: theme.border
    palette.midlight: theme.border
    palette.mid: theme.borderStrong
    palette.dark: theme.borderStrong
    palette.shadow: theme.sunk
    palette.highlight: theme.accent
    palette.highlightedText: theme.onAccent
    palette.link: theme.accent
    palette.linkVisited: theme.accent
    palette.toolTipBase: theme.panelAlt
    palette.toolTipText: theme.text

    // The roles above feed the Active group. A Control a user cannot use resolves
    // against the Disabled group instead, and an inactive window resolves against
    // Inactive, so both have to be filled too: an unset group falls back to the
    // macOS system greys, which is what made a disabled Select read as a different
    // kind of control rather than an unavailable one.
    //
    // These two groups are assigned in a function rather than bound like the roles
    // above. Re-evaluating a bound role on the base palette resets every other
    // group, so a bound `palette.disabled.*` would be undone by the next token
    // change. Assigning after the bindings have settled keeps them in place.
    //
    // Disabled keeps a real surface and steps the label down to textFaint, so it
    // reads as "unavailable here" without becoming invisible. Inactive stays
    // identical to Active: a window of this app is still this app's window.
    function applyColourGroups() {
        palette.inactive.window = theme.bg;
        palette.inactive.windowText = theme.text;
        palette.inactive.base = theme.panel;
        palette.inactive.alternateBase = theme.sunk;
        palette.inactive.text = theme.text;
        palette.inactive.placeholderText = theme.textFaint;
        palette.inactive.button = theme.panelAlt;
        palette.inactive.buttonText = theme.text;
        palette.inactive.brightText = theme.onAccent;
        palette.inactive.light = theme.border;
        palette.inactive.midlight = theme.border;
        palette.inactive.mid = theme.borderStrong;
        palette.inactive.dark = theme.borderStrong;
        palette.inactive.shadow = theme.sunk;
        palette.inactive.highlight = theme.accent;
        palette.inactive.highlightedText = theme.onAccent;
        palette.inactive.link = theme.accent;
        palette.inactive.linkVisited = theme.accent;
        palette.inactive.toolTipBase = theme.panelAlt;
        palette.inactive.toolTipText = theme.text;

        palette.disabled.window = theme.bg;
        palette.disabled.windowText = theme.textFaint;
        palette.disabled.base = theme.sunk;
        palette.disabled.alternateBase = theme.sunk;
        palette.disabled.text = theme.textFaint;
        palette.disabled.placeholderText = theme.textFaint;
        palette.disabled.button = theme.sunk;
        palette.disabled.buttonText = theme.textFaint;
        palette.disabled.brightText = theme.textFaint;
        palette.disabled.light = theme.border;
        palette.disabled.midlight = theme.border;
        palette.disabled.mid = theme.border;
        palette.disabled.dark = theme.border;
        palette.disabled.shadow = theme.sunk;
        palette.disabled.highlight = theme.border;
        palette.disabled.highlightedText = theme.textFaint;
        palette.disabled.link = theme.textFaint;
        palette.disabled.linkVisited = theme.textFaint;
        palette.disabled.toolTipBase = theme.sunk;
        palette.disabled.toolTipText = theme.textFaint;
    }

    Connections {
        target: theme
        function onPaletteChanged() { root.applyColourGroups() }
    }

    Component.onCompleted: root.applyColourGroups()

    // Error text wins over status text; both are shown in the same banner so a
    // problem is never hidden in a transient property.
    property string statusText: ""
    property string errorText: ""

    readonly property string bannerText: errorText !== "" ? errorText : statusText
    readonly property bool bannerVisible: bannerText !== ""

    function showError(msg) {
        errorText = msg === undefined || msg === null ? "" : String(msg)
        statusText = ""
    }
    function showStatus(msg) {
        if (errorText !== "")
            return          // do not overwrite a visible error
        statusText = msg === undefined || msg === null ? "" : String(msg)
    }
    function dismissBanner() {
        errorText = ""
        statusText = ""
    }

    Connections {
        target: app
        function onErrorMessage(msg) { root.showError(msg) }
        function onStatusMessage(msg) { root.showStatus(msg) }
    }

    ColumnLayout {
        anchors.fill: parent
        spacing: 0

        // ---- visible banner -------------------------------------------
        // In the layout rather than overlaid, so it can never cover the
        // controls of the screen underneath.
        Rectangle {
            id: banner
            Layout.fillWidth: true
            visible: root.bannerVisible
            implicitHeight: bannerRow.implicitHeight + 20
            color: root.errorText !== "" ? theme.dangerSurface : theme.okSurface
            border.width: 1
            border.color: root.errorText !== "" ? theme.record : theme.border

            RowLayout {
                id: bannerRow
                anchors.fill: parent
                anchors.margins: 10
                spacing: 12

                Rectangle {
                    Layout.preferredWidth: 4
                    Layout.preferredHeight: 22
                    Layout.alignment: Qt.AlignVCenter
                    radius: 2
                    color: root.errorText !== "" ? theme.record : theme.ok
                }

                Text {
                    Layout.fillWidth: true
                    Layout.alignment: Qt.AlignVCenter
                    color: theme.text
                    font.pixelSize: 14
                    wrapMode: Text.WordWrap
                    text: root.bannerText
                }

                ToolButton {
                    text: "✕"
                    Layout.preferredWidth: 40
                    Layout.preferredHeight: 28
                    font.pixelSize: 14
                    onClicked: root.dismissBanner()
                    ToolTip.visible: hovered
                    ToolTip.text: "Dismiss message"
                }
            }
        }

        // ---- screen stack ----------------------------------------------

        StackLayout {
            Layout.fillWidth: true
            Layout.fillHeight: true
            Layout.margins: theme.gap
            currentIndex: screenIndex()

            function screenIndex() {
                if (app.screen === "training") return 1
                if (app.screen === "finished") return 2
                if (app.screen === "settings") return 3
                if (app.screen === "setup") return 4
                if (app.screen === "history") return 5
                return 0
            }

            // Named so a geometry probe can find the screen it is measuring.
            // Text markers drift; an objectName does not.
            LibraryScreen { objectName: "libraryScreen" }
            TrainingScreen { objectName: "trainingScreen" }
            FinishedScreen { objectName: "finishedScreen" }
            SettingsScreen { objectName: "settingsScreen" }
            SetupScreen { objectName: "setupScreen" }
            HistoryScreen { objectName: "historyScreen" }
        }

        // ---- footer -----------------------------------------------------
        // The permanent privacy note only. It used to repeat `errorText` /
        // `statusText`, which put every message on screen twice: once in the
        // banner where it can be dismissed, and once here where it could not be
        // told apart from the other footer text. Transient and actionable
        // messages belong to the banner; nothing is copied below.

        Rectangle {
            Layout.fillWidth: true
            Layout.preferredHeight: 34
            color: theme.panel

            RowLayout {
                anchors.fill: parent
                anchors.leftMargin: 16
                anchors.rightMargin: 16
                spacing: 12

                Item { Layout.fillWidth: true }
                Text {
                    text: "All processing stays on this Mac"
                    color: theme.textDim
                    font.pixelSize: 12
                }
            }
        }
    }
}