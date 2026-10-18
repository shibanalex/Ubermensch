// Скрипт KWin: сообщает ядру Ани об активном окне.
// На Wayland программы не видят чужих окон — их видит только композитор, поэтому
// скрипт живёт внутри KWin и звонит ядру по D-Bus. Загружает его ядро (system/session.py).

const SERVICE = "org.ubermensch.Anya";
const PATH = "/Session";
const IFACE = "org.ubermensch.Session";

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
    report();
}

workspace.windowActivated.connect(activated);
activated(workspace.activeWindow);
