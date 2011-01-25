import Queue

# Status must eventually provide:
# waitWindow
# messageWindow
# progressWindow
# passphraseEntryWindow
# getLuksPassphrase
# detailedMessageWindow

WAIT_WINDOW=101
DESTROY_WINDOW = 102

class Status(object):
    class StatusHandle(object):
        """ Intentionally empty object used only as a dictionary key in
            the interfaces's View.
        """
        pass

    def __init__(self, anaconda):
        self.queue = Queue.Queue()
        self.anaconda = anaconda
        self.status_stack = []

    def _to_dict(self, **kw_dict):
        return kw_dict

    def destroy_window(self, wh):
        self.queue.put((DESTROY_WINDOW, wh, None))
        self.anaconda.intf.view.update(self.queue)

    def wait_window(self, title, text):
        handle = self.StatusHandle()
        self.queue.put((WAIT_WINDOW,
                        handle,
                        self._to_dict(title=title, text=text)))
        self.anaconda.intf.view.update(self.queue)
        return handle

    # stack interface
    def i_am_busy(self, reason, details):
        handle = self.StatusHandle()
        self.queue.put((WAIT_WINDOW,
                        handle,
                        self._to_dict(title=reason, text=details)))
        self.status_stack.append(handle)
        self.anaconda.intf.view.update(self.queue)

    def no_longer_busy(self):
        handle = self.status_stack.pop(len(self.status_stack) - 1)
        self.queue.put((DESTROY_WINDOW, handle, None))
        self.anaconda.intf.view.update(self.queue)

class View(object):
    def update(self, queue):
        raise NotImplementedError
