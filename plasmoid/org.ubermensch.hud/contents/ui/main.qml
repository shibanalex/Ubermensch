import QtQuick
import QtQuick.Layouts
import QtQuick.Controls as QQC2
import org.kde.plasma.plasmoid
import org.kde.plasma.core as PlasmaCore
import org.kde.plasma.plasma5support as P5Support
import org.kde.kirigami as Kirigami

PlasmoidItem {
    id: root

    readonly property string api: "http://127.0.0.1:8765"
    readonly property string tokenFile: "/home/allegro/Projects/Ubermensch/secrets/api_token"
    readonly property string projectDir: "/home/allegro/Projects/Übermensch_Project"

    // Цвета и шрифты напрямую из темы KDE Plasma (Deep Nordic)
    readonly property color surface: Qt.rgba(1, 1, 1, 0.06)
    readonly property color surfaceHover: Qt.rgba(1, 1, 1, 0.12)
    readonly property color hairline: Qt.rgba(1, 1, 1, 0.12)
    readonly property color ink: Kirigami.Theme.textColor
    readonly property color pencil: Kirigami.Theme.disabledTextColor
    readonly property color accent: Kirigami.Theme.highlightColor
    readonly property color accentLight: Qt.lighter(Kirigami.Theme.highlightColor, 1.2)
    readonly property color fgOnAccent: Kirigami.Theme.highlightedTextColor
    readonly property color warn: Kirigami.Theme.negativeTextColor
    readonly property string uiFont: Kirigami.Theme.defaultFont.family

    readonly property var months: ["января", "февраля", "марта", "апреля", "мая", "июня", "июля",
                                   "августа", "сентября", "октября", "ноября", "декабря"]
    readonly property var weekdays: ["Вс", "Пн", "Вт", "Ср", "Чт", "Пт", "Сб"]

    property string token: ""
    property bool online: false
    property string today: Qt.formatDate(new Date(), "yyyy-MM-dd")
    property var tasks: []
    property int editingId: -1
    property string editTitleText: ""
    property string editDueText: ""

    property int cpuPct: 8
    property string ramText: "—"
    property int hhTotal: 130
    property int tgTotal: 12
    property int interviewsCount: 4
    property string statusToast: ""

    readonly property var openTasks: tasks.filter(t => t.status === "open").sort((a, b) => {
        const da = a.due || today
        const db = b.due || today
        return da < db ? -1 : (da > db ? 1 : a.id - b.id)
    })
    readonly property var doneTasks: tasks.filter(t => t.status === "done")

    preferredRepresentation: fullRepresentation
    // Стандартная нативная рамка KDE Plasma (точно как у соседнего виджета allegro)
    Plasmoid.backgroundHints: PlasmaCore.Types.DefaultBackground
    Plasmoid.icon: "view-task"
    toolTipMainText: "Аня · Задачи и пульт"
    toolTipSubText: online ? `Открыто задач: ${openTasks.length}` : "Подключение к ядру…"

    P5Support.DataSource {
        id: shell
        engine: "executable"
        connectedSources: []
        property var callbacks: ({})
        onNewData: (source, data) => {
            const cb = callbacks[source]
            delete callbacks[source]
            disconnectSource(source)
            if (cb) cb(data["stdout"] || "", data["exit code"])
        }
        function run(cmd, cb) {
            callbacks[cmd] = cb
            connectSource(cmd)
        }
    }

    function showToast(msg) {
        statusToast = msg
        toastTimer.restart()
    }

    Timer {
        id: toastTimer
        interval: 2500
        onTriggered: root.statusToast = ""
    }

    function quote(text) {
        return "'" + String(text).replace(/'/g, "'\\''") + "'"
    }

    function loadToken(then) {
        shell.run("cat " + quote(tokenFile), (out) => {
            token = out.trim()
            if (then) then()
        })
    }

    function request(method, path, payload, done) {
        if (!token) {
            loadToken(() => { if (token) request(method, path, payload, done) })
            return
        }
        const xhr = new XMLHttpRequest()
        xhr.open(method, api + path)
        xhr.setRequestHeader("Authorization", "Bearer " + token)
        xhr.setRequestHeader("Content-Type", "application/json")
        xhr.onreadystatechange = () => {
            if (xhr.readyState !== XMLHttpRequest.DONE) return
            online = xhr.status === 200
            if (xhr.status === 401) token = ""
            if (online && done) done(JSON.parse(xhr.responseText || "{}"))
        }
        xhr.send(payload ? JSON.stringify(payload) : null)
    }

    function refreshTasks() {
        request("GET", "/api/tasks", null, (data) => {
            if (editingId >= 0) return
            today = data.today || Qt.formatDate(new Date(), "yyyy-MM-dd")
            tasks = data.tasks || []
        })
    }

    function pollStats() {
        const cmd = "python3 -c 'import json, sqlite3; " +
            "mem = {l.split(\":\")[0]: int(l.split()[1]) for l in open(\"/proc/meminfo\") if \":\" in l}; " +
            "used_g = (mem.get(\"MemTotal\", 1) - mem.get(\"MemAvailable\", 0)) / 1048576; " +
            "load = float(open(\"/proc/loadavg\").read().split()[0]); " +
            "cpu = min(99, max(2, int(load * 12.5))); " +
            "conn = sqlite3.connect(\"/home/allegro/Projects/Ubermensch/db/ubermensch.db\"); " +
            "hh = conn.execute(\"SELECT COUNT(*) FROM career_opportunities WHERE source=\\\"hh\\\"\").fetchone()[0]; " +
            "tg = conn.execute(\"SELECT COUNT(*) FROM career_opportunities WHERE source=\\\"tg\\\"\").fetchone()[0]; " +
            "iv = conn.execute(\"SELECT COUNT(DISTINCT opportunity_id) FROM career_events WHERE stage=\\\"interview\\\"\").fetchone()[0]; " +
            "print(json.dumps({\"cpu\": cpu, \"ram\": f\"{used_g:.1f} ГБ\", \"hh\": hh, \"tg\": tg, \"iv\": iv}))'"
        shell.run(cmd, (out) => {
            try {
                const d = JSON.parse(out.trim())
                root.cpuPct = d.cpu
                root.ramText = d.ram
                root.hhTotal = d.hh
                root.tgTotal = d.tg
                root.interviewsCount = d.iv
            } catch (e) {}
        })
    }

    function isoDay(offset) {
        const d = new Date()
        d.setDate(d.getDate() + offset)
        return Qt.formatDate(d, "yyyy-MM-dd")
    }

    function shortDate(iso) {
        if (!iso) return "сегодня"
        if (iso === root.today) return "сегодня"
        if (iso === isoDay(1)) return "завтра"
        return `${iso.slice(8, 10)}.${iso.slice(5, 7)}`
    }

    function parseDateInput(raw) {
        const s = String(raw || "").trim().toLowerCase()
        if (!s || s === "сегодня" || s === "сег") return root.today
        if (s === "завтра" || s === "зав") return isoDay(1)
        if (s === "+3" || s === "+3д") return isoDay(3)
        if (s === "+7" || s === "+7д") return isoDay(7)
        const isoMatch = s.match(/^(\d{4})-(\d{2})-(\d{2})$/)
        if (isoMatch) return s
        const dmMatch = s.match(/^(\d{1,2})[.,\/-](\d{1,2})(?:[.,\/-](\d{2,4}))?$/)
        if (dmMatch) {
            const day = String(parseInt(dmMatch[1], 10)).padStart(2, "0")
            const mon = String(parseInt(dmMatch[2], 10)).padStart(2, "0")
            let yr = new Date().getFullYear()
            if (dmMatch[3]) {
                yr = dmMatch[3].length === 2 ? 2000 + parseInt(dmMatch[3], 10) : parseInt(dmMatch[3], 10)
            }
            return `${yr}-${mon}-${day}`
        }
        return root.today
    }

    function addTask(title, dueRaw) {
        const clean = String(title || "").trim()
        if (!clean) return
        const dueIso = parseDateInput(dueRaw)
        request("POST", "/api/tasks", {title: clean, due: dueIso}, () => {
            refreshTasks()
            showToast("Задача добавлена")
        })
    }

    function setTaskStatus(task, status) {
        request("POST", `/api/tasks/${task.id}`, {status: status}, () => {
            refreshTasks()
            if (status === "dropped") showToast("Задача удалена")
        })
    }

    function startEdit(task) {
        editingId = task.id
        editTitleText = task.title
        editDueText = task.due ? `${task.due.slice(8, 10)}.${task.due.slice(5, 7)}` : ""
    }

    function saveEdit(task) {
        const cleanTitle = String(editTitleText || "").trim()
        if (!cleanTitle) {
            editingId = -1
            return
        }
        const dueIso = parseDateInput(editDueText)
        editingId = -1
        request("POST", `/api/tasks/${task.id}/edit`, {title: cleanTitle, due: dueIso}, () => {
            refreshTasks()
            showToast("Сохранено")
        })
    }

    function updateTaskDue(task, dueIso) {
        request("POST", `/api/tasks/${task.id}/edit`, {due: dueIso}, () => {
            refreshTasks()
            showToast("Дата: " + shortDate(dueIso))
        })
    }

    function runAnyaLink(subcmd, label) {
        showToast(label)
        const cmd = "setsid -f bash -c 'cd \"/home/allegro/Projects/Übermensch_Project\" && " +
                    "(.venv/bin/python -m ubermensch.desktop.link " + subcmd + " || " +
                    "(systemctl --user start ubermensch-desktop.service && sleep 1.2 && " +
                    ".venv/bin/python -m ubermensch.desktop.link " + subcmd + "))' >/dev/null 2>&1"
        shell.run(cmd, () => {})
    }

    function launchDetached(cmdline, label) {
        showToast(label)
        shell.run("setsid -f " + cmdline + " >/dev/null 2>&1", () => {})
    }

    Timer {
        interval: 4000
        running: true
        repeat: true
        triggeredOnStart: true
        onTriggered: refreshTasks()
    }

    Timer {
        interval: 10000
        running: true
        repeat: true
        triggeredOnStart: true
        onTriggered: pollStats()
    }

    fullRepresentation: Item {
        Layout.minimumWidth: 320
        Layout.minimumHeight: 340
        Layout.preferredWidth: 384
        Layout.preferredHeight: 464

        ColumnLayout {
            anchors.fill: parent
            anchors.margins: 10
            spacing: 8

            // --- Шапка: Дата и быстрые кнопки Ани ---
            RowLayout {
                Layout.fillWidth: true
                spacing: 6

                ColumnLayout {
                    Layout.fillWidth: true
                    spacing: 1
                    Text {
                        text: {
                            const d = new Date(root.today + "T12:00:00")
                            return `${root.weekdays[d.getDay()]}, ${d.getDate()} ${root.months[d.getMonth()]}`
                        }
                        font.family: root.uiFont
                        font.pixelSize: 16
                        font.weight: Font.DemiBold
                        color: root.ink
                    }
                    Text {
                        text: root.statusToast !== ""
                              ? root.statusToast
                              : `HH ${root.hhTotal} · TG ${root.tgTotal} · Собес ${root.interviewsCount} · RAM ${root.ramText}`
                        font.family: root.uiFont
                        font.pixelSize: 11
                        color: root.statusToast !== "" ? root.accent : root.pencil
                    }
                }

                TopPillBtn {
                    label: "Спросить"
                    tip: "Открыть окно диалога с Аней"
                    onClicked: root.runAnyaLink("prompt", "Открываю диалог с Аней…")
                }
                TopPillBtn {
                    label: "План"
                    tip: "Попросить Аню спланировать день"
                    onClicked: root.runAnyaLink("plan", "Аня составляет план дня…")
                }
                TopPillBtn {
                    label: "Консоль"
                    tip: "Открыть историю ответов Ани"
                    onClicked: root.runAnyaLink("history", "Открываю консоль Ани…")
                }
            }

            Rectangle {
                Layout.fillWidth: true
                implicitHeight: 1
                color: root.hairline
            }

            // --- Форма добавления новой задачи с датой ---
            RowLayout {
                Layout.fillWidth: true
                spacing: 6

                Rectangle {
                    Layout.fillWidth: true
                    implicitHeight: 30
                    radius: 5
                    color: root.surface
                    border.width: newTitleField.activeFocus ? 1 : 0
                    border.color: root.accent

                    QQC2.TextField {
                        id: newTitleField
                        anchors.fill: parent
                        anchors.leftMargin: 8
                        anchors.rightMargin: 8
                        placeholderText: "+ Новая задача…"
                        font.family: root.uiFont
                        font.pixelSize: 12
                        color: root.ink
                        placeholderTextColor: root.pencil
                        background: Item {}
                        onAccepted: {
                            root.addTask(newTitleField.text, newDueField.text)
                            newTitleField.text = ""
                            newDueField.text = ""
                        }
                    }
                }

                Rectangle {
                    Layout.preferredWidth: 68
                    implicitHeight: 30
                    radius: 5
                    color: root.surface
                    border.width: newDueField.activeFocus ? 1 : 0
                    border.color: root.accent

                    QQC2.TextField {
                        id: newDueField
                        anchors.fill: parent
                        anchors.leftMargin: 6
                        anchors.rightMargin: 6
                        placeholderText: "ДД.ММ"
                        horizontalAlignment: Text.AlignHCenter
                        font.family: root.uiFont
                        font.pixelSize: 11
                        color: root.ink
                        placeholderTextColor: root.pencil
                        background: Item {}
                        onAccepted: {
                            root.addTask(newTitleField.text, newDueField.text)
                            newTitleField.text = ""
                            newDueField.text = ""
                        }
                    }
                    QQC2.ToolTip.visible: newDueField.hovered
                    QQC2.ToolTip.text: "Дата: ДД.ММ (напр. 05.11), «завтра» или пусто (сегодня)"
                }

                Rectangle {
                    Layout.preferredWidth: 30
                    implicitHeight: 30
                    radius: 5
                    color: addBtnArea.containsMouse ? root.accent : root.surface
                    border.width: 1
                    border.color: root.accent

                    Text {
                        anchors.centerIn: parent
                        text: "+"
                        font.family: root.uiFont
                        font.pixelSize: 16
                        font.weight: Font.Bold
                        color: addBtnArea.containsMouse ? root.fgOnAccent : root.accent
                    }
                    MouseArea {
                        id: addBtnArea
                        anchors.fill: parent
                        hoverEnabled: true
                        cursorShape: Qt.PointingHandCursor
                        onClicked: {
                            root.addTask(newTitleField.text, newDueField.text)
                            newTitleField.text = ""
                            newDueField.text = ""
                        }
                    }
                    QQC2.ToolTip.visible: addBtnArea.containsMouse
                    QQC2.ToolTip.text: "Добавить задачу"
                }
            }

            // --- Список задач ---
            QQC2.ScrollView {
                Layout.fillWidth: true
                Layout.fillHeight: true
                QQC2.ScrollBar.horizontal.policy: QQC2.ScrollBar.AlwaysOff
                contentWidth: availableWidth

                ColumnLayout {
                    width: parent.width
                    spacing: 4

                    Text {
                        visible: root.openTasks.length === 0
                        text: "Список дел пуст — можно передохнуть"
                        font.family: root.uiFont
                        font.pixelSize: 12
                        font.italic: true
                        color: root.pencil
                        topPadding: 6
                    }

                    Repeater {
                        model: root.openTasks
                        delegate: TaskItemRow { task: modelData }
                    }

                    Text {
                        visible: root.doneTasks.length > 0
                        text: "СДЕЛАНО СЕГОДНЯ"
                        font.family: root.uiFont
                        font.pixelSize: 10
                        font.weight: Font.Bold
                        font.letterSpacing: 1.2
                        color: root.pencil
                        topPadding: 8
                    }

                    Repeater {
                        model: root.doneTasks
                        delegate: TaskItemRow { task: modelData }
                    }
                }
            }

            Rectangle {
                Layout.fillWidth: true
                implicitHeight: 1
                color: root.hairline
            }

            // --- Нижняя панель быстрого запуска ---
            RowLayout {
                Layout.fillWidth: true
                spacing: 6

                BottomActionBtn {
                    label: "Метрики"
                    onClicked: root.runAnyaLink("metrics", "Открываю метрики…")
                }
                BottomActionBtn {
                    label: "MuseScore"
                    onClicked: root.launchDetached("/home/allegro/Загрузки/MuseScore-Studio-4.7.6.261022093-x86_64.AppImage", "Запускаю MuseScore…")
                }
                BottomActionBtn {
                    label: "Проводник"
                    onClicked: root.launchDetached("dolphin /home/allegro", "Открываю Dolphin…")
                }
            }
        }
    }

    // --- Строка задачи с редактированием текста, даты и удалением ---
    component TaskItemRow: Rectangle {
        id: rowCard
        property var task
        readonly property bool isDone: task.status === "done"
        readonly property bool isEditing: root.editingId === task.id
        readonly property bool isOverdue: !isDone && task.due && task.due < root.today

        Layout.fillWidth: true
        implicitHeight: rowCol.implicitHeight + 10
        radius: 5
        color: isEditing ? root.surfaceHover : (rowHover.hovered ? root.surface : "transparent")

        HoverHandler { id: rowHover }

        ColumnLayout {
            id: rowCol
            anchors.fill: parent
            anchors.leftMargin: 6
            anchors.rightMargin: 6
            anchors.topMargin: 5
            anchors.bottomMargin: 5
            spacing: 4

            // Обычный режим просмотра строки
            RowLayout {
                visible: !rowCard.isEditing
                Layout.fillWidth: true
                spacing: 7

                // Чекбокс выполнения
                Rectangle {
                    Layout.alignment: Qt.AlignVCenter
                    width: 15
                    height: 15
                    radius: 3
                    color: rowCard.isDone ? root.accent : "transparent"
                    border.width: 1
                    border.color: chkArea.containsMouse ? root.accentLight : root.accent

                    Text {
                        visible: rowCard.isDone
                        anchors.centerIn: parent
                        text: "✓"
                        font.pixelSize: 10
                        font.bold: true
                        color: root.fgOnAccent
                    }
                    MouseArea {
                        id: chkArea
                        anchors.fill: parent
                        anchors.margins: -4
                        hoverEnabled: true
                        cursorShape: Qt.PointingHandCursor
                        onClicked: root.setTaskStatus(rowCard.task, rowCard.isDone ? "open" : "done")
                    }
                }

                // Текст задачи (двойной клик — редактировать)
                Text {
                    Layout.fillWidth: true
                    text: rowCard.task.title
                    wrapMode: Text.Wrap
                    font.family: root.uiFont
                    font.pixelSize: 13
                    font.strikeout: rowCard.isDone
                    color: rowCard.isDone ? root.pencil : root.ink

                    MouseArea {
                        anchors.fill: parent
                        cursorShape: Qt.PointingHandCursor
                        onDoubleClicked: if (!rowCard.isDone) root.startEdit(rowCard.task)
                    }
                }

                // Плашка даты (клик — быстрое меню смены даты или ввод своей даты)
                Rectangle {
                    visible: !rowCard.isDone
                    Layout.alignment: Qt.AlignVCenter
                    implicitWidth: dueLabel.implicitWidth + 10
                    implicitHeight: 20
                    radius: 4
                    color: dueArea.containsMouse ? root.surfaceHover : root.surface
                    border.width: 1
                    border.color: rowCard.isOverdue ? root.warn : root.hairline

                    Text {
                        id: dueLabel
                        anchors.centerIn: parent
                        text: root.shortDate(rowCard.task.due)
                        font.family: root.uiFont
                        font.pixelSize: 11
                        color: rowCard.isOverdue ? root.warn : (rowCard.task.due === root.today ? root.accent : root.pencil)
                    }
                    MouseArea {
                        id: dueArea
                        anchors.fill: parent
                        hoverEnabled: true
                        cursorShape: Qt.PointingHandCursor
                        onClicked: dateMenu.popup()
                    }
                    QQC2.ToolTip.visible: dueArea.containsMouse
                    QQC2.ToolTip.text: "Изменить дату задачи"

                    QQC2.Menu {
                        id: dateMenu
                        QQC2.MenuItem {
                            text: "Сегодня (" + root.shortDate(root.today) + ")"
                            onTriggered: root.updateTaskDue(rowCard.task, root.today)
                        }
                        QQC2.MenuItem {
                            text: "Завтра (" + root.shortDate(root.isoDay(1)) + ")"
                            onTriggered: root.updateTaskDue(rowCard.task, root.isoDay(1))
                        }
                        QQC2.MenuItem {
                            text: "Через 3 дня (" + root.shortDate(root.isoDay(3)) + ")"
                            onTriggered: root.updateTaskDue(rowCard.task, root.isoDay(3))
                        }
                        QQC2.MenuItem {
                            text: "Через неделю (" + root.shortDate(root.isoDay(7)) + ")"
                            onTriggered: root.updateTaskDue(rowCard.task, root.isoDay(7))
                        }
                        QQC2.MenuSeparator {}
                        QQC2.MenuItem {
                            text: "Ввести дату (ДД.ММ)…"
                            onTriggered: root.startEdit(rowCard.task)
                        }
                    }
                }

                // Кнопка редактирования ✎
                Rectangle {
                    visible: !rowCard.isDone
                    Layout.alignment: Qt.AlignVCenter
                    width: 20
                    height: 20
                    radius: 4
                    color: editBtnArea.containsMouse ? root.surfaceHover : "transparent"

                    Text {
                        anchors.centerIn: parent
                        text: "✎"
                        font.pixelSize: 12
                        color: editBtnArea.containsMouse ? root.accent : root.pencil
                    }
                    MouseArea {
                        id: editBtnArea
                        anchors.fill: parent
                        hoverEnabled: true
                        cursorShape: Qt.PointingHandCursor
                        onClicked: root.startEdit(rowCard.task)
                    }
                    QQC2.ToolTip.visible: editBtnArea.containsMouse
                    QQC2.ToolTip.text: "Исправить текст или дату"
                }

                // Кнопка удаления ✕
                Rectangle {
                    Layout.alignment: Qt.AlignVCenter
                    width: 20
                    height: 20
                    radius: 4
                    color: delBtnArea.containsMouse ? Qt.rgba(root.warn.r, root.warn.g, root.warn.b, 0.2) : "transparent"

                    Text {
                        anchors.centerIn: parent
                        text: "✕"
                        font.pixelSize: 11
                        color: delBtnArea.containsMouse ? root.warn : root.pencil
                    }
                    MouseArea {
                        id: delBtnArea
                        anchors.fill: parent
                        hoverEnabled: true
                        cursorShape: Qt.PointingHandCursor
                        onClicked: root.setTaskStatus(rowCard.task, "dropped")
                    }
                    QQC2.ToolTip.visible: delBtnArea.containsMouse
                    QQC2.ToolTip.text: "Удалить задачу"
                }
            }

            // Режим редактирования задачи (текст + дата ДД.ММ + сохранить/отмена)
            RowLayout {
                visible: rowCard.isEditing
                Layout.fillWidth: true
                spacing: 5

                Rectangle {
                    Layout.fillWidth: true
                    implicitHeight: 26
                    radius: 4
                    color: root.surface
                    border.width: 1
                    border.color: root.accent

                    QQC2.TextField {
                        id: editTitleInput
                        anchors.fill: parent
                        anchors.leftMargin: 6
                        anchors.rightMargin: 6
                        text: root.editTitleText
                        font.family: root.uiFont
                        font.pixelSize: 12
                        color: root.ink
                        background: Item {}
                        onTextChanged: root.editTitleText = text
                        onVisibleChanged: if (visible) { forceActiveFocus(); selectAll() }
                        onAccepted: root.saveEdit(rowCard.task)
                        Keys.onEscapePressed: root.editingId = -1
                    }
                }

                Rectangle {
                    Layout.preferredWidth: 62
                    implicitHeight: 26
                    radius: 4
                    color: root.surface
                    border.width: 1
                    border.color: root.accent

                    QQC2.TextField {
                        id: editDueInput
                        anchors.fill: parent
                        anchors.leftMargin: 4
                        anchors.rightMargin: 4
                        text: root.editDueText
                        placeholderText: "ДД.ММ"
                        horizontalAlignment: Text.AlignHCenter
                        font.family: root.uiFont
                        font.pixelSize: 11
                        color: root.ink
                        placeholderTextColor: root.pencil
                        background: Item {}
                        onTextChanged: root.editDueText = text
                        onAccepted: root.saveEdit(rowCard.task)
                        Keys.onEscapePressed: root.editingId = -1
                    }
                }

                // Сохранить ✓
                Rectangle {
                    width: 24
                    height: 26
                    radius: 4
                    color: root.accent
                    Text {
                        anchors.centerIn: parent
                        text: "✓"
                        font.pixelSize: 12
                        font.bold: true
                        color: root.fgOnAccent
                    }
                    MouseArea {
                        anchors.fill: parent
                        cursorShape: Qt.PointingHandCursor
                        onClicked: root.saveEdit(rowCard.task)
                    }
                }

                // Отмена ✕
                Rectangle {
                    width: 24
                    height: 26
                    radius: 4
                    color: root.surface
                    Text {
                        anchors.centerIn: parent
                        text: "✕"
                        font.pixelSize: 11
                        color: root.pencil
                    }
                    MouseArea {
                        anchors.fill: parent
                        cursorShape: Qt.PointingHandCursor
                        onClicked: root.editingId = -1
                    }
                }
            }
        }
    }

    component TopPillBtn: Rectangle {
        property string label
        property string tip: ""
        signal clicked()
        implicitWidth: pillLbl.implicitWidth + 14
        implicitHeight: 24
        radius: 4
        color: pillArea.containsMouse ? Qt.rgba(root.accent.r, root.accent.g, root.accent.b, 0.22) : root.surface
        border.width: 1
        border.color: pillArea.containsMouse ? root.accent : root.hairline

        Text {
            id: pillLbl
            anchors.centerIn: parent
            text: parent.label
            font.family: root.uiFont
            font.pixelSize: 11
            font.weight: Font.Medium
            color: root.ink
        }
        MouseArea {
            id: pillArea
            anchors.fill: parent
            hoverEnabled: true
            cursorShape: Qt.PointingHandCursor
            onClicked: parent.clicked()
        }
        QQC2.ToolTip.visible: pillArea.containsMouse && parent.tip !== ""
        QQC2.ToolTip.text: parent.tip
    }

    component BottomActionBtn: Rectangle {
        property string label
        signal clicked()
        Layout.fillWidth: true
        implicitHeight: 26
        radius: 4
        color: botArea.containsMouse ? Qt.rgba(root.accent.r, root.accent.g, root.accent.b, 0.22) : root.surface
        border.width: 1
        border.color: botArea.containsMouse ? root.accent : root.hairline

        Text {
            anchors.centerIn: parent
            text: parent.label
            font.family: root.uiFont
            font.pixelSize: 11
            font.weight: Font.Medium
            color: root.ink
        }
        MouseArea {
            id: botArea
            anchors.fill: parent
            hoverEnabled: true
            cursorShape: Qt.PointingHandCursor
            onClicked: parent.clicked()
        }
    }
}
