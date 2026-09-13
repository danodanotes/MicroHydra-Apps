"""ble_recv — receive file over BLE. MicroHydra Cardputer ADV.
Copy to /apps/ble_recv.py
Quit: ESC ` G0
Q: show Android app QR (GitHub page)
Block size: any. Phone may use 400, 500, 800...
Closes a block on the MORE marker or 2.8s idle. RAM may fail on huge blocks.
"""
import bluetooth
import gc
import machine
import os
import time
from micropython import const
from lib.display import Display
from lib.userinput import UserInput
from lib.hydra.config import Config

_IRQ_CONNECT = const(1)
_IRQ_DISCONNECT = const(2)
_IRQ_GATTS_WRITE = const(3)
_FLAG_READ = const(0x0002)
_FLAG_WRITE_NO_RESP = const(0x0004)
_FLAG_WRITE = const(0x0008)
_FLAG_NOTIFY = const(0x0010)
_NUS = bluetooth.UUID("6E400001-B5A3-F393-E0A9-E50E24DCCA9E")
_NUS_TX = (
    bluetooth.UUID("6E400003-B5A3-F393-E0A9-E50E24DCCA9E"),
    _FLAG_READ | _FLAG_NOTIFY,
)
_NUS_RX = (
    bluetooth.UUID("6E400002-B5A3-F393-E0A9-E50E24DCCA9E"),
    _FLAG_WRITE | _FLAG_WRITE_NO_RESP,
)
_NUS_SVC = (_NUS, (_NUS_TX, _NUS_RX))
BLE_NAME = "Cardputer-ADV"
APPS_DIR = "/apps"
IDLE_MS = 2800
RX_BUF = 2048
TMP = "/apps/.xfer.tmp"
_OK = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-"

# Protocol markers built in pieces so the contiguous text never appears
# in this source file (otherwise transferring this .py would self-cut).
_P = "##"
ID_BLOCKS = _P + "BLOCKS"
ID_TYPE = _P + "TYPE"
ID_FILE = _P + "FILE"
ID_ID = _P + "ID"
ID_CONT = _P + "CONTINUATION"
ID_MORE = _P + "MORE"
ID_END = _P + "END"
ID_FMT = _P + "FMT"
ID_NAME = _P + "NAME"

display = Display()
kb = UserInput()
cfg = Config()
W = 240
BG = cfg.palette[2]
FG = cfg.palette[8]
DIM = cfg.palette[6]
ACC = cfg.palette[4]


def clip(s, n=36):
    s = str(s)
    if len(s) <= n:
        return s
    return s[: n - 1] + "~"


def adv_payload(name):
    raw = name.encode()[:18]
    return bytes((2, 1, 6, len(raw) + 1, 9)) + raw


class BleUart:
    def __init__(self, name=BLE_NAME):
        self.ble = bluetooth.BLE()
        self.ble.active(True)
        try:
            self.ble.config(gap_name=name)
        except Exception:
            pass
        self.ble.irq(self._irq)
        handles = self.ble.gatts_register_services((_NUS_SVC,))
        self._tx, self._rx = handles[0]
        try:
            self.ble.gatts_set_buffer(self._rx, RX_BUF, True)
        except Exception:
            pass
        self.conn = None
        self.connected = False
        self.parts = []
        self.last_rx = 0
        self.receiving = False
        self._adv = adv_payload(name)
        self.advertise()

    def _irq(self, event, data):
        if event == _IRQ_CONNECT:
            self.conn = data[0]
            self.connected = True
            self.parts = []
            self.receiving = False
            self.last_rx = time.ticks_ms()
        elif event == _IRQ_DISCONNECT:
            self.conn = None
            self.connected = False
            self.advertise()
        elif event == _IRQ_GATTS_WRITE:
            try:
                chunk = self.ble.gatts_read(self._rx)
            except Exception:
                chunk = b""
            if chunk:
                self.parts.append(chunk)
                self.receiving = True
                self.last_rx = time.ticks_ms()

    def advertise(self):
        try:
            self.ble.gap_advertise(100000, adv_data=self._adv)
        except Exception:
            try:
                self.ble.gap_advertise(None)
                self.ble.gap_advertise(100000, adv_data=self._adv)
            except Exception:
                pass

    def take(self):
        if not self.parts:
            return b""
        data = b"".join(self.parts)
        self.parts = []
        return data

    def idle_done(self):
        if not self.receiving:
            return False
        return time.ticks_diff(time.ticks_ms(), self.last_rx) >= IDLE_MS

    def say(self, msg):
        if self.conn is None:
            return
        try:
            if isinstance(msg, str):
                msg = msg.encode()
            if not msg.endswith(b"\n"):
                msg += b"\n"
            i = 0
            while i < len(msg):
                self.ble.gatts_notify(self.conn, self._tx, msg[i : i + 20])
                i += 20
                time.sleep_ms(30)
        except Exception:
            pass

    def stop(self):
        try:
            self.ble.active(False)
        except Exception:
            pass


