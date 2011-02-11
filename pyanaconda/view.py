import Queue
import logging
log = logging.getLogger("anaconda")

# Status must eventually provide:
# waitWindow
# messageWindow
# progressWindow
# passphraseEntryWindow
# getLuksPassphrase
# detailedMessageWindow

PASSPHRASE_WINDOW = 101
WAIT_WINDOW     = 102

DESTROY_WINDOW  = 201

class Status(object):
    class StatusHandle(object):
        """ Intentionally empty object used only as a dictionary key in
            the interfaces's View.
        """
        pass

    def __init__(self, anaconda):
        self.out_queue = Queue.Queue() # outbound queue
        self.in_queue = Queue.Queue() # inbound queue
        self.anaconda = anaconda
        self.status_stack = []

    def _to_dict(self, **kw_dict):
        return kw_dict

    def _update_ui(self):
        # view's in_queue is our out_queue
        self.anaconda.intf.view.update(self.out_queue, self.in_queue)

    def destroy_window(self, wh):
        self.out_queue.put((DESTROY_WINDOW, wh, None))
        self._update_ui()

    def wait_window(self, title, text):
        handle = self.StatusHandle()
        self.out_queue.put((WAIT_WINDOW,
                        handle,
                        self._to_dict(title=title, text=text)))
        self._update_ui()
        return handle

    # stack interface
    def i_am_busy(self, reason, details):
        handle = self.StatusHandle()
        self.out_queue.put((WAIT_WINDOW,
                        handle,
                        self._to_dict(title=reason, text=details)))
        self.status_stack.append(handle)
        self._update_ui()

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
