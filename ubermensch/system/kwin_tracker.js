// Скрипт KWin: сообщает ядру Ани об активном окне и держит лист задач у рабочего стола.
// На Wayland программы не видят чужих окон — их видит только композитор, поэтому
// скрипт живёт внутри KWin и звонит ядру по D-Bus. Загружает его ядро (system/session.py).

const SERVICE = "org.ubermensch.Anya";
const PATH = "/Session";
const IFACE = "org.ubermensch.Session";
const SHEET_TITLE = "anya.exe — задачи";

let watched = null;

function describe(w) {
    if (!w) return "{}";
    return JSON.stringify({
        app: String(w.resourceClass || w.resourceName || "?"),
        title: String(w.caption || ""),
        pid: w.pid,
        id: String(w.internalId),
        fullscreen: Boolean(w.fullScreen),
        desktop: Boolean(w.desktopWindow),
    });
}

function report() {
    callDBus(SERVICE, PATH, IFACE, "Window", describe(workspace.activeWindow));
}

// Лист живёт под окнами программ, а когда пользователь смотрит на рабочий стол
// (клик по обоям) или работает с самим листом — поднимается над ними.
function layerSheet(active) {
    const up = Boolean(active) && (active.desktopWindow || active.caption === SHEET_TITLE);
    for (const w of workspace.windowList()) {
        if (w.caption === SHEET_TITLE) {
            w.keepBelow = !up;
            if (up) workspace.raiseWindow(w);
        }
    }
}

function activated(w) {
    if (watched) {
        watched.captionChanged.disconnect(report);
        watched.fullScreenChanged.disconnect(report);
    }
    watched = w;
    if (w) {
        w.captionChanged.connect(report);  // вкладки браузера, открытые файлы
        w.fullScreenChanged.connect(report);
    }
    layerSheet(w);
    report();
}

workspace.windowActivated.connect(activated);
workspace.windowAdded.connect(function () { layerSheet(workspace.activeWindow); });
activated(workspace.activeWindow);