def next_name(ext="txt"):
    try:
        os.mkdir(APPS_DIR)
    except OSError:
        pass
    try:
        names = os.listdir(APPS_DIR)
    except OSError:
        names = []
    n = 1
    while n < 100:
        name = "recv_%02d.%s" % (n, ext)
        if name not in names:
            return name
        n += 1
    return "recv_xx." + ext


def safe_name(name, ext="txt"):
    name = str(name).strip().replace("\\", "/").split("/")[-1]
    out = []
    for ch in name:
        if ch in _OK:
            out.append(ch)
    name = "".join(out)
    if not name:
        return next_name(ext)
    if "." not in name:
        name = name + "." + ext
    return name[:48]


def unique_path(fname, folder):
    try:
        os.mkdir(folder)
    except OSError:
        pass
    try:
        names = os.listdir(folder)
    except OSError:
        names = []
    if fname not in names:
        return folder + "/" + fname, fname
    if "." in fname:
        base, ext = fname.rsplit(".", 1)
    else:
        base, ext = fname, "txt"
    i = 2
    while True:
        cand = "%s_%d.%s" % (base, i, ext)
        if cand not in names:
            return folder + "/" + cand, cand
        i += 1


def parse_head(text):
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    ftype = "txt"
    fname = ""
    total = 0
    bid = 0
    more = False
    rest = []
    for line in text.split("\n"):
        s = line.strip()
        if s.startswith(ID_BLOCKS):
            try:
                total = int(s[len(ID_BLOCKS) :].strip())
            except Exception:
                total = 0
        elif s.startswith(ID_TYPE) or s.startswith(ID_FMT):
            p = s.split(None, 1)
            if len(p) > 1:
                ftype = p[1].strip().lower()
        elif s.startswith(ID_FILE) or s.startswith(ID_NAME):
            p = s.split(None, 1)
            if len(p) > 1:
                fname = p[1].strip()
        elif s.startswith(ID_ID):
            try:
                bid = int(s[len(ID_ID) :].strip())
            except Exception:
                bid = 0
        elif s == ID_MORE:
            more = True
        elif s.startswith(ID_CONT) or s == ID_END or s.startswith(_P):
            pass
        else:
            rest.append(line)
    while rest and rest[-1] == "":
        rest.pop()
    body = "\n".join(rest)
    if body and not body.endswith("\n"):
        body += "\n"
    if not fname:
        fname = "recv." + ftype
    return more, total, bid, fname, ftype, body


def tmp_reset():
    try:
        os.remove(TMP)
    except OSError:
        pass


def tmp_add(text):
    if not text:
        return
    f = open(TMP, "a")
    f.write(text)
    f.close()


def tmp_size():
    try:
        return os.stat(TMP)[6]
    except OSError:
        return 0


def place_tmp(fname):
    last = None
    for folder in (APPS_DIR, "/sd/apps"):
        try:
            path, fn = unique_path(fname, folder)
            try:
                os.rename(TMP, path)
                return path, fn, os.stat(path)[6]
            except OSError:
                src = open(TMP, "rb")
                dst = open(path, "wb")
                try:
                    while True:
                        buf = src.read(128)
                        if not buf:
                            break
                        dst.write(buf)
                finally:
                    src.close()
                    dst.close()
                tmp_reset()
                return path, fn, os.stat(path)[6]
        except Exception as e:
            last = e
    raise last


