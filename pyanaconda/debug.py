from __future__ import print_function

import functools
import os
import threading
import traceback

import logging
log = logging.getLogger("anaconda")

def prlimit_me():
    pid = os.getpid()
    os.system("/tmp/updates/prlimit --pid %d --core=unlimited" % pid)

def trc():
    thread = threading.current_thread()
    print('--- --- trace for: %s, pid: %d' % (thread.name, os.getpid()))
    tb = traceback.extract_stack()
    print_no_newline = functools.partial(print, end='')
    map(print_no_newline, traceback.format_list(tb[:-1]))
    # traceback.print_stack(tb)
    print('--- ---')

def logged(fn):
    def decorated(*args, **kwargs):
        log.error('bef: %s', fn.__name__)
        retval = fn(*args, **kwargs)
        log.error('aft: %s', fn.__name__)
        return retval
    return decorated
