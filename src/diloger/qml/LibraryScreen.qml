import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts

FocusScope {
    id: page

    property var fileList: []

    function refresh() {
        fileList = app.files()
    }

    Component.onCompleted: refresh()

    Connections {
        target: app
        function onLibraryChanged() { page.refresh() }
        function onRefreshRequested() { page.refresh() }
    }

    ColumnLayout {
        anchors.fill: parent
        spacing: theme.gap

        RowLayout {
            Layout.fillWidth: true
            spacing: theme.gap

            Label {
                text: "Library"
                font.pixelSize: 30
                font.weight: Font.Bold
                color: theme.text
            }
            Item { Layout.fillWidth: true }
            Button {
                text: "History"
                onClicked: app.showHistory()
            }
            Button {
                text: "Settings"
                onClicked: app.showSettings()
            }
        }

        Rectangle {
            Layout.fillWidth: true
            Layout.preferredHeight: 62
            color: theme.panel
            border.color: theme.border
            border.width: 1
            radius: theme.radius

            RowLayout {
                anchors.fill: parent
                anchors.leftMargin: 16
                anchors.rightMargin: 12
                spacing: 12

                ColumnLayout {
                    spacing: 2
                    Layout.fillWidth: true
                    Text {
                        text: app.libraryPath === "" ? "No library folder selected" : app.libraryPath
                        color: app.libraryPath === "" ? theme.warn : theme.text
                        font.pixelSize: 15
                        elide: Text.ElideMiddle
                        Layout.fillWidth: true
                    }
                    Text {
                        text: "One non-empty line per prompt. TXT or MD."
                        color: theme.textDim
                        font.pixelSize: 12
                    }
                }
                Button {
                    text: "Choose folder…"
                    onClicked: app.chooseFolder("library")
                }
                Button {
                    text: "Rescan"
                    onClicked: app.rescanLibrary()
                }
            }
        }

        Rectangle {
            Layout.fillWidth: true
            Layout.fillHeight: true
            color: theme.panel
            border.color: theme.border
            border.width: 1
            radius: theme.radius

            ListView {
                id: list
                anchors.fill: parent
                anchors.margins: 8
                clip: true
                spacing: 4
                model: page.fileList

                delegate: Rectangle {
                    width: list.width
                    height: 64
                    radius: theme.radius
                    color: app.selectedContentPath === modelData.path
                           ? theme.accentDim : "transparent"
                    border.width: app.selectedContentPath === modelData.path ? 1 : 0
                    border.color: theme.accent

                    RowLayout {
                        anchors.fill: parent
                        anchors.leftMargin: 14
                        anchors.rightMargin: 14
                        spacing: theme.gap

                        ColumnLayout {
                            spacing: 2
                            Layout.fillWidth: true
                            Layout.minimumWidth: 0
                            Text {
                                Layout.fillWidth: true
                                text: modelData.name
                                color: theme.text
                                font.pixelSize: 16
                                elide: Text.ElideMiddle
                            }
                            Text {
                                text: modelData.prompts + " prompts"
                                color: theme.textDim
                                font.pixelSize: 12
                            }
                        }
                        // Fixed-width and fixed-height, right-aligned, so every
                        // card lines up regardless of name or path length. The
                        // button owns the whole click: there is deliberately no
                        // full-row MouseArea over it.
                        SelectButton {
                            Layout.preferredWidth: 116
                            Layout.preferredHeight: 34
                            Layout.alignment: Qt.AlignRight | Qt.AlignVCenter
                            selected: app.selectedContentPath === modelData.path
                            onClicked: app.selectFile(modelData.path)
                        }
                    }
                }

                Label {
                    anchors.centerIn: parent
                    visible: page.fileList.length === 0
                    text: app.libraryPath === ""
                          ? "Select a library folder to see your TXT and MD files."
                          : "No TXT or MD files in this folder."
                    color: theme.textDim
                    font.pixelSize: 15
                    wrapMode: Text.WordWrap
                    horizontalAlignment: Text.AlignHCenter
                    width: parent.width - 60
                }
            }
        }

        RowLayout {
            Layout.fillWidth: true
            spacing: theme.gap

            Rectangle {
                Layout.fillWidth: true
                Layout.preferredHeight: 74
                color: theme.panelAlt
                border.color: theme.border
                border.width: 1
                radius: theme.radius

                ColumnLayout {
                    anchors.centerIn: parent
                    spacing: 3
                    Text {
                        text: "Drop a TXT or MD file here"
                        color: theme.text
                        font.pixelSize: 15
                    }
                    Text {
                        text: "Runs it as a one-off session without adding it to the library."
                        color: theme.textDim
                        font.pixelSize: 12
                    }
                }

                DropArea {
                    anchors.fill: parent
                    onDropped: function(drop) {
                        if (drop.hasUrls && drop.urls.length > 0) {
                            app.loadDroppedFile(drop.urls[0].toString().replace("file://", ""))
                        }
                    }
                }
            }

            Button {
                text: "Session setup"
                enabled: app.selectedContentPath !== ""
                onClicked: app.showSetup()
                Layout.preferredWidth: 160
                Layout.preferredHeight: 74
            }
        }
    }
}