def ask_filename(suggested, ext):
    name = suggested
    if "." in name:
        name = name.rsplit(".", 1)[0]
    blink = True
    t0 = time.ticks_ms()
    while True:
        now = time.ticks_ms()
        if time.ticks_diff(now, t0) > 400:
            blink = not blink
            t0 = now
        display.fill(BG)
        display.rect(0, 0, W, 18, ACC, fill=True)
        display.text("FILE NAME", 6, 3, BG)
        display.text(clip(name + ("|" if blink else " "), 28), 8, 50, FG)
        display.text("ENT save   ESC auto", 6, 100, DIM)
        display.show()
        keys = kb.get_new_keys()
        for k in keys:
            if k in ("ENT", "\n"):
                return safe_name(name, ext) if name.strip() else next_name(ext)
            if k == "ESC":
                return safe_name(suggested, ext)
            if k in ("`", "G0"):
                if uart:
                    uart.stop()
                machine.reset()
            if k in ("BSPC", "DEL"):
                name = name[:-1]
            elif k == "SPC":
                name += "_"
            elif len(k) == 1:
                name += k
        time.sleep_ms(20)


status = "starting BT..."
detail = "wait"
saved = "-"
nbytes = 0
nblk = 0
expect = 0
wait_more = False
started = False
hinted_name = "recv.txt"
hinted_ext = "txt"
uart = None
acc = bytearray()


def ping(msg):
    if uart:
        uart.say(msg)


def fail(msg):
    global status, detail, wait_more, started, acc
    ping("ERR " + msg)
    status = "error"
    detail = clip(msg)
    wait_more = False
    started = False
    acc = bytearray()
    gc.collect()



# QR popup -> GitHub app page (press Q)
_QR_N = 37
_QR_BPR = 5
_QR_MOD = 2  # pixel size per module; 37*2=74 fits 240x135
_QR = bytes((
    0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 63, 151, 190, 79, 224, 32,
    155, 242, 136, 32, 46, 177, 121, 139, 160, 46, 178, 227, 171, 160, 46, 168,
    89, 75, 160, 32, 163, 114, 40, 32, 63, 170, 170, 175, 224, 0, 62, 201,
    96, 0, 47, 139, 80, 15, 128, 38, 112, 46, 77, 160, 55, 194, 73, 42,
    192, 36, 24, 227, 59, 128, 43, 208, 72, 215, 96, 55, 4, 242, 217, 224,
    18, 200, 20, 155, 192, 6, 38, 167, 125, 128, 54, 189, 52, 182, 160, 37,
    74, 62, 77, 160, 30, 197, 243, 38, 192, 18, 109, 63, 159, 160, 55, 242,
    164, 183, 0, 33, 60, 73, 64, 160, 33, 134, 226, 18, 64, 41, 50, 73,
    187, 160, 40, 216, 208, 254, 0, 0, 46, 45, 98, 128, 63, 155, 75, 234,
    192, 32, 179, 163, 99, 224, 46, 182, 140, 191, 96, 46, 172, 230, 243, 96,
    46, 181, 136, 140, 128, 32, 132, 37, 155, 128, 63, 160, 16, 244, 64, 0,
    0, 0, 0, 0, 0, 0, 0, 0, 0,
))


def show_app_qr():
    """Fullscreen popup: QR to github.com/danodanotes/BleBlocksMicroHydra."""
    side = _QR_N * _QR_MOD
    ox = (W - side) // 2
    oy = 18
    display.fill(BG)
    display.rect(0, 0, W, 16, ACC, fill=True)
    display.text("ANDROID APP", 6, 2, BG)
    display.text("Q/ESC", 190, 2, BG)
    # white panel behind QR for contrast
    display.rect(ox - 2, oy - 2, side + 4, side + 4, FG, fill=True)
    y = oy
    for r in range(_QR_N):
        base = r * _QR_BPR
        x = ox
        for c in range(_QR_N):
            bi = base + (c >> 3)
            bit = 7 - (c & 7)
            if _QR[bi] & (1 << bit):
                display.rect(x, y, _QR_MOD, _QR_MOD, BG, fill=True)
            x += _QR_MOD
        y += _QR_MOD
    display.text("github.com/danodanotes", 6, 118, DIM)
    display.text("BleBlocksMicroHydra", 6, 128, ACC)
    display.show()
    while True:
        for k in kb.get_new_keys():
            if k in ("ESC", "`", "G0", "Q", "q", "ENT", "\n", "SPC", "BSPC"):
                return
        time.sleep_ms(40)


