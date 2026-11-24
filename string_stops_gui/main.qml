import QtQuick 2.15
import QtQuick.Window 2.15
import QtQuick.Controls 2.15
import QtQuick.Layouts 1.15

Window {
    id: window
    width: 820
    height: 760
    minimumWidth: 640
    minimumHeight: 500
    visible: true
    title: "String Stops Checker — MuseScore Inspector"
    color: "#161b22"

    property string scorePath: "/home/allegro/Партитуры/Trio.mscz"
    property var allRows: []
    property int currentFilter: -1 // -1: all, 0: ok, 1: diff, 2: imp
    property string searchText: ""
    property string reportContent: ""
    property var counts: ({total: 0, playable: 0, difficult: 0, impossible: 0})
    property string statusMessage: "Ready"

    Component.onCompleted: {
        loadData();
    }

    function loadData() {
        var xhr = new XMLHttpRequest();
        xhr.open("GET", "file:///tmp/string_stops_active.json");
        xhr.onreadystatechange = function() {
            if (xhr.readyState === XMLHttpRequest.DONE) {
                if (xhr.status === 200 || xhr.responseText.length > 0) {
                    try {
                        var data = JSON.parse(xhr.responseText);
                        window.counts = data.counts || {total: 0, playable: 0, difficult: 0, impossible: 0};
                        window.allRows = data.rows || [];
                        window.reportContent = data.report || "";
                        filterModel();
                        window.statusMessage = data.summary || "Loaded successfully";
                    } catch (e) {
                        window.statusMessage = "Error parsing data: " + e;
                    }
                }
            }
        };
        xhr.send();
    }

    function filterModel() {
        resultsModel.clear();
        for (var i = 0; i < window.allRows.length; ++i) {
            var row = window.allRows[i];
            if (window.currentFilter !== -1 && row.severity !== window.currentFilter) {
                continue;
            }
            if (window.searchText.length > 0) {
                var query = window.searchText.toLowerCase();
                var text = (row.location + " " + row.staffLabel + " " + row.notesLabel + " " + row.details).toLowerCase();
                if (text.indexOf(query) === -1) continue;
            }
            resultsModel.append(row);
        }
    }

    ListModel {
        id: resultsModel
    }

    ColumnLayout {
        anchors.fill: parent
        anchors.margins: 14
        spacing: 12

        // Header
        RowLayout {
            Layout.fillWidth: true
            spacing: 10

            Label {
                text: "🎻 String Stops Checker"
                font.pixelSize: 18
                font.bold: true
                color: "#f0f6fc"
            }

            Label {
                text: window.scorePath
                font.pixelSize: 12
                color: "#8b949e"
                elide: Text.ElideMiddle
                Layout.fillWidth: true
            }

            Button {
                text: "📋 Full Report"
                onClicked: reportDialog.open()
            }
        }

        // Summary Metric Cards
        RowLayout {
            Layout.fillWidth: true
            spacing: 10

            Rectangle {
                Layout.fillWidth: true
                height: 54
                radius: 6
                color: window.currentFilter === -1 ? "#30363d" : "#21262d"
                border.width: 1
                border.color: window.currentFilter === -1 ? "#58a6ff" : "#30363d"
                MouseArea {
                    anchors.fill: parent
                    cursorShape: Qt.PointingHandCursor
                    onClicked: { window.currentFilter = -1; filterModel(); }
                }
                Column {
                    anchors.centerIn: parent
                    spacing: 2
                    Label { text: "TOTAL STOPS"; font.pixelSize: 10; color: "#8b949e"; font.bold: true; anchors.horizontalCenter: parent.horizontalCenter }
                    Label { text: String(window.counts.total); font.pixelSize: 16; font.bold: true; color: "#f0f6fc"; anchors.horizontalCenter: parent.horizontalCenter }
                }
            }

            Rectangle {
                Layout.fillWidth: true
                height: 54
                radius: 6
                color: window.currentFilter === 0 ? "#1b4728" : "#21262d"
                border.width: 1
                border.color: window.currentFilter === 0 ? "#3fb950" : "#238636"
                MouseArea {
                    anchors.fill: parent
                    cursorShape: Qt.PointingHandCursor
                    onClicked: { window.currentFilter = 0; filterModel(); }
                }
                Column {
                    anchors.centerIn: parent
                    spacing: 2
                    Label { text: "PLAYABLE (GREEN)"; font.pixelSize: 10; color: "#3fb950"; font.bold: true; anchors.horizontalCenter: parent.horizontalCenter }
                    Label { text: String(window.counts.playable); font.pixelSize: 16; font.bold: true; color: "#3fb950"; anchors.horizontalCenter: parent.horizontalCenter }
                }
            }

            Rectangle {
                Layout.fillWidth: true
                height: 54
                radius: 6
                color: window.currentFilter === 1 ? "#4d3800" : "#21262d"
                border.width: 1
                border.color: window.currentFilter === 1 ? "#d29922" : "#9e6a03"
                MouseArea {
                    anchors.fill: parent
                    cursorShape: Qt.PointingHandCursor
                    onClicked: { window.currentFilter = 1; filterModel(); }
                }
                Column {
                    anchors.centerIn: parent
                    spacing: 2
                    Label { text: "DIFFICULT (ORANGE)"; font.pixelSize: 10; color: "#d29922"; font.bold: true; anchors.horizontalCenter: parent.horizontalCenter }
                    Label { text: String(window.counts.difficult); font.pixelSize: 16; font.bold: true; color: "#d29922"; anchors.horizontalCenter: parent.horizontalCenter }
                }
            }

            Rectangle {
                Layout.fillWidth: true
                height: 54
                radius: 6
                color: window.currentFilter === 2 ? "#4c1d1e" : "#21262d"
                border.width: 1
                border.color: window.currentFilter === 2 ? "#f85149" : "#da3633"
                MouseArea {
                    anchors.fill: parent
                    cursorShape: Qt.PointingHandCursor
                    onClicked: { window.currentFilter = 2; filterModel(); }
                }
                Column {
                    anchors.centerIn: parent
                    spacing: 2
                    Label { text: "IMPOSSIBLE (RED)"; font.pixelSize: 10; color: "#f85149"; font.bold: true; anchors.horizontalCenter: parent.horizontalCenter }
                    Label { text: String(window.counts.impossible); font.pixelSize: 16; font.bold: true; color: "#f85149"; anchors.horizontalCenter: parent.horizontalCenter }
                }
            }
        }

        // Search & Filter row
        RowLayout {
            Layout.fillWidth: true
            spacing: 8

            TextField {
                id: searchInput
                placeholderText: "Search by measure (e.g. M30), instrument (Cello), pitch…"
                Layout.fillWidth: true
                color: "#f0f6fc"
                placeholderTextColor: "#8b949e"
                background: Rectangle {
                    color: "#21262d"
                    radius: 4
                    border.color: searchInput.activeFocus ? "#58a6ff" : "#30363d"
                }
                onTextChanged: {
                    window.searchText = text;
                    filterModel();
                }
            }

            Button {
                text: "Clear Filter"
                enabled: window.currentFilter !== -1 || window.searchText.length > 0
                onClicked: {
                    window.currentFilter = -1;
                    window.searchText = "";
                    searchInput.text = "";
                    filterModel();
                }
            }
        }

        // Results List
        ScrollView {
            Layout.fillWidth: true
            Layout.fillHeight: true
            clip: true

            ListView {
                id: listView
                anchors.fill: parent
                spacing: 6
                model: resultsModel
                delegate: Rectangle {
                    width: listView.width
                    height: col.implicitHeight + 16
                    radius: 6
                    color: "#21262d"
                    border.width: 1
                    border.color: model.severity === 2 ? "#da3633" : (model.severity === 1 ? "#d29922" : "#238636")

                    Column {
                        id: col
                        anchors.fill: parent
                        anchors.margins: 10
                        spacing: 4

                        RowLayout {
                            width: parent.width
                            spacing: 8

                            // Location
                            Label {
                                text: model.location
                                font.bold: true
                                font.pixelSize: 13
                                color: "#f0f6fc"
                            }

                            // Staff / Instrument
                            Label {
                                text: "· " + model.staffLabel
                                font.pixelSize: 12
                                color: "#8b949e"
                            }

                            Item { Layout.fillWidth: true }

                            // Notes
                            Label {
                                text: model.notesLabel
                                font.bold: true
                                font.pixelSize: 13
                                color: "#e6edf3"
                            }

                            // Classification Badge
                            Rectangle {
                                width: badgeText.implicitWidth + 12
                                height: 22
                                radius: 11
                                color: model.severity === 2 ? "#da3633" : (model.severity === 1 ? "#d29922" : "#238636")
                                Label {
                                    id: badgeText
                                    anchors.centerIn: parent
                                    text: model.classification
                                    font.bold: true
                                    font.pixelSize: 11
                                    color: "#ffffff"
                                }
                            }
                        }

                        // Details line
                        Label {
                            width: parent.width
                            text: model.details
                            wrapMode: Text.WordWrap
                            font.pixelSize: 11
                            color: "#c9d1d9"
                        }
                    }
                }
            }
        }

        // Status bar
        RowLayout {
            Layout.fillWidth: true
            Label {
                text: window.statusMessage
                font.pixelSize: 11
                color: "#8b949e"
                Layout.fillWidth: true
            }
            Label {
                text: "Showing " + resultsModel.count + " of " + window.allRows.length + " stops"
                font.pixelSize: 11
                color: "#8b949e"
            }
        }
    }

    // Report Dialog Modal
    Dialog {
        id: reportDialog
        title: "Complete Analysis Report"
        width: Math.min(window.width - 40, 720)
        height: Math.min(window.height - 40, 680)
        anchors.centerIn: parent
        modal: true
        standardButtons: Dialog.Close

        ColumnLayout {
            anchors.fill: parent
            spacing: 8

            ScrollView {
                Layout.fillWidth: true
                Layout.fillHeight: true
                clip: true

                TextArea {
                    id: reportTextArea
                    text: window.reportContent
                    readOnly: true
                    selectByMouse: true
                    wrapMode: TextEdit.Wrap
                    font.family: "monospace"
                    font.pixelSize: 11
                    color: "#f0f6fc"
                    background: Rectangle {
                        color: "#161b22"
                        radius: 4
                        border.color: "#30363d"
                    }
                }
            }

            Button {
                text: "📋 Copy All Report to Clipboard"
                Layout.alignment: Qt.AlignRight
                onClicked: {
                    reportTextArea.selectAll();
                    reportTextArea.copy();
                    window.statusMessage = "Report copied to clipboard!";
                    reportDialog.close();
                }
            }
        }
    }
}
