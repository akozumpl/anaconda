import Queue
import logging
log = logging.getLogger("anaconda")

DETAILED_MESSAGE_WINDOW = 109
EDIT_REPO_WINDOW = 111
INITIALIZE_DISK_WINDOW = 107
KICKSTART_ERROR_WINDOW = 108
LUKS_WINDOW = 110
MESSAGE_WINDOW = 101
METHODSTR_WINDOW = 112
PASSPHRASE_WINDOW = 102
PROGRESS_PULSE = 103
PROGRESS_WINDOW = 104
UPDATE_PROGRESS = 105
WAIT_WINDOW     = 106

DISPLAY_STEP = 150

DESTROY_WINDOW  = 201

# The global view reference.
_view = None

def bind_view(view):
    global _view
    _view = view

class Status(object):
    class StatusHandle(object):
        def __init__(self, status):
            self.status = status
        pass

    def __init__(self):
        """ Creates the StatusHandle object. """
        self.out_queue = Queue.Queue() # outbound queue
        self.in_queue = Queue.Queue() # inbound queue
        self.status_stack = []

    def _to_dict(self, **kw_dict):
        return kw_dict

    def _update_ui(self):
        # view's in_queue is our out_queue
        if _view:
            _view.update(self.out_queue, self.in_queue)
        else:
            log.debugger("No view object to handle a UI update.")

    # asynchronous interface
    def destroy_window(self, wh):
        self.out_queue.put((DESTROY_WINDOW, wh, None))
        self._update_ui()

    def progress_pulse(self, wh):
        self.out_queue.put((PROGRESS_PULSE, wh, None))
        self._update_ui()

    def progress_window(self, title, text, total, updpct = 0.05, pulse = False):
        handle = self.StatusHandle(self)
        dictionary = {"title" : title,
                      "text" : text,
                      "total" : total,
                      "updpct" : updpct,
                      "pulse" : pulse
                      }
        self.out_queue.put((PROGRESS_WINDOW,
                            handle,
                            dictionary))
        self._update_ui()
        return handle

    def update_progress(self, handle, amount):
        self.out_queue.put((UPDATE_PROGRESS,
                            handle,
                            self._to_dict(amount=amount)))
        self._update_ui()

    def wait_window(self, title, text):
        handle = self.StatusHandle(self)
        self.out_queue.put((WAIT_WINDOW,
                        handle,
                        self._to_dict(title=title, text=text)))
        self._update_ui()
        return handle

    # stack interface
    def announce_kickstart_error_sync(self, text):
        self.out_queue.put((KICKSTART_ERROR_WINDOW,
                            None,
                            self._to_dict(text=text)))
        self._update_ui()
        return self.in_queue.get()

    def display_edit_repo_sync(self ,repo):
        self.out_queue.put((EDIT_REPO_WINDOW,
                            None,
                            self._to_dict(repo=repo)))
        self._update_ui()
        return self.in_queue.get()

    def display_step(self, step):
        # synchronous event
        self.out_queue.put((DISPLAY_STEP,
                            None,
                            self._to_dict(step=step)))
        self._update_ui()
        return self.in_queue.get()

    def i_am_busy(self, reason, details):
        handle = self.StatusHandle(self)
        self.out_queue.put((WAIT_WINDOW,
                        handle,
                        self._to_dict(title=reason, text=details)))
        self.status_stack.append(handle)
        self._update_ui()

    def need_answer_sync(self, title, text, type="ok", default=None,
                       custom_buttons=None, custom_icon=None):
        argdict = self._to_dict(title=title, text=text, type=type,
                                default=default, custom_buttons=custom_buttons,
                                custom_icon=custom_icon)
        self.out_queue.put((MESSAGE_WINDOW, None, argdict))
        self._update_ui()
        return self.in_queue.get()

    def need_answer_long_sync(self, title, text, longText=None, type="ok",
                              default=None, custom_buttons=None, custom_icon=None):
        argdict = self._to_dict(title=title, text=text, longText=longText,
                                type=type, default=default,
                                custom_buttons=custom_buttons,
                                custom_icon=custom_icon)
        self.out_queue.put((DETAILED_MESSAGE_WINDOW, None, argdict))
        self._update_ui()
        return self.in_queue.get()

    def need_initialize_disk_answer_sync(self, path, description, size):
        argdict = self._to_dict(path=path, description=description, size=size)
        self.out_queue.put((INITIALIZE_DISK_WINDOW, None, argdict))
        self._update_ui()
        return self.in_queue.get()

    def need_luks_passphrase_sync(self, passphrase = "", preexist = False):
        argdict = self._to_dict(passphrase=passphrase, preexist=preexist)
        self.out_queue.put((LUKS_WINDOW, None, argdict))
        self._update_ui()
        return self.in_queue.get()

    def need_methodstr_sync(self, methodstr, exception):
        argdict = self._to_dict(methodstr=methodstr, exception=exception)
        self.out_queue.put((METHODSTR_WINDOW, None, argdict))
        self._update_ui()
        return self.in_queue.get()

    def need_passphrase_sync(self, device_name):
        # synchronous event
        self.out_queue.put((PASSPHRASE_WINDOW,
                            None,
                            self._to_dict(device=device_name)))
        self._update_ui()
        return self.in_queue.get()

    def no_longer_busy(self):
        handle = self.status_stack.pop(len(self.status_stack) - 1)
        self.out_queue.put((DESTROY_WINDOW, handle, None))
        self._update_ui()

class View(object):
    def update(self, queue):
        raise NotImplementedError