def draw():
    display.fill(BG)
    display.rect(0, 0, W, 18, ACC, fill=True)
    display.text("BLE RECV", 6, 3, BG)
    display.text(clip(status, 28), 6, 24, FG)
    blk = str(nblk)
    if expect:
        blk = blk + "/" + str(expect)
    display.text("blk " + blk + "  " + str(nbytes) + " b", 6, 42, FG)
    display.text(clip(saved, 28), 6, 60, ACC)
    display.text(clip(detail, 36), 6, 80, DIM)
    display.text("ESC quit   Q app QR", 6, 118, DIM)
    display.show()


def finish_file():
    global status, detail, saved, nbytes, wait_more, started
    wait_more = False
    started = False
    ping("GOT %d" % tmp_size())
    status = "name?"
    detail = clip(hinted_name)
    draw()
    fname = ask_filename(hinted_name, hinted_ext)
    ping("NAME " + str(fname))
    path, fname, nbytes = place_tmp(fname)
    gc.collect()
    saved = fname
    status = "saved"
    detail = path
    ping("OK " + path)


def handle_chunk(text):
    global nblk, expect, wait_more, started, hinted_name, hinted_ext, nbytes, status, detail
    more, total, bid, hinted, ftype, piece = parse_head(text)
    if total:
        expect = total
    if hinted:
        hinted_name = hinted
        hinted_ext = ftype
    if bid <= 1 and not started:
        tmp_reset()
        started = True
        nbytes = 0
    if bid:
        nblk = bid
    else:
        nblk = nblk + 1
        if nblk < 1:
            nblk = 1
        started = True
    tmp_add(piece)
    nbytes = tmp_size()
    gc.collect()
    ping("BLK %d/%d" % (nblk, expect if expect else nblk))
    if more:
        wait_more = True
        status = "waiting next block"
        detail = "got %d / %d" % (nblk, expect if expect else nblk)
        ping("MORE")
        return "more"
    wait_more = False
    return "end"


try:
    uart = BleUart()
    status = "visible: " + BLE_NAME
    detail = "connect phone"
except Exception as e:
    uart = None
    status = "BT error"
    detail = clip(e)

draw()
last_ui = time.ticks_ms()

while True:
    try:
        keys = kb.get_new_keys()
        force = False
        for k in keys:
            if k in ("ESC", "`", "G0"):
                if uart:
                    uart.stop()
                machine.reset()
            elif k in ("Q", "q"):
                show_app_qr()
                draw()
            elif k in ("ENT", "\n") and (acc or started):
                force = True
        if uart:
            piece = uart.take()
            if piece:
                acc.extend(piece)
                status = "receiving..."
            cut = -1
            mark_len = 0
            try:
                m = ID_MORE.encode()
                cut = acc.find(m)
                if cut >= 0:
                    mark_len = len(m)
            except Exception:
                cut = -1
            ready = False
            one = None
            if cut >= 0 and mark_len:
                end = cut + mark_len
                if end < len(acc) and acc[end] == 10:
                    end += 1
                one = bytes(acc[:end])
                acc = acc[end:]
                ready = True
            elif acc and (force or uart.idle_done()):
                one = bytes(acc)
                acc = bytearray()
                uart.receiving = False
                ready = True
            if ready and one:
                kind = handle_chunk(one.decode("utf-8", "ignore"))
                if kind == "end" or force:
                    finish_file()
                draw()
            elif force and started:
                uart.receiving = False
                finish_file()
                draw()
        now = time.ticks_ms()
        if time.ticks_diff(now, last_ui) > 400:
            if uart and (not acc) and (not wait_more):
                if uart.connected:
                    if status.startswith("visible"):
                        status = "connected"
                        detail = "Send on phone"
                else:
                    if status == "connected" or status == "receiving...":
                        status = "visible: " + BLE_NAME
                        detail = "connect phone"
            draw()
            last_ui = now
    except MemoryError:
        fail("MEM")
        try:
            draw()
        except Exception:
            pass
        gc.collect()
    except Exception as e:
        fail("%s %s" % (type(e).__name__, e))
        try:
            draw()
        except Exception:
            pass
        gc.collect()
    time.sleep_ms(20)
