// Задачи Ани — виджет Plasma: карточка в цветах текущей темы с задачами на сегодня, потом
// и сделанными сегодня.
// Галочка у сделанной задачи снимается — задача возвращается (если отметил мискликом).
// Двойной клик по тексту — правка, правая кнопка — меню. Данные — локальный API ядра
// (127.0.0.1, токен из папки данных); просьбы к маскоту — `python -m ubermensch.desktop.link`.
// Значения @…@ подставляет scripts/install_widget.sh.
import QtQuick
import QtQuick.Layouts
import QtQuick.Controls as QQC2
import org.kde.plasma.plasmoid
import org.kde.plasma.core as PlasmaCore
import org.kde.plasma.plasma5support as P5Support
import org.kde.kirigami as Kirigami

PlasmoidItem {
    id: root

    readonly property string api: "@API@"
    readonly property string tokenFile: "@TOKEN_FILE@"
    readonly property string linkCommand: "@LINK@"

    // цвета и шрифт — из текущей темы Plasma, виджет подходит к любому оформлению
    readonly property color card: Qt.rgba(Kirigami.Theme.backgroundColor.r, Kirigami.Theme.backgroundColor.g,
                                           Kirigami.Theme.backgroundColor.b, 0.82)
    readonly property color hairline: Qt.rgba(accent.r, accent.g, accent.b, 0.35)
    readonly property color ink: Kirigami.Theme.textColor
    readonly property color pencil: Kirigami.Theme.disabledTextColor
    readonly property color accent: Kirigami.Theme.highlightColor
    readonly property color accentLight: Qt.lighter(Kirigami.Theme.highlightColor, 1.2)
    readonly property color onAccent: Kirigami.Theme.highlightedTextColor
    readonly property color warn: Kirigami.Theme.negativeTextColor
    readonly property string font: Kirigami.Theme.defaultFont.family
    readonly property var months: ["января", "февраля", "марта", "апреля", "мая", "июня", "июля",
                                   "августа", "сентября", "октября", "ноября", "декабря"]
    readonly property var weekdays: ["Вс", "Пн", "Вт", "Ср", "Чт", "Пт", "Сб"]

    property string token: ""
    property bool online: false
    property string today: Qt.formatDate(new Date(), "yyyy-MM-dd")
    property var tasks: []
    property int editingId: -1
    property var undoTask: null

    readonly property var nowTasks: tasks.filter(t => t.status === "open" && (t.due || today) <= today)
    readonly property var laterTasks: tasks.filter(t => t.status === "open" && (t.due || today) > today)
    readonly property var doneTasks: tasks.filter(t => t.status === "done")

    preferredRepresentation: Plasmoid.formFactor === PlasmaCore.Types.Planar ? fullRepresentation
                                                                            : compactRepresentation
    Plasmoid.backgroundHints: PlasmaCore.Types.NoBackground
    Plasmoid.icon: "view-task"
    toolTipMainText: "Задачи Ани"
    toolTipSubText: online ? `Сегодня: ${nowTasks.length}, сделано: ${doneTasks.length}`
                           : "Аня не на связи"

    // --- данные ---

    P5Support.DataSource {
        id: shell
        engine: "executable"
        connectedSources: []
        property var callbacks: ({})
        onNewData: (source, data) => {
            const callback = callbacks[source]
            delete callbacks[source]
            disconnectSource(source)
            if (callback) callback(data["stdout"] || "", data["exit code"])
        }
        function run(command, callback) {
            callbacks[command] = callback
            connectSource(command)
        }
    }

    function quote(text) { return "'" + String(text).replace(/'/g, "'\\''") + "'" }

    function loadToken(then) {
        shell.run("cat " + quote(tokenFile), (out) => {
            token = out.trim()
            if (then) then()
        })
    }

    function request(method, path, payload, done) {
        if (!token) { loadToken(() => { if (token) request(method, path, payload, done) }); return }
        const xhr = new XMLHttpRequest()
        xhr.open(method, api + path)
        xhr.setRequestHeader("Authorization", "Bearer " + token)
        xhr.setRequestHeader("Content-Type", "application/json")
        xhr.onreadystatechange = () => {
            if (xhr.readyState !== XMLHttpRequest.DONE) return
            online = xhr.status === 200
            if (xhr.status === 401) token = ""  // токен пересоздан — перечитать
            if (online && done) done(JSON.parse(xhr.responseText || "{}"))
        }
        xhr.send(payload ? JSON.stringify(payload) : null)
    }

    function refresh() {
        request("GET", "/api/tasks", null, (data) => {
            if (editingId >= 0) return  // не сбивать правку на полуслове
            today = data.today
            tasks = data.tasks
        })
    }

    function setStatus(task, status) {
        request("POST", `/api/tasks/${task.id}`, {status: status}, refresh)
        if (status === "done") { undoTask = task; undoTimer.restart() }
    }

    function edit(task, changes) { request("POST", `/api/tasks/${task.id}/edit`, changes, refresh) }

    function add(title) {
        if (title.trim()) request("POST", "/api/tasks", {title: title.trim()}, refresh)
    }

    function ask(command) { shell.run(linkCommand + " " + command, () => {}) }

    function isoDay(offset) {
        const d = new Date()
        d.setDate(d.getDate() + offset)
        return Qt.formatDate(d, "yyyy-MM-dd")
    }

    function shortDate(iso) { return iso ? `${iso.slice(8, 10)}.${iso.slice(5, 7)}` : "" }

    Timer { interval: 5000; running: true; repeat: true; triggeredOnStart: true; onTriggered: refresh() }
    Timer { id: undoTimer; interval: 7000; onTriggered: undoTask = null }

    // --- в панели: значок со счётчиком ---

    compactRepresentation: MouseArea {
        onClicked: root.expanded = !root.expanded
        Kirigami.Icon { anchors.fill: parent; source: "view-task"; active: parent.containsMouse }
        Rectangle {
            visible: root.nowTasks.length > 0
            anchors { right: parent.right; top: parent.top }
            width: Math.max(height, badge.implicitWidth + 6); height: 14; radius: 7
            color: root.accent
            Text { id: badge; anchors.centerIn: parent; text: root.nowTasks.length; color: root.onAccent; font.pixelSize: 10 }
        }
    }

    // --- карточка ---

    fullRepresentation: Item {
        Layout.minimumWidth: Kirigami.Units.gridUnit * 14
        Layout.minimumHeight: Kirigami.Units.gridUnit * 12
        Layout.preferredWidth: 320
        Layout.preferredHeight: 440

        Rectangle {
            id: sheet
            anchors.fill: parent; anchors.margins: 6
            radius: 14; color: root.card; clip: true
            border.width: 1; border.color: root.hairline

            ColumnLayout {
                anchors { fill: parent; leftMargin: 20; rightMargin: 16; topMargin: 14; bottomMargin: 10 }
                spacing: 4

                RowLayout {
                    Layout.fillWidth: true
                    Text {
                        Layout.fillWidth: true
                        text: {
                            const d = new Date(root.today + "T12:00:00")
                            return `${root.weekdays[d.getDay()]}, ${d.getDate()} ${root.months[d.getMonth()]}`
                        }
                        font.family: root.font; font.pixelSize: 19; font.weight: Font.DemiBold; color: root.ink
                    }
                    Text {
                        text: "✦"; font.pixelSize: 16; color: planArea.containsMouse ? root.accentLight : root.accent
                        MouseArea {
                            id: planArea; anchors.fill: parent; hoverEnabled: true
                            cursorShape: Qt.PointingHandCursor; onClicked: root.ask("plan")
                        }
                        QQC2.ToolTip.visible: planArea.containsMouse; QQC2.ToolTip.text: "Спланировать день с Аней"
                    }
                    Text {
                        text: "⋯"; font.pixelSize: 18; color: moreArea.containsMouse ? root.accentLight : root.pencil
                        MouseArea {
                            id: moreArea; anchors.fill: parent; hoverEnabled: true
                            cursorShape: Qt.PointingHandCursor; onClicked: moreMenu.popup()
                        }
                        QQC2.Menu {
                            id: moreMenu
                            QQC2.MenuItem { text: "Спросить Аню"; onTriggered: root.ask("prompt") }
                            QQC2.MenuItem { text: "История ответов"; onTriggered: root.ask("history") }
                            QQC2.MenuItem { text: "Метрики"; onTriggered: root.ask("metrics") }
                            QQC2.MenuItem { text: "Показать Аню"; onTriggered: root.ask("show") }
                        }
                    }
                }

                Rectangle {  // золотая черта под датой
                    Layout.fillWidth: true; Layout.bottomMargin: 2
                    implicitHeight: 1
                    gradient: Gradient {
                        orientation: Gradient.Horizontal
                        GradientStop { position: 0; color: root.accent }
                        GradientStop { position: 1; color: "transparent" }
                    }
                }

                Text {
                    visible: !root.online
                    text: "Аня не на связи — ядро не запущено?"
                    font.family: root.font; font.pixelSize: 12; color: root.pencil
                }

                QQC2.ScrollView {
                    Layout.fillWidth: true; Layout.fillHeight: true
                    QQC2.ScrollBar.horizontal.policy: QQC2.ScrollBar.AlwaysOff
                    contentWidth: availableWidth

                    ColumnLayout {
                        width: parent.width
                        spacing: 2
                        Section { title: "Сегодня"; items: root.nowTasks; empty: "Пусто — можно выдохнуть" }
                        Section { title: "Потом"; items: root.laterTasks }
                        Section { title: "Сделано сегодня"; items: root.doneTasks }
                    }
                }

                Rectangle {  // «вернуть» сразу после отметки
                    Layout.fillWidth: true; implicitHeight: 28; radius: 8
                    visible: root.undoTask !== null
                    color: Qt.rgba(root.accent.r, root.accent.g, root.accent.b, 0.12)
                    RowLayout {
                        anchors.fill: parent; anchors.leftMargin: 10; anchors.rightMargin: 10
                        Text {
                            Layout.fillWidth: true; elide: Text.ElideRight
                            text: root.undoTask ? `✓ ${root.undoTask.title}` : ""
                            font.family: root.font; font.pixelSize: 12; color: root.ink
                        }
                        Text {
                            text: "вернуть"; font.family: root.font; font.pixelSize: 12; font.underline: true
                            color: root.accent
                            MouseArea {
                                anchors.fill: parent; cursorShape: Qt.PointingHandCursor
                                onClicked: { root.setStatus(root.undoTask, "open"); root.undoTask = null }
                            }
                        }
                    }
                }

                QQC2.TextField {
                    id: newTask
                    Layout.fillWidth: true
                    placeholderText: "+ добавить задачу"
                    font.family: root.font; font.pixelSize: 13; color: root.ink
                    placeholderTextColor: root.pencil
                    background: Item {}
                    onAccepted: { root.add(text); text = "" }
                }
            }
        }
    }

    // --- раздел и строка задачи ---

    component Section: ColumnLayout {
        property string title
        property var items: []
        property string empty: ""
        Layout.fillWidth: true
        spacing: 1
        visible: items.length > 0 || empty !== ""
        Text {
            text: title.toUpperCase(); font.family: root.font; font.pixelSize: 11
            font.weight: Font.Bold; font.letterSpacing: 2; color: root.accent
            topPadding: 8; bottomPadding: 2
        }
        Text {
            visible: items.length === 0 && empty !== ""
            text: empty; font.family: root.font; font.italic: true; font.pixelSize: 13; color: root.pencil
        }
        Repeater { model: items; delegate: TaskRow { task: modelData } }
    }

    component TaskRow: RowLayout {
        id: row
        property var task
        readonly property bool done: task.status === "done"
        readonly property bool overdue: !done && task.due && task.due < root.today
        readonly property bool editing: root.editingId === task.id
        Layout.fillWidth: true
        spacing: 8

        Rectangle {  // кружок: клик отмечает, по сделанной — возвращает
            Layout.alignment: Qt.AlignTop; Layout.topMargin: 3
            width: 15; height: 15; radius: 7.5
            color: row.done ? root.accent : "transparent"
            border.width: 1.3; border.color: boxArea.containsMouse ? root.accentLight : root.accent
            Text {
                visible: row.done; anchors.centerIn: parent
                text: "✓"; font.pixelSize: 10; font.bold: true; color: root.onAccent
            }
            MouseArea {
                id: boxArea; anchors.fill: parent; anchors.margins: -5; hoverEnabled: true
                cursorShape: Qt.PointingHandCursor
                onClicked: root.setStatus(row.task, row.done ? "open" : "done")
            }
            QQC2.ToolTip.visible: boxArea.containsMouse && row.done
            QQC2.ToolTip.text: "Вернуть в список"
        }

        Text {
            visible: !row.editing
            Layout.fillWidth: true
            text: row.task.title; wrapMode: Text.Wrap
            font.family: root.font; font.pixelSize: 13; font.strikeout: row.done
            color: row.done ? root.pencil : root.ink
            MouseArea {
                anchors.fill: parent; acceptedButtons: Qt.LeftButton | Qt.RightButton
                onDoubleClicked: if (!row.done) root.editingId = row.task.id
                onClicked: (mouse) => { if (mouse.button === Qt.RightButton) rowMenu.popup() }
            }
        }
        QQC2.TextField {
            visible: row.editing
            Layout.fillWidth: true
            text: row.task.title
            font.family: root.font; font.pixelSize: 13; color: root.ink
            onVisibleChanged: if (visible) { forceActiveFocus(); selectAll() }
            onAccepted: {
                if (text.trim() && text.trim() !== row.task.title) root.edit(row.task, {title: text.trim()})
                root.editingId = -1
            }
            Keys.onEscapePressed: root.editingId = -1
            onActiveFocusChanged: if (!activeFocus && row.editing) root.editingId = -1
        }
        Text {
            visible: !row.editing && !!row.task.due && row.task.due !== root.today && !row.done
            Layout.alignment: Qt.AlignTop; Layout.topMargin: 3
            text: (row.overdue ? "до " : "") + root.shortDate(row.task.due)
            font.family: root.font; font.pixelSize: 11; color: row.overdue ? root.warn : root.pencil
        }

        QQC2.Menu {
            id: rowMenu
            QQC2.MenuItem { text: "Изменить"; enabled: !row.done; onTriggered: root.editingId = row.task.id }
            QQC2.MenuItem {
                text: row.done ? "Вернуть в список" : "Сделано"
                onTriggered: root.setStatus(row.task, row.done ? "open" : "done")
            }
            QQC2.MenuItem {
                text: "На сегодня"; visible: !row.done && (row.task.due || root.today) !== root.today
                height: visible ? implicitHeight : 0
                onTriggered: root.edit(row.task, {due: root.today})
            }
            QQC2.MenuItem {
                text: "На завтра"; enabled: !row.done; onTriggered: root.edit(row.task, {due: root.isoDay(1)})
            }
            QQC2.MenuItem {
                text: "Через неделю"; enabled: !row.done; onTriggered: root.edit(row.task, {due: root.isoDay(7)})
            }
            QQC2.MenuSeparator {}
            QQC2.MenuItem { text: "Удалить"; onTriggered: root.setStatus(row.task, "dropped") }
        }
    }
}
