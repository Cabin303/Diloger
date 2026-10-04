import QtQuick
import QtQuick.Layouts

// The one transcript view. Finished and History both render this component and
// nothing else: two separate transcript renderers is how the screen ended up
// showing export Markdown in one place and plain text in another.
//
// Blocks arrive already structured -- label, timecode, text -- so nothing here
// parses, re-orders or re-formats words. `#`, `- Date:` and other Markdown
// syntax cannot appear because the source is not Markdown.
ColumnLayout {
    id: view

    // [{label, start_ms, end_ms, range, text}, ...]
    property var blocks: []
    property string notice: ""
    property string emptyText: "No transcript yet."
    property bool loading: false

    spacing: 12

    Text {
        Layout.fillWidth: true
        visible: view.notice !== "" && view.blocks.length > 0
        text: view.notice
        color: theme.textFaint
        font.pixelSize: 12
        wrapMode: Text.WordWrap
    }

    Text {
        Layout.fillWidth: true
        visible: view.blocks.length === 0
        text: view.emptyText
        color: theme.textDim
        font.pixelSize: 14
        wrapMode: Text.WordWrap
    }

    // One column, one block. A Repeater rather than ListView so the whole
    // transcript can be measured and scrolled by the parent card without a
    // nested view fighting it for the same space.
    ColumnLayout {
        Layout.fillWidth: true
        spacing: 14
        visible: view.blocks.length > 0

        Repeater {
            model: view.blocks

            delegate: ColumnLayout {
                id: blockRow
                required property var modelData
                readonly property var block: blockRow.modelData
                readonly property bool isQuestion:
                    String(block.label || "").indexOf("Question ") === 0

                Layout.fillWidth: true
                spacing: 3

                RowLayout {
                    Layout.fillWidth: true
                    spacing: 8

                    Text {
                        text: blockRow.block.label || ""
                        color: blockRow.isQuestion ? theme.accent : theme.textDim
                        font.pixelSize: 12
                        font.bold: true
                    }
                    Item { Layout.fillWidth: true }
                    Text {
                        // Milliseconds, from the view model. The screen and the
                        // export share one formatter, so they cannot disagree.
                        text: "[" + view.fmt(blockRow.block.start_ms)
                              + " – " + view.fmt(blockRow.block.end_ms) + "]"
                        color: theme.textFaint
                        font.pixelSize: 11
                        font.family: "Menlo"
                    }
                }

                Text {
                    Layout.fillWidth: true
                    text: blockRow.block.text || ""
                    color: theme.text
                    font.pixelSize: 15
                    wrapMode: Text.WordWrap
                }
            }
        }
    }

    function fmt(ms) {
        var total = Math.max(0, Math.floor(ms / 1000))
        var h = Math.floor(total / 3600)
        var m = Math.floor((total % 3600) / 60)
        var s = total % 60
        var p2 = function (n) { return (n < 10 ? "0" : "") + n }
        var p3 = function (n) { return (n < 10 ? "00" : (n < 100 ? "0" : "")) + n }
        return p2(h) + ":" + p2(m) + ":" + p2(s) + "." + p3(Math.max(0, Math.floor(ms)) % 1000)
    }
}