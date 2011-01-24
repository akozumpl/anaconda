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
    class WidgetHandle(object):
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
        wh = self.WidgetHandle()
        self.queue.put((WAIT_WINDOW, wh, self._to_dict(title=title, text=text)))
        self.anaconda.intf.view.update(self.queue)
        return wh

    # stack interface - TODO
    def iam_busy(self):
        self.anaconda.view.update(self.queue)

    def no_longer_busy(self):
        self.anaconda.view.update(self.queue)


class View(object):
    def update(self, queue):
        raise NotImplementedError
