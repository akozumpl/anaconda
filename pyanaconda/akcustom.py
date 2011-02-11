import logging
import pyanaconda.view
import time

import iutil

log = logging.getLogger("anaconda")

def akcustomstep(anaconda):
    log.critical("akcustomstep starting.")
    test_gtk_sync(anaconda)

def test_busy():
    status = pyanaconda.view.Status()
    status.i_am_busy("my head is", "full of fuck")
    time.sleep(1.5)
    status.no_longer_busy()

def test_progress():
    status = pyanaconda.view.Status()
    h = status.progress_window("Top", "Text.", 10)
    for i in range(1, 20):
        status.progress_pulse(h)
        time.sleep(float(1)/i)
    status.destroy_window(h)

    h = status.progress_window("Exec", "Text.", 10)
    iutil.execWithPulseProgress('/tmp/updates/ak.py', [], progress = h)
    status.destroy_window(h)

    h = status.progress_window("Exec", "Text.", 10)
    iutil.execWithCallback('/tmp/updates/ak.py', [], callback = update_progress,
                           callback_data = h)
    status.destroy_window(h)

def test_passphrase():
    status = pyanaconda.view.Status()
    rc = status.need_passphrase_sync("/dev/sda2")
    log.critical("got passphrase: %s" % str(rc))

def test_initialize_disk():
    status = pyanaconda.view.Status()
    ret = status.need_initialize_disk_answer_sync(path="/dev/sda",
                                                  description="DISK OIN OIN OIN",
                                                  size=1024*1024)
    log.critical("akcustom: answer was: %s" % ret)

def test_kickstart_error():
    status = pyanaconda.view.Status()
    ret = status.announce_kickstart_error_sync("dooh.. WINNING")
    log.critical("akcustom: answer was: %s" % ret)

def test_answer_long():
    status = pyanaconda.view.Status()
    ret = status.need_answer_long_sync("dooh", "text", "long text")
    log.critical("akcustom: answer was: %s" % ret)

def test_luks_passphrase():
    status = pyanaconda.view.Status()
    ret = status.need_luks_passphrase_sync("passphrase", False)
    log.critical("akcustom: answer was: %s" % str(ret))

def test_methodstr():
    status = pyanaconda.view.Status()
    exception = SystemError("easy easy")
    ret = status.need_methodstr_sync("cdrom:", exception)
    log.critical("akcustom: answer was: %s" % str(ret))

def test_gtk_sync(anaconda):
    import pyanaconda.gui
    ret = pyanaconda.gui.idle_gtk_sync(anaconda.intf.messageWindow, "title",
                                      "text", type = "custom",
                                       custom_buttons = ["oh", "yeah"])
    log.critical("akcustom: answer was: %s" % str(ret))

amount = 0
def update_progress(data, callback_data):
    global amount

    if not data:
        return
    progress_handler = callback_data
    if data == '\n':
        amount += 1
        progress_handler.status.update_progress(progress_handler, amount)
